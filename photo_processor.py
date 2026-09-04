# -*- coding: utf-8 -*-
"""
证件照处理流水线模块
将抠图、人脸检测、裁剪调整串联为完整流程。
业务逻辑层：不包含任何 UI 代码。
"""
import math
import numpy as np
import cv2

from image_utils import resize_image_esp, get_nontransparent_bounds

# 通用证件照默认构图尺寸 (高, 宽)。比例 1.4，人像居中、头顶留安全边距，
# 便于后期在排版页按任意规格做「覆盖式居中裁切」而不变形。
# 处理页不再绑定具体尺寸，一次处理产出通用照，尺寸在排版页决定。
UNIVERSAL_PHOTO_SIZE = (1400, 1000)


class FaceError(Exception):
    """人脸检测异常（无人脸或多个人脸）。"""
    def __init__(self, message: str, face_count: int):
        super().__init__(message)
        self.face_count = face_count


class PhotoResult:
    """证件照处理结果容器。"""

    def __init__(self, standard: np.ndarray, hd: np.ndarray,
                 matting: np.ndarray, face_info: dict = None):
        self.standard = standard    # 标准照（精确尺寸）
        self.hd = hd                # 高清照
        self.matting = matting      # 抠图结果（透明背景）
        self.face_info = face_info  # 人脸信息


