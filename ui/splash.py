# -*- coding: utf-8 -*-
"""
启动页（Splash）
============================================================
设计约束：本模块只允许依赖 PySide6。

启动页必须在 numpy / cv2 / qfluentwidgets 这些重型模块导入**之前**
就能画到屏幕上——否则用户双击 run.bat 后看到的是几秒黑屏，而不是
加载反馈。一旦在这里 import ui.theme（它会拉起 qfluentwidgets），
启动页就要等重型依赖加载完才出现，等于白做。

代价：图标在本模块内自绘（简化版 logo，约 20 行），与
theme.make_logo_pixmap 视觉一致但不共享代码。改 logo 时记得两处同步。
"""
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QColor, QPainter, QLinearGradient, QFont, QPen, QBrush, QPainterPath,
)
from PySide6.QtWidgets import QWidget

WIDTH, HEIGHT = 460, 300

BG_TOP = "#FAFAFA"
BG_BOTTOM = "#F4F4F5"
BORDER = "#D4D4D8"
TRACK = "#E4E4E7"
ACCENT_LIGHT = "#FB923C"
ACCENT_DARK = "#EA580C"
TEXT_MAIN = "#18181B"
TEXT_SUB = "#71717A"
FONT_FAMILY = "Microsoft YaHei UI"

BAR_WIDTH = 260
BAR_HEIGHT = 4


class StartupSplash(QWidget):
    """无边框圆角启动页：logo + 标题 + 进度条 + 当前步骤文字。

    用法（见 launcher.py）：
        splash = StartupSplash()
        splash.show()
        splash.set_status("正在加载图像引擎…", 0.45)   # 立即重绘
        ...
        splash.finish(win)                              # 主窗口就绪后收起
    """

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WIDTH, HEIGHT)
        self._progress = 0.0
        self._status = "正在启动…"

    # ----------------------------------------------------------
    #  外部接口
    # ----------------------------------------------------------
    def set_status(self, text=None, progress=None):
        """更新进度与步骤文字，并立刻重绘。

        这里用 repaint() 而不是 update()：启动阶段还没有运行事件循环，
        update() 只投递重绘事件、可能一直排队不执行，界面就会卡在旧进度。
        """
        if progress is not None:
            self._progress = max(0.0, min(1.0, float(progress)))
        if text:
            self._status = text
        self.repaint()

    def finish(self, main_window):
        """主窗口显示后收起启动页。"""
        try:
            main_window.show()
            main_window.raise_()
        except Exception:
            pass
        self.close()
        self.deleteLater()

    # ----------------------------------------------------------
    #  绘制
    # ----------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        self._draw_background(p)
        cx = WIDTH / 2.0
        self._draw_logo(p, cx, 40, 60)
        self._draw_titles(p, cx, 122)
        self._draw_progress(p, cx, 208)
        p.end()

    def _draw_background(self, p):
        grad = QLinearGradient(0, 0, 0, HEIGHT)
        grad.setColorAt(0.0, QColor(BG_TOP))
        grad.setColorAt(1.0, QColor(BG_BOTTOM))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1), 14, 14)

        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1), 14, 14)

    def _draw_logo(self, p, cx, top, size):
        """简化版证件照 logo：橙色渐变相框 + 白底 + 人像剪影。"""
        left = cx - size / 2.0
        grad = QLinearGradient(left, top, left + size, top + size)
        grad.setColorAt(0.0, QColor(ACCENT_LIGHT))
        grad.setColorAt(1.0, QColor(ACCENT_DARK))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(left, top, size, size), size * 0.22, size * 0.22)

        pad = size * 0.17
        inner = size - 2 * pad
        inner_rect = QRectF(left + pad, top + pad, inner, inner)
        p.setBrush(QBrush(QColor(255, 255, 255, 235)))
        p.drawRoundedRect(inner_rect, size * 0.11, size * 0.11)

        # 人像剪影裁到白色相框内，避免肩部溢出到橙色边框上
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(inner_rect, size * 0.11, size * 0.11)
        p.setClipPath(clip)
        p.setBrush(QBrush(QColor("#9A3412")))
        head_r = inner * 0.15
        p.drawEllipse(QPointF(cx, top + pad + inner * 0.36), head_r, head_r)
        sw, sh = inner * 0.44, inner * 0.32
        p.drawEllipse(QRectF(cx - sw / 2, top + pad + inner * 0.52, sw, sh * 2))
        p.restore()

    def _draw_titles(self, p, cx, top):
        title_font = QFont(FONT_FAMILY, 20, QFont.Bold)
        p.setFont(title_font)
        p.setPen(QColor(TEXT_MAIN))
        p.drawText(QRectF(0, top, WIDTH, 32), Qt.AlignHCenter | Qt.AlignTop,
                   "证件照工作室")

        sub_font = QFont(FONT_FAMILY, 9)
        sub_font.setLetterSpacing(QFont.AbsoluteSpacing, 2.5)
        p.setFont(sub_font)
        p.setPen(QColor(TEXT_SUB))
        p.drawText(QRectF(0, top + 32, WIDTH, 18), Qt.AlignHCenter | Qt.AlignTop,
                   "ID PHOTO STUDIO")

    def _draw_progress(self, p, cx, top):
        left = cx - BAR_WIDTH / 2.0

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(TRACK)))
        p.drawRoundedRect(QRectF(left, top, BAR_WIDTH, BAR_HEIGHT),
                          BAR_HEIGHT / 2.0, BAR_HEIGHT / 2.0)

        filled = BAR_WIDTH * self._progress
        if filled > 0:
            grad = QLinearGradient(left, 0, left + BAR_WIDTH, 0)
            grad.setColorAt(0.0, QColor(ACCENT_LIGHT))
            grad.setColorAt(1.0, QColor(ACCENT_DARK))
            p.setBrush(QBrush(grad))
            w = max(BAR_HEIGHT, filled)   # 最小宽度 = 一个圆点，避免起始处残影
            p.drawRoundedRect(QRectF(left, top, w, BAR_HEIGHT),
                              BAR_HEIGHT / 2.0, BAR_HEIGHT / 2.0)

        p.setFont(QFont(FONT_FAMILY, 10))
        p.setPen(QColor(TEXT_SUB))
        p.drawText(QRectF(0, top + 14, WIDTH, 20), Qt.AlignHCenter | Qt.AlignTop,
                   self._status)
