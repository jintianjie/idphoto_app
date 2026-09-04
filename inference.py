# -*- coding: utf-8 -*-
"""
模型推理引擎模块
封装人像抠图 (多模型可选) 和人脸检测 (RetinaFace) 的 ONNX 推理逻辑。
模型采用懒加载策略：首次调用时加载，后续复用，保证启动速度。
"""
import os
import math
import numpy as np
import cv2
from itertools import product as product
from PIL import Image

# onnxruntime 不在顶层导入：它初始化开销大（约 40ms），而本模块的模型
# 本就是懒加载（首次推理才建 Session）。推迟到真正建 Session 时再导入，
# 可省掉这段启动耗时。行为完全等价——顶层导入后，唯一用武之地
# 也只有后面两处 InferenceSession 调用。
_ORT = None


def _get_ort():
    """按需导入 onnxruntime 并缓存到模块级变量 _ORT。"""
    global _ORT
    if _ORT is None:
        import onnxruntime
        _ORT = onnxruntime
    return _ORT


# ============================================================
#  RetinaFace 辅助函数（从原项目移植，纯 numpy 实现）
# ============================================================

def _decode_boxes(loc, priors, variances):
    """解码边界框预测。"""
    boxes = np.concatenate((
        priors[:, :2] + loc[:, :2] * variances[0] * priors[:, 2:],
        priors[:, 2:] * np.exp(loc[:, 2:] * variances[1]),
    ), axis=1)
    boxes[:, :2] -= boxes[:, 2:] / 2
    boxes[:, 2:] += boxes[:, :2]
    return boxes


def _decode_landmarks(pre, priors, variances):
    """解码人脸关键点预测。"""
    landms = np.concatenate((
        priors[:, :2] + pre[:, :2] * variances[0] * priors[:, 2:],
        priors[:, :2] + pre[:, 2:4] * variances[0] * priors[:, 2:],
        priors[:, :2] + pre[:, 4:6] * variances[0] * priors[:, 2:],
        priors[:, :2] + pre[:, 6:8] * variances[0] * priors[:, 2:],
        priors[:, :2] + pre[:, 8:10] * variances[0] * priors[:, 2:],
    ), axis=1)
    return landms


def _nms(dets, threshold):
    """非极大值抑制 (NMS)。"""
    x1 = dets[:, 0]
    y1 = dets[:, 1]
    x2 = dets[:, 2]
    y2 = dets[:, 3]
    scores = dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(ovr <= threshold)[0]
        order = order[inds + 1]
    return keep


class _PriorBox:
    """RetinaFace 先验框生成器。"""

    RETINAFACE_CFG = {
        "min_sizes": [[16, 32], [64, 128], [256, 512]],
        "steps": [8, 16, 32],
        "variance": [0.1, 0.2],
        "clip": False,
    }

    def __init__(self, image_size):
        self.image_size = image_size
        self.min_sizes = self.RETINAFACE_CFG["min_sizes"]
        self.steps = self.RETINAFACE_CFG["steps"]
        self.clip = self.RETINAFACE_CFG["clip"]
        self.feature_maps = [
            [math.ceil(image_size[0] / step), math.ceil(image_size[1] / step)]
            for step in self.steps
        ]

    def forward(self):
        anchors = []
        for k, f in enumerate(self.feature_maps):
            min_sizes = self.min_sizes[k]
            for i, j in product(range(f[0]), range(f[1])):
                for min_size in min_sizes:
                    s_kx = min_size / self.image_size[1]
                    s_ky = min_size / self.image_size[0]
                    cx = [x * self.steps[k] / self.image_size[1] for x in [j + 0.5]]
                    cy = [y * self.steps[k] / self.image_size[0] for y in [i + 0.5]]
                    for c_y, c_x in product(cy, cx):
                        anchors += [c_x, c_y, s_kx, s_ky]
        output = np.array(anchors).reshape(-1, 4)
        if self.clip:
            output = np.clip(output, 0, 1)
        return output


# ============================================================
#  人像抠图引擎（多模型支持）
# ============================================================

