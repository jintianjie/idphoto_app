# -*- coding: utf-8 -*-
"""
图像处理工具函数模块
负责图像格式转换、缩放、裁剪、美颜、水印等底层操作，不涉及业务逻辑。
"""
import cv2
import numpy as np
from PIL import Image
import io

from color_utils import hex_to_rgb_tuple

# 渐变背景的终止色（白色），与起始色做线性混合
GRADIENT_END_COLOR = (255.0, 255.0, 255.0)


def resize_image_esp(input_image: np.ndarray, max_side: int = 2000) -> np.ndarray:
    """
    将图像最长边缩放到 max_side，保持宽高比不变。
    若原图最长边已小于 max_side，则原样返回。
    """
    if input_image is None or input_image.size == 0:
        raise ValueError("输入图像为空")
    height = input_image.shape[0]
    width = input_image.shape[1]
    max_dim = max(height, width)
    if max_dim <= max_side:
        return input_image
    if height == max_dim:
        new_width = int((max_side / height) * width)
        new_height = max_side
    else:
        new_height = int((max_side / width) * height)
        new_width = max_side
    return cv2.resize(input_image, (new_width, new_height), interpolation=cv2.INTER_AREA)


def image_to_bgr(input_image: np.ndarray) -> np.ndarray:
    """将任意通道数的图像转为 3 通道 BGR。"""
    if input_image is None:
        raise ValueError("输入图像为空")
    if len(input_image.shape) == 2:
        return cv2.cvtColor(input_image, cv2.COLOR_GRAY2BGR)
    if input_image.shape[2] == 4:
        return input_image[:, :, 0:3]
    return input_image




def add_background_to_image(
    matting_image: np.ndarray,
    bgr_color: tuple,
    render_mode: int = 0,
) -> np.ndarray:
    """
    为透明背景的 BGRA 图像添加背景色。
    :param matting_image: 4 通道 BGRA 图像
    :param bgr_color: (B, G, R) 元组
    :param render_mode: 0=纯色, 1=上下渐变, 2=中心渐变
    :return: 3 通道 BGR 图像
    """
    if matting_image is None or matting_image.size == 0:
        raise ValueError("输入图像为空")
    if len(matting_image.shape) != 3 or matting_image.shape[2] != 4:
        raise ValueError("输入图像必须是 4 通道 BGRA 图像")

    height, width = matting_image.shape[:2]
    b, g, r, a = cv2.split(matting_image)
    alpha = a.astype(np.float32) / 255.0

    if render_mode == 0:
        # 纯色背景
        bg_b = np.full((height, width), bgr_color[0], dtype=np.float32)
        bg_g = np.full((height, width), bgr_color[1], dtype=np.float32)
        bg_r = np.full((height, width), bgr_color[2], dtype=np.float32)
    elif render_mode == 1:
        # 上下渐变：从指定色渐变到白色
        bg_b, bg_g, bg_r = _generate_gradient(bgr_color, width, height, mode="updown")
    else:
        # 中心渐变
        bg_b, bg_g, bg_r = _generate_gradient(bgr_color, width, height, mode="center")

    # Alpha 混合: result = foreground * alpha + background * (1 - alpha)
    out_b = b.astype(np.float32) * alpha + bg_b * (1.0 - alpha)
    out_g = g.astype(np.float32) * alpha + bg_g * (1.0 - alpha)
    out_r = r.astype(np.float32) * alpha + bg_r * (1.0 - alpha)

    result = cv2.merge((
        np.clip(out_b, 0, 255).astype(np.uint8),
        np.clip(out_g, 0, 255).astype(np.uint8),
        np.clip(out_r, 0, 255).astype(np.uint8),
    ))
    return result


