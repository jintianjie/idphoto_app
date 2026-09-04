# -*- coding: utf-8 -*-
"""
颜色工具模块

提供与图像处理、UI 绘制共享的颜色转换函数。
保持零外部依赖，避免 image_utils / ui_widgets 互相引用。
"""


def hex_to_rgb_tuple(hex_str: str) -> tuple:
    """将 #RRGGBB 格式的十六进制颜色转为 (R, G, B) 元组。"""
    hex_str = hex_str.lstrip("#")
    if len(hex_str) != 6:
        raise ValueError(f"无效的颜色值: {hex_str}")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return (r, g, b)