class MattingEngine:
    """
    人像抠图引擎，支持多模型切换。
    默认使用 modnet_photographic_portrait_matting.onnx。
    懒加载：首次调用 process() 时加载模型。
    """

    def __init__(self, model_path: str, model_name: str = "modnet_photographic_portrait_matting"):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"抠图模型文件不存在: {model_path}")
        self.model_path = model_path
        self.model_name = model_name
        self._session = None

    def _ensure_loaded(self):
        """懒加载 ONNX 模型会话（onnxruntime 于此处按需导入）。"""
        if self._session is None:
            self._session = _get_ort().InferenceSession(
                self.model_path, providers=["CPUExecutionProvider"]
            )

    def process(self, input_image_bgr: np.ndarray) -> np.ndarray:
        """
        对输入的 BGR 图像进行人像抠图。
        :return: 4 通道 BGRA 图像，背景透明
        """
        if input_image_bgr is None or input_image_bgr.size == 0:
            raise ValueError("输入图像为空")

        self._ensure_loaded()
        input_name = self._session.get_inputs()[0].name
        output_name = self._session.get_outputs()[0].name

        orig_h, orig_w = input_image_bgr.shape[:2]

        # 按模型类型分发预处理
        # modnet 与 hivision_modnet 预处理完全一致（同为 512x512 / BGR / mean=0.5,std=0.5），
        # 合并为同一分支，消除重复代码；后处理同理（见下方 in 判断）。
        if self.model_name in ("modnet_photographic_portrait_matting", "hivision_modnet"):
            tensor = self._preprocess_modnet(input_image_bgr, size=512)
        elif self.model_name == "rmbg-1.4":
            tensor = self._preprocess_rmbg(input_image_bgr, size=1024)
        elif self.model_name == "birefnet-v1-lite":
            tensor = self._preprocess_birefnet(input_image_bgr, size=1024)
        else:
            # 默认按 modnet 处理
            tensor = self._preprocess_modnet(input_image_bgr, size=512)

        # 推理
        output = self._session.run([output_name], {input_name: tensor})[0]

        # 按模型类型分发后处理
        if self.model_name in ("modnet_photographic_portrait_matting", "hivision_modnet"):
            mask = self._postprocess_modnet(output, (orig_w, orig_h))
        elif self.model_name == "rmbg-1.4":
            mask = self._postprocess_rmbg(output, (orig_w, orig_h))
        elif self.model_name == "birefnet-v1-lite":
            mask = self._postprocess_birefnet(output, (orig_w, orig_h))
        else:
            mask = self._postprocess_modnet(output, (orig_w, orig_h))

        # 针对 hivision_modnet 做 hollow_out 修复
        if self.model_name == "hivision_modnet":
            bgr = input_image_bgr.copy()
            bgr = self._ensure_3ch(bgr)
            bgra = cv2.merge((bgr, mask))
            bgra = self._hollow_out_fix(bgra)
            return bgra

        bgr = self._ensure_3ch(input_image_bgr)
        return cv2.merge((bgr, mask))

    # --------------------------------------------------
    #  MODNet 系列预处理/后处理
    # --------------------------------------------------

    @staticmethod
    def _preprocess_modnet(image_bgr: np.ndarray, size: int) -> np.ndarray:
        """MODNet 预处理：512x512，BGR，mean=0.5，std=0.5。"""
        image = MattingEngine._ensure_3ch(image_bgr)
        resized = cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)
        normalized = resized.astype(np.float32) / 255.0
        normalized = (normalized - 0.5) / 0.5
        tensor = normalized.transpose(2, 0, 1)[np.newaxis, :]
        return tensor

    @staticmethod
    def _postprocess_modnet(output: np.ndarray, target_size: tuple) -> np.ndarray:
        """MODNet 后处理：缩放回原始尺寸并返回单通道 mask。"""
        matte = (output * 255).astype(np.uint8)
        matte = np.squeeze(matte)
        return cv2.resize(matte, target_size, interpolation=cv2.INTER_AREA)

    # --------------------------------------------------
    #  RMBG-1.4 预处理/后处理
    # --------------------------------------------------

    @staticmethod
    def _preprocess_rmbg(image_bgr: np.ndarray, size: int) -> np.ndarray:
        """RMBG-1.4 预处理：1024x1024，RGB，/255，(x-0.5)/0.5。"""
        image = MattingEngine._ensure_3ch(image_bgr)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        pil_img = pil_img.resize((size, size), Image.BILINEAR)
        arr = np.array(pil_img).astype(np.float32)
        arr = arr.transpose(2, 0, 1)[np.newaxis, :]
        arr = arr / 255.0
        arr = (arr - 0.5) / 0.5
        return arr

    @staticmethod
    def _postprocess_rmbg(output: np.ndarray, target_size: tuple) -> np.ndarray:
        """RMBG-1.4 后处理：min-max 归一化。"""
        result = np.squeeze(output)
        ma = np.max(result)
        mi = np.min(result)
        if ma - mi > 1e-6:
            result = (result - mi) / (ma - mi)
        mask = (result * 255).astype(np.uint8)
        mask_pil = Image.fromarray(mask, mode="L")
        mask_pil = mask_pil.resize(target_size, Image.BILINEAR)
        return np.array(mask_pil)

    # --------------------------------------------------
    #  BiRefNet-v1-lite 预处理/后处理
    # --------------------------------------------------

    @staticmethod
    def _preprocess_birefnet(image_bgr: np.ndarray, size: int) -> np.ndarray:
        """BiRefNet 预处理：1024x1024，RGB，ImageNet 归一化。"""
        image = MattingEngine._ensure_3ch(image_bgr)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        pil_img = pil_img.resize((size, size), Image.BILINEAR)
        arr = np.array(pil_img, dtype=np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406])
        std = np.array([0.229, 0.224, 0.225])
        arr = (arr - mean) / std
        arr = arr.transpose(2, 0, 1)[np.newaxis, :]
        return arr.astype(np.float32)

    @staticmethod
    def _postprocess_birefnet(output: np.ndarray, target_size: tuple) -> np.ndarray:
        """BiRefNet 后处理：Sigmoid + resize。"""
        result = np.squeeze(output)
        result = 1.0 / (1.0 + np.exp(-result))
        mask = (result * 255).astype(np.uint8)
        mask_pil = Image.fromarray(mask, mode="L")
        mask_pil = mask_pil.resize(target_size, Image.BILINEAR)
        return np.array(mask_pil)

    # --------------------------------------------------
    #  通用辅助
    # --------------------------------------------------

    @staticmethod
    def _ensure_3ch(image: np.ndarray) -> np.ndarray:
        """确保图像为 3 通道 BGR。"""
        if len(image.shape) == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if image.shape[2] == 4:
            return image[:, :, :3]
        return image

    @staticmethod
    def _hollow_out_fix(src: np.ndarray) -> np.ndarray:
        """
        修补抠图区域，作为 hivision_modnet 精度不足的补充。
        从原项目 human_matting.py 移植。
        """
        b, g, r, a = cv2.split(src)
        src_bgr = cv2.merge((b, g, r))
        # padding
        add_area = np.zeros((10, a.shape[1]), np.uint8)
        a = np.vstack((add_area, a, add_area))
        add_area = np.zeros((a.shape[0], 10), np.uint8)
        a = np.hstack((add_area, a, add_area))
        # threshold + erode + contour
        _, a_threshold = cv2.threshold(a, 127, 255, 0)
        a_erode = cv2.erode(
            a_threshold,
            kernel=cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
            iterations=3,
        )
        contours, _ = cv2.findContours(
            a_erode, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
        )
        if not contours:
            a = a[10:-10, 10:-10]
            return cv2.merge((src_bgr, a))
        contours = sorted(contours, key=cv2.contourArea, reverse=True)
        a_contour = cv2.drawContours(
            np.zeros(a.shape, np.uint8), contours[0], -1, 255, 2
        )
        h, w = a.shape[:2]
        mask = np.zeros([h + 2, w + 2], np.uint8)
        cv2.floodFill(a_contour, mask=mask, seedPoint=(0, 0), newVal=255)
        a = cv2.add(a, 255 - a_contour)
        return cv2.merge((src_bgr, a[10:-10, 10:-10]))