def _generate_gradient(start_bgr: tuple, width: int, height: int, mode: str = "updown"):
    """生成渐变背景的三个通道。返回 (B, G, R) 各通道的 float32 矩阵。"""
    end_color = GRADIENT_END_COLOR  # 白色

    if mode == "updown":
        # 上下渐变
        b_channel = np.zeros((height, width), dtype=np.float32)
        g_channel = np.zeros((height, width), dtype=np.float32)
        r_channel = np.zeros((height, width), dtype=np.float32)
        for y in range(height):
            ratio = y / height
            b_channel[y, :] = start_bgr[0] * (1 - ratio) + end_color[0] * ratio
            g_channel[y, :] = start_bgr[1] * (1 - ratio) + end_color[1] * ratio
            r_channel[y, :] = start_bgr[2] * (1 - ratio) + end_color[2] * ratio
        return b_channel, g_channel, r_channel
    else:
        # 中心渐变（椭圆）
        canvas = np.zeros((height, width, 3), dtype=np.float32)
        center = (width // 2, height // 2)
        max_axis = max(height, width)
        for radius in range(max_axis, 0, -1):
            ratio = (max_axis - radius) / max_axis
            b_val = start_bgr[0] * (1 - ratio) + end_color[0] * ratio
            g_val = start_bgr[1] * (1 - ratio) + end_color[1] * ratio
            r_val = start_bgr[2] * (1 - ratio) + end_color[2] * ratio
            cv2.ellipse(canvas, center, (radius, radius), 0, 0, 360, (b_val, g_val, r_val), -1)
        b_ch, g_ch, r_ch = cv2.split(canvas)
        return b_ch, g_ch, r_ch


# ============================================================
#  美颜处理
# ============================================================

def apply_beauty(image_bgr: np.ndarray, params: dict) -> np.ndarray:
    """
    应用美颜参数。
    params: {
        whiten: int 0-15,
        brightness: int -5~25,
        contrast: int -10~50,
        saturation: int -10~50,
        sharpen: int 0~5
    }
    """
    if image_bgr is None or image_bgr.size == 0:
        return image_bgr
    if len(image_bgr.shape) != 3 or image_bgr.shape[2] != 3:
        return image_bgr

    result = image_bgr.astype(np.float32)

    # 亮度
    brightness = int(params.get("brightness", 0))
    if brightness != 0:
        result = np.clip(result + brightness * 2.5, 0, 255)

    # 对比度
    contrast = int(params.get("contrast", 0))
    if contrast != 0:
        factor = 1.0 + contrast / 50.0
        result = np.clip((result - 128.0) * factor + 128.0, 0, 255)

    # 饱和度
    saturation = int(params.get("saturation", 0))
    if saturation != 0:
        hsv = cv2.cvtColor(result.astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
        factor = 1.0 + saturation / 50.0
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * factor, 0, 255)
        result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)

    # 美白（gamma + 轻微高斯提亮）
    whiten = int(params.get("whiten", 0))
    if whiten > 0:
        gamma = 1.0 - whiten * 0.04
        gamma = max(0.4, gamma)
        result = np.clip(np.power(result / 255.0, gamma) * 255.0, 0, 255)

    result = result.astype(np.uint8)

    # 锐化
    sharpen = int(params.get("sharpen", 0))
    if sharpen > 0:
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        for _ in range(sharpen):
            result = cv2.filter2D(result, -1, kernel)

    return result


# ============================================================
#  水印处理
# ============================================================