class PhotoProcessor:
    """
    证件照处理器，编排完整的证件照制作流程。
    """

    # 默认人像参数
    DEFAULT_HEAD_MEASURE_RATIO = 0.2   # 人脸面积占全图比例
    DEFAULT_HEAD_HEIGHT_RATIO = 0.45   # 人脸中心在高度方向的比例
    DEFAULT_HEAD_TOP_RANGE = (0.12, 0.1)  # 头顶到顶部的距离范围 (max, min)

    def __init__(self, engine_manager):
        """
        :param engine_manager: EngineManager 实例，提供模型推理能力
        """
        self.engine_manager = engine_manager

    def process_id_photo(
        self,
        input_image: np.ndarray,
        size: tuple = None,
        head_measure_ratio: float = None,
        head_height_ratio: float = None,
        head_top_range: tuple = None,
        face_confidence_threshold: float = None,
        face_alignment: bool = False,
        process_mode: str = "crop_bg",
    ) -> PhotoResult:
        """
        完整证件照制作流程：抠图 → 人脸检测 → 裁剪调整。
        :param input_image: BGR 图像
        :param size: (height, width) 目标尺寸；为 None 时产出「通用证件照」
                     （固定构图 UNIVERSAL_PHOTO_SIZE），不绑定具体规格，
                     后期在排版页按任意尺寸覆盖裁切而不变形。
        :param face_confidence_threshold: 人脸检测置信度阈值
        :param face_alignment: 是否启用「人脸旋转校正」（插件开关，默认关闭）；
                     仅当头部偏转 > 2° 时对原图+抠图一起旋转摆正，再重检人脸。
        :param process_mode: "crop_bg" 裁切换底；"bg_only" 只换底色不裁剪
        :return: PhotoResult
        """
        if input_image is None or input_image.size == 0:
            raise ValueError("输入图像为空")
        # 通用型：未指定尺寸时产出固定构图的通用证件照，尺寸下移到排版页决定
        if size is None:
            size = UNIVERSAL_PHOTO_SIZE
        if not size or len(size) != 2 or size[0] <= 0 or size[1] <= 0:
            raise ValueError(f"无效的证件照尺寸: {size}")

        # 参数默认值处理
        if head_measure_ratio is None:
            head_measure_ratio = self.DEFAULT_HEAD_MEASURE_RATIO
        if head_height_ratio is None:
            head_height_ratio = self.DEFAULT_HEAD_HEIGHT_RATIO
        if head_top_range is None:
            head_top_range = self.DEFAULT_HEAD_TOP_RANGE

        # Step 1: 缩放原图到最大边 2000
        origin_image = resize_image_esp(input_image, max_side=2000)

        # Step 2: 人像抠图
        matting_engine = self.engine_manager.get_matting_engine()
        matting_image = matting_engine.process(origin_image)

        if process_mode == "bg_only":
            # 只换底色：将抠图结果等比缩放后置于目标尺寸画布中央
            standard_photo = self._resize_to_canvas(matting_image, size)
            hd_photo = standard_photo.copy()
            return PhotoResult(
                standard=standard_photo,
                hd=hd_photo,
                matting=matting_image,
                face_info=None,
            )

        # Step 3: 人脸检测（在原图上检测）
        face_detector = self.engine_manager.get_face_detector()
        faces = face_detector.detect(
            origin_image,
            confidence_threshold=face_confidence_threshold,
        )

        if len(faces) != 1:
            raise FaceError(
                f"检测到 {len(faces)} 张人脸，请上传仅包含单张人脸的图像。",
                len(faces),
            )

        face_det = faces[0]
        face_rect = (
            int(face_det[0]),   # x
            int(face_det[1]),   # y
            int(face_det[2] - face_det[0] + 1),  # width
            int(face_det[3] - face_det[1] + 1),  # height
        )
        original_roll = self._calculate_roll_angle(face_det)

        # 人脸旋转校正（插件开关；仅当头部偏转 > 2° 时旋转原图+抠图，再重检）
        # 复刻开源 HivisionIDPhotos：按 -roll_angle 旋转后，warpAffine 实际按
        # +roll_angle 旋转，抵消头部倾斜使照片摆正；旋转后重检以保持裁剪基准正确。
        if face_alignment and abs(original_roll) > 2:
            rot_angle = -1 * original_roll
            # 抠图（BGRA）与 原图（BGR）按相同角度、相同中心旋转，保持人像对齐
            mb, mg, mr, ma = cv2.split(matting_image)
            rgb_rot = self._rotate_bound(cv2.merge((mb, mg, mr)), rot_angle)
            alpha_rot = self._rotate_bound(ma, rot_angle)
            matting_image = cv2.merge(
                (rgb_rot[:, :, 0], rgb_rot[:, :, 1], rgb_rot[:, :, 2], alpha_rot)
            )
            origin_image = self._rotate_bound(origin_image, rot_angle)
            # 旋转后重新检测人脸，更新裁剪基准框
            faces = face_detector.detect(
                origin_image, confidence_threshold=face_confidence_threshold
            )
            if len(faces) != 1:
                raise FaceError(
                    f"旋转校正后检测到 {len(faces)} 张人脸，请重新上传照片。",
                    len(faces),
                )
            face_det = faces[0]
            face_rect = (
                int(face_det[0]), int(face_det[1]),
                int(face_det[2] - face_det[0] + 1),
                int(face_det[3] - face_det[1] + 1),
            )

        # Step 4: 证件照裁剪调整
        standard_photo, hd_photo = self._adjust_and_crop(
            matting_image=matting_image,
            face_rect=face_rect,
            target_size=size,
            head_measure_ratio=head_measure_ratio,
            head_height_ratio=head_height_ratio,
            head_top_range=head_top_range,
        )

        face_info = {
            "rectangle": face_rect,
            "roll_angle": original_roll,
        }

        return PhotoResult(
            standard=standard_photo,
            hd=hd_photo,
            matting=matting_image,
            face_info=face_info,
        )

    def process_matting_only(self, input_image: np.ndarray) -> np.ndarray:
        """仅进行人像抠图，不做裁剪。"""
        if input_image is None or input_image.size == 0:
            raise ValueError("输入图像为空")
        origin_image = resize_image_esp(input_image, max_side=2000)
        matting_engine = self.engine_manager.get_matting_engine()
        return matting_engine.process(origin_image)

    def _adjust_and_crop(
        self,
        matting_image: np.ndarray,
        face_rect: tuple,
        target_size: tuple,
        head_measure_ratio: float,
        head_height_ratio: float,
        head_top_range: tuple,
    ) -> tuple:
        """
        根据人脸位置和标准尺寸，裁剪并调整证件照。
        :return: (standard_photo, hd_photo)
        """
        face_x, face_y, face_w, face_h = face_rect
        standard_h, standard_w = target_size
        width_height_ratio = standard_h / standard_w

        # 计算人脸中心和面积
        face_center_x = face_x + face_w / 2.0
        face_center_y = face_y + face_h / 2.0
        face_area = face_w * face_h

        # 计算裁剪框大小
        crop_area = face_area / head_measure_ratio
        resize_ratio = crop_area / (standard_h * standard_w)
        resize_ratio_single = math.sqrt(resize_ratio)
        crop_h = int(standard_h * resize_ratio_single)
        crop_w = int(standard_w * resize_ratio_single)

        # 裁剪框定位
        x1 = int(face_center_x - crop_w / 2.0)
        y1 = int(face_center_y - crop_h * head_height_ratio)
        x2 = x1 + crop_w
        y2 = y1 + crop_h

        # 第一轮裁剪
        cut_image = self._crop_with_padding(x1, y1, x2, y2, matting_image)
        cut_image = cv2.resize(cut_image, (crop_w, crop_h))

        # 获取人像在裁剪图中的边界
        y_top, y_bottom_gap, x_left, x_right_gap = get_nontransparent_bounds(
            cut_image.astype(np.uint8)
        )

        # 检测左右空隙，计算上下裁剪补偿
        if x_left > 0 or x_right_gap > 0:
            status_left_right = 1
            cut_value_top = int((x_left + x_right_gap) * width_height_ratio / 2)
        else:
            status_left_right = 0
            cut_value_top = 0

        # 检测头顶距离是否合适
        status_top, move_value = self._detect_head_distance(
            y_top - cut_value_top, crop_h, head_top_range[0], head_top_range[1]
        )

        # 第二轮裁剪
        if status_left_right == 0 and status_top == 0:
            result_image = cut_image
        else:
            result_image = self._crop_with_padding(
                x1 + x_left,
                y1 + cut_value_top + status_top * move_value,
                x2 - x_right_gap,
                y2 - cut_value_top + status_top * move_value,
                matting_image,
            )

        # 底部下拉至底边
        result_image = self._move_to_bottom(result_image)

        # 生成标准照和高清照
        standard_photo = self._resize_to_standard(result_image, target_size)
        hd_photo, _ = self._resize_by_min_side(result_image, max(600, standard_w))

        return standard_photo, hd_photo

    @staticmethod
    def _crop_with_padding(x1, y1, x2, y2, image):
        """
        裁剪图像，超出边界的部分用透明像素填充。
        返回 4 通道 BGRA 图像。
        """
        crop_h = y2 - y1
        crop_w = x2 - x1
        if crop_h <= 0 or crop_w <= 0:
            raise ValueError(f"无效的裁剪尺寸: ({crop_w}, {crop_h})")

        # 计算超出边界的部分
        pad_top = 0
        pad_bottom = 0
        pad_left = 0
        pad_right = 0

        src_y1, src_y2 = y1, y2
        src_x1, src_x2 = x1, x2

        if y1 < 0:
            pad_top = abs(y1)
            src_y1 = 0
        if y2 > image.shape[0]:
            pad_bottom = y2 - image.shape[0]
            src_y2 = image.shape[0]
        if x1 < 0:
            pad_left = abs(x1)
            src_x1 = 0
        if x2 > image.shape[1]:
            pad_right = x2 - image.shape[1]
            src_x2 = image.shape[1]

        # 创建透明背景
        background_bgr = np.full((crop_h, crop_w), 255, dtype=np.uint8)
        background_alpha = np.full((crop_h, crop_w), 0, dtype=np.uint8)
        background = cv2.merge((background_bgr, background_bgr, background_bgr, background_alpha))

        # 填充实际图像区域
        src_region = image[src_y1:src_y2, src_x1:src_x2]
        dst_h = crop_h - pad_top - pad_bottom
        dst_w = crop_w - pad_left - pad_right
        if src_region.shape[0] > 0 and src_region.shape[1] > 0:
            # 确保目标区域大小匹配
            actual_h = min(dst_h, src_region.shape[0])
            actual_w = min(dst_w, src_region.shape[1])
            background[pad_top:pad_top + actual_h, pad_left:pad_left + actual_w] = \
                src_region[:actual_h, :actual_w]

        return background

    @staticmethod
    def _detect_head_distance(value, crop_height, max_ratio, min_ratio):
        """
        检测头顶与照片顶部的距离是否在适当范围内。
        :return: (status, move_value)
                  status=0: 合适，不动
                  status=1: 距离过大，人像上移
                  status=-1: 距离过小，人像下移
        """
        ratio = value / crop_height
        if min_ratio <= ratio <= max_ratio:
            return 0, 0
        elif ratio > max_ratio:
            move_value = int((ratio - max_ratio) * crop_height)
            return 1, move_value
        else:
            move_value = int((min_ratio - ratio) * crop_height)
            return -1, move_value

    @staticmethod
    def _move_to_bottom(image_bgra: np.ndarray) -> np.ndarray:
        """将人像下拉至图像底部，消除底部空隙。"""
        y_top, y_bottom_gap, _, _ = get_nontransparent_bounds(image_bgra)
        if y_bottom_gap <= 0:
            return image_bgra
        height, width, channels = image_bgra.shape
        # 创建顶部空白条
        top_pad = np.zeros((y_bottom_gap, width, channels), dtype=np.uint8)
        # 移除底部空白，在顶部添加
        result = image_bgra[:height - y_bottom_gap, :, :]
        result = np.concatenate((top_pad, result), axis=0)
        return result

    @staticmethod
    def _resize_to_canvas(image_bgra: np.ndarray, size: tuple) -> np.ndarray:
        """将 BGRA 图像等比缩放并居中放置到目标尺寸画布上。"""
        target_h, target_w = size
        h, w = image_bgra.shape[:2]
        ratio = min(target_w / w, target_h / h, 1.0)
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        resized = cv2.resize(image_bgra, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # 创建透明画布
        canvas = np.zeros((target_h, target_w, 4), dtype=np.uint8)
        y = (target_h - new_h) // 2
        x = (target_w - new_w) // 2
        canvas[y:y + new_h, x:x + new_w] = resized
        return canvas

    @staticmethod
    def _resize_to_standard(image: np.ndarray, size: tuple) -> np.ndarray:
        """将图像缩放到标准尺寸，使用多级缩放保证质量。

        大图先逐级减半（高度降到 2 倍目标以内），再一次性精确缩放到目标，
        避免单次大幅降采样产生明显锯齿；最终尺寸恒等于目标尺寸。
        """
        target_h, target_w = size
        result = image
        while result.shape[0] >= 2 * target_h:
            half_h = max(target_h, result.shape[0] // 2)
            half_w = max(1, int(round(result.shape[1] * half_h / result.shape[0])))
            result = cv2.resize(result, (half_w, half_h),
                                interpolation=cv2.INTER_AREA)
        if result.shape[0] != target_h or result.shape[1] != target_w:
            result = cv2.resize(result, (target_w, target_h),
                                interpolation=cv2.INTER_AREA)
        return result

    @staticmethod
    def _resize_by_min_side(image: np.ndarray, min_side: int) -> tuple:
        """将图像最短边缩放到至少 min_side，返回 (缩放后图像, 缩放比例)。"""
        height, width = image.shape[:2]
        min_dim = min(height, width)
        if min_dim < min_side:
            if height >= width:
                new_width = min_side
                new_height = int(height * min_side / width)
            else:
                new_height = min_side
                new_width = int(width * min_side / height)
            return cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA), \
                   new_height / height
        return image, 1.0

    @staticmethod
    def _calculate_roll_angle(face_det) -> float:
        """根据人脸关键点计算头部偏转角度。"""
        left_eye = np.array([face_det[5], face_det[6]])
        right_eye = np.array([face_det[7], face_det[8]])
        dy = right_eye[1] - left_eye[1]
        dx = right_eye[0] - left_eye[0]
        return float(np.degrees(np.arctan2(dy, dx)))

    @staticmethod
    def _rotate_bound(image: np.ndarray, angle: float, center=None) -> np.ndarray:
        """
        旋转图像且边界不裁切（复刻开源 rotation_adjust.rotate_bound）。

        内部对角度取负：调用方传入 ``-roll_angle`` 时，warpAffine 实际按
        ``+roll_angle`` 旋转，恰好抵消头部倾斜、使照片摆正。支持 2/3/4 通道。
        """
        (h, w) = image.shape[:2]
        if center is None:
            (cX, cY) = (w / 2, h / 2)
        else:
            (cX, cY) = center
        M = cv2.getRotationMatrix2D((cX, cY), -angle, 1.0)
        cos = np.abs(M[0, 0])
        sin = np.abs(M[0, 1])
        nW = int((h * sin) + (w * cos))
        nH = int((h * cos) + (w * sin))
        M[0, 2] += (nW / 2) - cX
        M[1, 2] += (nH / 2) - cY
        return cv2.warpAffine(image, M, (nW, nH))