# ============================================================
#  人脸检测引擎 (RetinaFace)
# ============================================================

class FaceDetector:
    """
    人脸检测引擎，基于 retinaface-resnet50.onnx。
    懒加载：首次调用 detect() 时加载模型。
    """

    CONFIDENCE_THRESHOLD = 0.8
    NMS_THRESHOLD = 0.2
    TOP_K = 5000
    KEEP_TOP_K = 750

    def __init__(self, model_path: str):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"人脸检测模型文件不存在: {model_path}")
        self.model_path = model_path
        self._session = None

    def _ensure_loaded(self):
        """懒加载 ONNX 模型会话（onnxruntime 于此处按需导入）。"""
        if self._session is None:
            self._session = _get_ort().InferenceSession(
                self.model_path, providers=["CPUExecutionProvider"]
            )

    def detect(self, image_bgr: np.ndarray, confidence_threshold: float = None) -> list:
        """
        检测图像中的人脸。
        :param confidence_threshold: 可选覆盖默认置信度阈值
        :return: 检测到的人脸列表，每个元素为
                 [x1, y1, x2, y2, score, lm_x1, lm_y1, ..., lm_x5, lm_y5]
        """
        if image_bgr is None or image_bgr.size == 0:
            raise ValueError("输入图像为空")

        threshold = confidence_threshold if confidence_threshold is not None else self.CONFIDENCE_THRESHOLD

        self._ensure_loaded()

        img_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        img = np.float32(img_rgb)
        im_height, im_width = img.shape[:2]

        # 预处理
        scale = np.array([im_width, im_height, im_width, im_height])
        img -= (104.0, 117.0, 123.0)
        img = img.transpose(2, 0, 1)[np.newaxis, :]

        # 推理
        loc, conf, landms = self._session.run(None, {"input": img})

        # 生成先验框并解码
        cfg = _PriorBox.RETINAFACE_CFG
        priorbox = _PriorBox(image_size=(im_height, im_width))
        priors = priorbox.forward()

        boxes = _decode_boxes(np.squeeze(loc, axis=0), priors, cfg["variance"])
        boxes = boxes * scale
        scores = np.squeeze(conf, axis=0)[:, 1]
        landms = _decode_landmarks(np.squeeze(landms, axis=0), priors, cfg["variance"])

        landm_scale = np.array([
            im_width, im_height, im_width, im_height,
            im_width, im_height, im_width, im_height,
            im_width, im_height,
        ])
        landms = landms * landm_scale

        # 过滤低置信度
        inds = np.where(scores > threshold)[0]
        boxes = boxes[inds]
        landms = landms[inds]
        scores = scores[inds]

        # 按分数排序，取 top_k
        order = scores.argsort()[::-1][:self.TOP_K]
        boxes = boxes[order]
        landms = landms[order]
        scores = scores[order]

        # NMS
        dets = np.hstack((boxes, scores[:, np.newaxis])).astype(np.float32)
        keep = _nms(dets, self.NMS_THRESHOLD)
        dets = dets[keep, :]
        landms = landms[keep]

        dets = dets[:self.KEEP_TOP_K, :]
        landms = landms[:self.KEEP_TOP_K, :]

        # 拼接检测结果
        dets = np.concatenate((dets, landms), axis=1)
        return dets.tolist()