def apply_watermark(image_bgr: np.ndarray, params: dict) -> np.ndarray:
    """
    给 BGR 图像添加文字水印。
    params: {
        enable: bool,
        text: str,
        color: hex str,
        size: int,
        alpha: float 0-1,
        angle: int,
        spacing: int
    }
    """
    if not params.get("enable", False):
        return image_bgr
    if image_bgr is None or image_bgr.size == 0:
        return image_bgr

    text = params.get("text", "")
    if not text:
        return image_bgr

    # 复制一份再绘制：原实现就地修改入参数组，若调用方仍持有该引用会被意外污染。
    # 此处改为在副本上绘制，返回内容与旧版完全一致，仅不再副作用入参。
    image_bgr = image_bgr.copy()

    height, width = image_bgr.shape[:2]
    color_hex = params.get("color", "#FFFFFF")
    rgb = hex_to_rgb_tuple(color_hex)
    bgr = (rgb[2], rgb[1], rgb[0])

    font_size = int(params.get("size", 20))
    alpha = float(params.get("alpha", 0.15))
    alpha = max(0.0, min(1.0, alpha))
    angle = int(params.get("angle", 30))
    spacing = int(params.get("spacing", 25))

    # 创建透明图层
    overlay = np.zeros((height, width, 4), dtype=np.uint8)

    # OpenCV 不支持中文，用 PIL 绘制
    from PIL import Image, ImageDraw, ImageFont
    pil_overlay = Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGRA2RGBA))
    draw = ImageDraw.Draw(pil_overlay)

    try:
        font = ImageFont.truetype("msyh.ttc", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("Microsoft YaHei.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    # 估算文字尺寸
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # 按角度步进铺设水印
    rad = np.radians(angle)
    cos_val = np.cos(rad)
    tan_val = np.tan(rad)
    # 退化角度保护：角度接近 90/270 度时 cos 趋近 0、tan 趋于无穷，
    # 会导致内循环步长退化为 1（极密）且 col*tan 溢出。统一降级为水平铺排。
    if not np.isfinite(tan_val) or abs(cos_val) < 1e-6:
        tan_val = 0.0
        step_x = max(1, int(text_w + spacing))
    else:
        step_x = int((text_w + spacing) * cos_val)
        if step_x == 0:
            step_x = 1 if cos_val >= 0 else -1
    step_y = int((text_w + spacing) * np.sin(rad))
    row_step_y = text_h + spacing

    # 对角线铺设多行
    for row in range(-height, height * 2, max(1, row_step_y)):
        for col in range(-width, width * 2, abs(step_x) + 1):
            x = col
            y = row + int(col * tan_val)
            draw.text((x, y), text, font=font, fill=(rgb[0], rgb[1], rgb[2], int(255 * alpha)))

    overlay = cv2.cvtColor(np.array(pil_overlay), cv2.COLOR_RGBA2BGRA)

    # 混合到原图
    alpha_mask = overlay[:, :, 3].astype(np.float32) / 255.0
    for c in range(3):
        image_bgr[:, :, c] = (
            image_bgr[:, :, c].astype(np.float32) * (1 - alpha_mask) +
            overlay[:, :, c].astype(np.float32) * alpha_mask
        ).astype(np.uint8)

    return image_bgr


# ============================================================
#  辅助工具
# ============================================================

def get_nontransparent_bounds(image_bgra: np.ndarray, thresh: int = 127):
    """
    获取 4 通道图像中最大非透明区域的边界距离信息。
    """
    if image_bgra is None or image_bgra.size == 0:
        raise ValueError("输入图像为空")
    if len(image_bgra.shape) != 3 or image_bgra.shape[2] != 4:
        raise ValueError("输入图像必须是 4 通道 BGRA 图像")

    _, _, _, alpha = cv2.split(image_bgra)
    _, alpha_bin = cv2.threshold(alpha, thresh=thresh, maxval=255, type=0)
    contours, _ = cv2.findContours(alpha_bin, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return 0, 0, 0, 0

    contour_areas = [cv2.contourArea(c) for c in contours]
    max_idx = contour_areas.index(max(contour_areas))
    x, y, w, h = cv2.boundingRect(contours[max_idx])

    height, width = image_bgra.shape[:2]
    return y, height - (y + h), x, width - (x + w)


def save_image_with_dpi(image_array: np.ndarray, output_path: str, dpi: int = 300):
    """
    以指定 DPI 保存图像为 PNG。
    """
    if image_array is None or image_array.size == 0:
        raise ValueError("输入图像为空")

    if len(image_array.shape) == 2:
        pil_img = Image.fromarray(image_array, mode="L")
    elif image_array.shape[2] == 4:
        rgba = cv2.cvtColor(image_array, cv2.COLOR_BGRA2RGBA)
        pil_img = Image.fromarray(rgba, mode="RGBA")
    else:
        rgb = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb, mode="RGB")

    pil_img.save(output_path, format="PNG", dpi=(dpi, dpi))


def save_image_to_jpeg_kb(
    image_bgr: np.ndarray, output_path: str, target_kb: int, dpi: int = 300
):
    """将 BGR 图像保存为 JPEG，压缩到指定 KB 大小。"""
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("输入图像为空")
    if target_kb <= 0:
        save_image_with_dpi(image_bgr, output_path, dpi)
        return

    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    quality = 95
    while quality >= 1:
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=quality, dpi=(dpi, dpi))
        size_kb = len(buf.getvalue()) / 1024
        if size_kb <= target_kb or quality == 1:
            with open(output_path, "wb") as f:
                f.write(buf.getvalue())
            return
        quality -= 5
