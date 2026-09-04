# -*- coding: utf-8 -*-
"""
右侧统一预览面板（替代原 tkinter 三画布合并方案）
============================================================
  * 顶部 Pivot 切换视图：自动 / 原图 / 证件照 / 排版
  * 单个预览卡片：标题 + 居中自适应缩放的图像（等比缩放，绝不拉伸变形）
  * 鼠标滚轮缩放、左键拖拽平移
  * 无图时显示虚线占位提示
视图切换通过回调通知控制器（控制器据此决定刷新哪类图像）。
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF
from PySide6.QtGui import QPixmap, QPainter, QColor

from qfluentwidgets import Pivot, CardWidget

from . import theme, widgets


class PreviewLabel(QLabel):
    """支持滚轮缩放 + 左键拖拽平移的预览图标签。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None          # 原始（大）QPixmap
        self._fit_scale = 1.0        # 适配标签时的基准缩放
        self._zoom = 1.0             # 用户缩放倍率（1 = 适配）
        self._pan = QPoint(0, 0)     # 屏幕像素偏移（居中后再叠加）
        self._placeholder = "请选择图片并处理"
        self._dragging = False
        self._drag_last = QPointF(0, 0)
        self.setMouseTracking(True)
        self.setMinimumSize(220, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"background-color:{theme.pal('surface2')};border-radius:6px;")
        self.setCursor(Qt.ArrowCursor)
        # 注册主题感知：浅/深切换时重染预览占位底色（弱引用，无需手动反注册）
        theme.register_theme_widget(self)

    def refresh_theme(self):
        """浅/深切换时重染预览占位底色（surface2 跟随当前调色板）。"""
        self.setStyleSheet(
            f"background-color:{theme.pal('surface2')};border-radius:6px;")

    # ---- 数据接口 ----
    def set_preview_pixmap(self, pixmap, placeholder="请选择图片并处理"):
        self._placeholder = placeholder
        self._pixmap = pixmap
        self._zoom = 1.0
        self._pan = QPoint(0, 0)
        self._compute_fit()
        self.update()

    def _compute_fit(self):
        if self._pixmap is None or self._pixmap.width() == 0:
            self._fit_scale = 1.0
            return
        aw = max(self.width() - 16, 40)
        ah = max(self.height() - 16, 40)
        self._fit_scale = min(aw / self._pixmap.width(),
                              ah / self._pixmap.height())

    def _image_rect(self):
        s = self._fit_scale * self._zoom
        disp_w = self._pixmap.width() * s
        disp_h = self._pixmap.height() * s
        left = (self.width() - disp_w) / 2 + self._pan.x()
        top = (self.height() - disp_h) / 2 + self._pan.y()
        return QRectF(left, top, disp_w, disp_h)

    # ---- 缩放 / 平移交互 ----
    def wheelEvent(self, ev):
        if self._pixmap is None:
            return
        cx, cy = ev.position().x(), ev.position().y()
        old_zoom = self._zoom
        factor = 1.15 if ev.angleDelta().y() > 0 else 1.0 / 1.15
        new_zoom = max(0.2, min(8.0, old_zoom * factor))
        if new_zoom == old_zoom:
            return
        s_old = self._fit_scale * old_zoom
        s_new = self._fit_scale * new_zoom
        # 光标对应的图像点（相对屏幕中心）
        cx0 = self.width() / 2 + self._pan.x()
        cy0 = self.height() / 2 + self._pan.y()
        img_x = (cx - cx0) / s_old
        img_y = (cy - cy0) / s_old
        # 缩放后让该图像点仍停在光标下
        new_cx = cx - img_x * s_new
        new_cy = cy - img_y * s_new
        self._pan = QPoint(int(new_cx - self.width() / 2),
                           int(new_cy - self.height() / 2))
        self._zoom = new_zoom
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._pixmap is not None:
            self._dragging = True
            self._drag_last = ev.position()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._dragging and self._pixmap is not None:
            pos = ev.position()
            dx = pos.x() - self._drag_last.x()
            dy = pos.y() - self._drag_last.y()
            self._pan += QPoint(int(dx), int(dy))
            self._drag_last = pos
            self.update()
        elif self._pixmap is not None:
            self.setCursor(Qt.OpenHandCursor)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor if self._pixmap is not None
                           else Qt.ArrowCursor)
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev):
        if not self._dragging:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._compute_fit()
        self.update()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect()
        painter.fillRect(r, QColor(theme.pal("surface2")))
        if self._pixmap is None:
            painter.setPen(QColor(theme.pal("text3")))
            painter.drawText(r, Qt.AlignCenter, self._placeholder)
            return
        rect = self._image_rect()
        painter.drawPixmap(rect.toRect(), self._pixmap)


class PreviewPane(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.view = "auto"
        self._view_cb = None

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # 视图切换
        self.pivot = Pivot()
        for key, text in [("auto", "自动"), ("original", "原图"),
                          ("idphoto", "证件照"), ("layout", "排版")]:
            self.pivot.addItem(key, text)
        # Pivot 的 currentItemChanged 信号才携带 routeKey；onClick 只收到 bool，故不能用。
        self.pivot.currentItemChanged.connect(self._on_view)
        v.addWidget(self.pivot)

        # 预览卡片
        card = CardWidget()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 14, 14, 14)
        cv.setSpacing(8)
        self.title_label = theme.pane_title("预览")
        cv.addWidget(self.title_label)

        self.image_label = PreviewLabel()
        cv.addWidget(self.image_label, 1)
        v.addWidget(card, 1)

        self.pivot.setCurrentItem("auto")

    def set_view_changed_callback(self, cb):
        self._view_cb = cb

    def set_view(self, k):
        """以代码方式切换预览视图（同步 Pivot 高亮并通知控制器）。

        只调用 setCurrentItem：它会发出 currentItemChanged(routeKey)，
        由 _on_view 统一更新 self.view 并回调控制器，避免重复触发。
        """
        if k not in ("auto", "original", "idphoto", "layout"):
            return
        self.pivot.setCurrentItem(k)

    def _on_view(self, k):
        self.view = k
        if self._view_cb:
            self._view_cb(k)

    def set_title(self, t):
        self.title_label.setText(t)

    def show_image(self, image, placeholder="请选择图片并处理"):
        self.image = image
        if image is None:
            self.image_label.set_preview_pixmap(None, placeholder)
            return
        pm = widgets._cv2_to_pixmap(image, 2000)
        if pm is None:
            self.image_label.set_preview_pixmap(None, placeholder)
            return
        self.image_label.set_preview_pixmap(pm, placeholder)