# ============================================================
#  人脸检测引擎 (MTCNN)
# ============================================================

class MTCNNFaceDetector:
    """
    人脸检测引擎，基于 mtcnnruntime（MTCNN 级联网络）。
    离线可用，但首次导入 mtcnnruntime 时会下载一次权重（约数 MB）。
    输出格式与 FaceDetector 保持一致：
        [x1, y1, x2, y2, score, lm0x, lm0y, ..., lm4x, lm4y]（共 15 项）
    """

    CONFIDENCE_THRESHOLD = 0.8
    _SCALE = 2  # 先在小图上检测以提速，与上游 HivisionIDPhotos 一致

    def __init__(self, model_dir: str = None):
        self._detector = None

    def _ensure_loaded(self):
        if self._detector is None:
            try:
                from mtcnnruntime import MTCNN
            except ImportError:
                raise ImportError(
                    "使用 MTCNN 需先安装依赖：pip install mtcnnruntime"
                )
            self._detector = MTCNN()

    def detect(self, image_bgr: np.ndarray, confidence_threshold: float = None) -> list:
        """
        检测图像中的人脸。
        :param confidence_threshold: 可选覆盖默认置信度阈值
        :return: 检测到的人脸列表，每个元素为
                 [x1, y1, x2, y2, score, lm_x1, lm_y1, ..., lm_x5, lm_y5]
        """
        if image_bgr is None or image_bgr.size == 0:
            raise ValueError("输入图像为空")

        thr = confidence_threshold if confidence_threshold is not None else self.CONFIDENCE_THRESHOLD

        self._ensure_loaded()

        # mtcnnruntime 接收 RGB
        img_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        h, w = img_rgb.shape[:2]
        small = cv2.resize(
            img_rgb, (w // self._SCALE, h // self._SCALE), interpolation=cv2.INTER_AREA
        )

        # 该库在无人脸时会因 np.vstack([]) 抛 ValueError，需防御
        try:
            faces, landmarks = self._detector.detect(small, thresholds=[thr, thr, thr])
        except ValueError:
            faces, landmarks = [], []

        if len(faces) == 1:
            # 小图命中单人：坐标映射回原图
            coord_scale = self._SCALE
        else:
            # 兜底：原图再检测一次（与上游一致）
            try:
                faces, landmarks = self._detector.detect(img_rgb, thresholds=[thr, thr, thr])
            except ValueError:
                faces, landmarks = [], []
            coord_scale = 1

        dets = []
        for i, f in enumerate(faces):
            x1, y1, x2, y2, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4])
            lm = np.asarray(landmarks[i]).flatten()
            lm_list = [float(v) for v in lm[:10]]
            det = [x1 * coord_scale, y1 * coord_scale, x2 * coord_scale, y2 * coord_scale, score] + lm_list
            dets.append(det)
        return dets


# ============================================================
#  引擎管理器（单例，统一管理模型生命周期）
# ============================================================

class EngineManager:
    """
    统一管理抠图和人脸检测引擎的生命周期。
    支持切换抠图模型与人脸检测模型。
    """

    SUPPORTED_MODELS = [
        "modnet_photographic_portrait_matting",
        "hivision_modnet",
        "rmbg-1.4",
        "birefnet-v1-lite",
    ]

    SUPPORTED_FACE_MODELS = ["retinaface-resnet50", "mtcnn"]

    def __init__(self, model_dir: str, face_model_path: str = None, face_model_name: str = "retinaface-resnet50"):
        self._model_dir = model_dir
        if face_model_path is None:
            face_model_path = os.path.join(model_dir, "retinaface-resnet50.onnx")

        self._matting_model_name = "modnet_photographic_portrait_matting"
        self._face_model_name = face_model_name
        self._face_model_path = face_model_path
        self._matting_engine = None
        self._face_detector = None

    @property
    def matting_model_name(self):
        """当前抠图模型名（只读）。"""
        return self._matting_model_name

    @property
    def face_model_name(self):
        """当前人脸检测模型名（只读）。"""
        return self._face_model_name

    def set_matting_model(self, model_name: str):
        """切换抠图模型（下次 get_matting_engine 时生效）。"""
        if model_name not in self.SUPPORTED_MODELS:
            raise ValueError(f"不支持的模型: {model_name}")
        # 切换前显式释放旧的 ONNX 会话，避免反复切换时累积占用原生内存
        if self._matting_engine is not None:
            self._matting_engine._session = None
        self._matting_engine = None
        self._matting_model_name = model_name

    def set_face_model(self, model_name: str):
        """切换人脸检测模型（下次 get_face_detector 时生效）。"""
        if model_name not in self.SUPPORTED_FACE_MODELS:
            raise ValueError(f"不支持的人脸检测模型: {model_name}")
        # 切换时释放旧检测器，避免 RetinaFace 与 MTCNN 同时存在占用内存
        self._face_detector = None
        self._face_model_name = model_name

    def get_matting_engine(self) -> MattingEngine:
        """获取抠图引擎实例（懒加载）。"""
        if self._matting_engine is None:
            model_path = os.path.join(self._model_dir, f"{self._matting_model_name}.onnx")
            self._matting_engine = MattingEngine(model_path, self._matting_model_name)
        return self._matting_engine

    def get_face_detector(self):
        """获取人脸检测引擎实例（懒加载，按所选模型名构建）。"""
        if self._face_detector is None:
            if self._face_model_name == "mtcnn":
                self._face_detector = MTCNNFaceDetector()
            else:
                self._face_detector = FaceDetector(self._face_model_path)
        return self._face_detector

    def preload(self, callback=None):
        """预加载所有模型。可在后台线程调用以加速首次处理。"""
        self.get_matting_engine()._ensure_loaded()
        if callback:
            callback("抠图模型加载完成")
        self.get_face_detector()._ensure_loaded()
        if callback:
            callback("人脸检测模型加载完成")
