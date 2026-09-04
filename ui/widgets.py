# -*- coding: utf-8 -*-
"""
可复用纯 UI 控件（不含任何业务逻辑）
============================================================
  * SliderRow      —— 标签 + 数字框 + 滑块（浮点/整数通用，合并高级/美颜/水印三处重复实现）
  * RadioGroup     —— 单选组（合并处理模式 / 渲染模式 / 模型选择 等重复实现）
  * ColorSwatchGroup —— 预设色板 + 自定义取色 + 当前色预览
  * StatusDot      —— 状态圆点
  * ThumbnailList / ThumbItem —— 缩略图队列（点击 / 双击 / 进度 / ✓）
  * TabStrip       —— Pivot 顶部标签 + QStackedWidget（合并「笔记本多页」实现）
所有控件只负责「长什么样、怎么摆」，通过 Signal / 回调把用户操作交给控制器。

UI 规范（本项目统一约定）
------------------------------------------------------------
1. 可视控件一律使用 qfluentwidgets 对应控件，禁止原生 Qt 可视控件。
2. 所有文字颜色 / 控件底色 / 明暗对比度完全交给 Fluent 主题系统（setTheme）管理，
   本文件内不写任何用于配色的 color / background-color。
   仅两类例外（均不涉及「文字 vs 背景」的对比度）：
     · 色块底色 —— 展示用户选择的证件照背景色，属「数据可视化」，必须如实显示
     · 主题色描边 —— 取自官方 API themeColor() 或 Qt 系统调色板角色 palette(mid)，
                     随主题自动变化，非硬编码
3. QStackedWidget / QButtonGroup / QListWidgetItem 为纯逻辑容器与数据项，
   无视觉属性、不绘制像素、不参与主题配色，也没有 Fluent 等价物，故保留原生实现。
"""
import cv2
from PIL import Image

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidgetItem, QGridLayout,
    QButtonGroup, QStackedWidget, QColorDialog, QLabel,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QColor

from qfluentwidgets import (
    Slider, DoubleSpinBox, SpinBox, RadioButton, BodyLabel, CaptionLabel,
    Pivot, FluentIcon, ProgressBar, SwitchButton,
    PushButton, TransparentToolButton, ListWidget, ImageLabel,
    InfoBadge, themeColor, CardWidget, StrongBodyLabel,
)

from . import theme  # 仅用于 register_theme_widget（主题色描边跟随主题切换）


# ============================================================
#  卡片容器（统一规范：CardWidget 包裹 + 内边距 20 + 间距 10）
# ============================================================
class CardSection(CardWidget):
    """带标题的卡片容器，统一替代原 theme.SectionCard（不再手写任何底色）。

    规范落地（本项目 UI 约定）：
      · 页面主体内容用 CardWidget 包裹
      · 卡片内边距 (20, 20, 20, 20)、间距 10
      · 标题用 Fluent StrongBodyLabel —— 字色由主题托管，明暗自动反转

    CardWidget 自身是「半透明白叠加层」，底色由父窗口/页面提供，
    因此它必须放在有正确底色的容器里（FluentWindow 或已设底色的页面）。

    接口与旧 SectionCard 完全一致（addWidget / addLayout / addSpacing），
    迁移时只需换类名，调用处零改动。
    """

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(14, 14, 14, 14)
        self.root.setSpacing(10)
        if title:
            self.title_label = StrongBodyLabel(title)
            self.root.addWidget(self.title_label)
        else:
            self.title_label = None

    def addWidget(self, w, stretch=0):
        self.root.addWidget(w, stretch)

    def addLayout(self, l, stretch=0):
        self.root.addLayout(l, stretch)

    def addSpacing(self, s):
        self.root.addSpacing(s)


# ============================================================
#  滑块行（标签 + 数字框 + 滑块）
# ============================================================
class SliderRow(QWidget):
    """浮点 / 整数滑块行：数字框与滑块双向同步，对外只暴露 value() / set_value()。
    布局：上行（标签 + 数字框），下行（滑块独占整行）。
    """

    def __init__(self, label, minimum, maximum, step, value,
                 decimals=0, parent=None):
        super().__init__(parent)
        self.decimals = decimals
        self.scale = 10 ** decimals  # 浮点滑块按整数比例缩放，规避整数滑块精度问题

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        # 上行：标签 + 数字框
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)

        # QLabel -> BodyLabel：字色/底色由 Fluent 主题托管，不再手写任何 QSS
        self.label = BodyLabel(label)
        self.label.setMinimumWidth(44)
        top.addWidget(self.label)

        # DoubleSpinBox 为 Fluent 自带控件，配色随明暗自动反转
        self.spin = DoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setValue(value)
        self.spin.setFixedWidth(150)
        top.addWidget(self.spin)
        top.addStretch(1)
        v.addLayout(top)

        # 下行：滑块独占整行
        self.slider = Slider(Qt.Horizontal)
        self.slider.setRange(int(minimum * self.scale), int(maximum * self.scale))
        self.slider.setValue(int(value * self.scale))
        v.addWidget(self.slider)

        self.spin.valueChanged.connect(self._on_spin)
        self.slider.valueChanged.connect(self._on_slider)

    def refresh_theme(self):
        """保留主题刷新钩子（theme 弱引用表可能回调）。

        全部控件均为 Fluent 原生控件、由 setTheme 自动刷新配色，
        本控件已无任何手写样式，故为空实现。
        """
        return

    def _on_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self.scale)))
        self.slider.blockSignals(False)

    def _on_slider(self, v):
        self.spin.blockSignals(True)
        self.spin.setValue(v / self.scale)
        self.spin.blockSignals(False)

    def value(self):
        return self.spin.value()

    def set_value(self, v):
        self.spin.setValue(v)


# ============================================================
#  单选组
# ============================================================
class RadioGroup(QWidget):
    """单选组：options=[(value, text), ...]，对外暴露 get() / set()。"""

    def __init__(self, options, default=None, orientation=Qt.Horizontal,
                 on_change=None, parent=None):
        super().__init__(parent)
        self._value = default
        if orientation == Qt.Horizontal:
            lay = QHBoxLayout(self)
        else:
            lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # QButtonGroup 是纯逻辑分组器（不可见、不绘制），保留原生实现
        self.group = QButtonGroup(self)
        self.buttons = {}
        for val, text in options:
            rb = RadioButton(text)  # 已是 Fluent 控件
            self.group.addButton(rb)
            self.buttons[val] = rb
            if val == default:
                rb.setChecked(True)
            lay.addWidget(rb)
            rb.toggled.connect(
                lambda checked, v=val: self._on_toggle(checked, v, on_change)
            )

    def _on_toggle(self, checked, v, on_change):
        if checked:
            self._value = v
            if on_change:
                on_change(v)

    def get(self):
        return self._value

    def set(self, v):
        btn = self.buttons.get(v)
        if btn is not None:
            btn.setChecked(True)


# ============================================================
#  圆形色板
# ============================================================
class _ColorButton(PushButton):
    """单个色块。

    · 底色 = 数据色（用户选择的证件照背景色），必须如实显示，不能交给主题托管
    · 选中描边 = 当前 Fluent 主题色（官方 API themeColor()，随主题自动变化）
    """

    def __init__(self, hex_val, size=22, parent=None):
        super().__init__(parent)
        self.hex_val = hex_val.upper()
        self._sel = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.set_selected(False)
        # 注册主题感知：改强调色 / 切明暗时重画选中描边（弱引用，无需手动反注册）
        theme.register_theme_widget(self)

    def set_selected(self, sel):
        self._sel = bool(sel)
        # palette(mid) 是 Qt 系统调色板角色，明暗下自动取合适值，非硬编码
        ring = themeColor().name() if sel else "palette(mid)"
        width = 2 if sel else 1
        self.setStyleSheet(
            f"QPushButton{{background-color:{self.hex_val};"
            f"border:{width}px solid {ring};border-radius:{self.width() // 2}px;}}"
        )

    def refresh_theme(self):
        """强调色或主题变更后，按最新 themeColor() 重画描边。"""
        self.set_selected(self._sel)


class ColorSwatchGroup(QWidget):
    """预设色板 + 自定义取色 + 当前色预览；选中即 emit colorSelected(hex)。"""

    colorSelected = Signal(str)

    def __init__(self, preset_colors, initial="#438EDB", parent=None):
        super().__init__(parent)
        self.selected_hex = initial.upper()
        self.swatches = []
        self._build(preset_colors, initial)

    def _build(self, preset_colors, initial):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        for _name, hex_val in preset_colors:
            b = _ColorButton(hex_val)
            b.clicked.connect(lambda _=None, h=hex_val: self.select(h))
            row.addWidget(b)
            self.swatches.append((hex_val.upper(), b))

        # 自定义取色：QPushButton("+") -> TransparentToolButton（Fluent 图标按钮）
        self.custom_btn = TransparentToolButton(FluentIcon.ADD)
        self.custom_btn.setFixedSize(22, 22)
        self.custom_btn.setCursor(Qt.PointingHandCursor)
        self.custom_btn.setToolTip("自定义颜色")
        self.custom_btn.clicked.connect(self._pick_custom)
        row.addWidget(self.custom_btn)
        row.addStretch(1)
        v.addLayout(row)

        # 当前色预览
        cur = QHBoxLayout()
        cur.setSpacing(6)
        # 纯色块：展示数据色、不含文字，用 QWidget 承载底色即可
        self.current_swatch = QWidget()
        self.current_swatch.setFixedSize(18, 18)
        self.current_label = CaptionLabel("")
        cur.addWidget(self.current_swatch)
        cur.addWidget(self.current_label)
        cur.addStretch(1)
        v.addLayout(cur)

        self.select(initial, emit=False)

        # 注册主题感知：切明暗时重画色块描边（弱引用）
        theme.register_theme_widget(self)

    def select(self, hex_val, emit=True):
        hex_val = hex_val.upper()
        self.selected_hex = hex_val
        for h, b in self.swatches:
            b.set_selected(h == hex_val)
        self._paint_current_swatch()
        self.current_label.setText("当前  " + hex_val)
        if emit:
            self.colorSelected.emit(hex_val)

    def _paint_current_swatch(self):
        """当前色预览块：底色=数据色，描边用 Qt 系统调色板角色（自动适配明暗）。"""
        self.current_swatch.setStyleSheet(
            f"background-color:{self.selected_hex};"
            f"border:1px solid palette(mid);border-radius:9px;"
        )

    def refresh_theme(self):
        """切明暗或改强调色后，重画当前色预览描边与各预设色块选中描边。"""
        self._paint_current_swatch()
        for _h, b in self.swatches:
            b.refresh_theme()

    def get_hex(self):
        return self.selected_hex

    def _pick_custom(self):
        # QColorDialog 是系统标准取色对话框，配色由系统/Qt 托管，不涉及主题手写
        init = QColor(self.selected_hex)
        color = QColorDialog.getColor(init, self, "选择背景颜色")
        if color.isValid():
            self.select(color.name().upper())


# ============================================================
#  数据色展示按钮（水印颜色 / 自定义底色等）
# ============================================================
class ColorSwatchButton(PushButton):
    """单色块按钮：展示「数据色」，点击后由调用方弹出取色器。

    · 底色 = 数据色（用户选的实际颜色），必须如实显示，不能交给主题托管
    · 描边  = Qt 系统调色板角色 palette(mid)，明暗下自动取合适值，非硬编码
    对外只暴露 set_color() / get_color()，点击沿用 PushButton.clicked 信号。
    """

    def __init__(self, hex_val="#FFFFFF", size=24, parent=None):
        super().__init__(parent)
        self.hex_val = hex_val.upper()
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._paint()
        # 注册主题感知：切明暗时按最新调色板角色重画描边（弱引用）
        theme.register_theme_widget(self)

    def _paint(self):
        self.setStyleSheet(
            f"QPushButton{{background-color:{self.hex_val};"
            f"border:1px solid palette(mid);border-radius:4px;}}"
        )

    def set_color(self, hex_val):
        self.hex_val = str(hex_val).upper()
        self._paint()

    def get_color(self):
        return self.hex_val

    def refresh_theme(self):
        """切明暗后按最新调色板角色重画描边。"""
        self._paint()


# ============================================================
#  状态圆点
# ============================================================
class StatusDot(QWidget):
    """状态圆点：圆点颜色是状态数据（如就绪绿 / 错误红），不参与主题配色。"""

    def __init__(self, color="#22C55E", size=10, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._set(color)

    def _set(self, color):
        self.setStyleSheet(
            f"background-color:{color};border-radius:{self.width() // 2}px;"
        )

    def set_color(self, color):
        self._set(color)


# ============================================================
#  缩略图队列
# ============================================================
# 滚动条压成 0 尺寸：视觉上看不见"两个杆杆"，但内容仍可滚动。
# （若用 ScrollBarAlwaysOff，内容超出可视区后将完全无法滚动，属功能缺陷）
# 注：此处只设尺寸与 transparent，不含任何文字/背景配色，与主题无关。
_HIDDEN_SCROLLBAR_QSS = (
    "QScrollBar:vertical{width:0px;background:transparent;margin:0;}"
    "QScrollBar:horizontal{height:0px;background:transparent;margin:0;}"
    "QScrollBar::handle:vertical,QScrollBar::handle:horizontal{"
    "background:transparent;min-width:0px;min-height:0px;}"
    "QScrollBar::add-line,QScrollBar::sub-line{width:0px;height:0px;}"
    "QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}"
)


def _cv2_to_pixmap(image, max_size):
    """OpenCV BGR/BGRA ndarray -> 等比缩放后的 QPixmap。"""
    if image is None:
        return None
    try:
        if len(image.shape) == 2:
            rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        elif image.shape[2] == 4:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
        else:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        pil.thumbnail((max_size, max_size), Image.LANCZOS)
        qimg = QImage(pil.tobytes(), pil.width, pil.height,
                      pil.width * 3, QImage.Format_RGB888)
        # .copy() 让返回的 QPixmap 自己拥有像素数据：避免 QImage 直接包裹
        # PIL tobytes() 临时缓冲区、函数返回后缓冲区被 GC 导致 Pixmap 指向
        # 悬空内存（极端情况下表现为预览图不刷新 / 显示旧图或花屏）。
        return QPixmap.fromImage(qimg).copy()
    except Exception:
        return None


def _cv2_to_qimage(image):
    """OpenCV BGR/BGRA/GRAY ndarray -> 全分辨率 QImage（用于打印）。

    与 `_cv2_to_pixmap` 的区别：不缩放、保留原始像素，保证打印清晰度。
    numpy 行主序连续内存直接交给 QImage，省一次 PIL 中转。
    """
    if image is None:
        return None
    try:
        if len(image.shape) == 2:
            rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            fmt, ch = QImage.Format_RGB888, 3
        elif image.shape[2] == 4:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
            fmt, ch = QImage.Format_RGBA8888, 4
        else:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            fmt, ch = QImage.Format_RGB888, 3
        h, w = rgb.shape[:2]
        return QImage(rgb.tobytes(), w, h, w * ch, fmt)
    except Exception:
        return None


class ThumbItem(QWidget):
    """队列缩略图条目 —— 常规默认样式。

    布局（紧凑竖排）：
      ┌─────────────┐
      │   照片       │  ← ImageLabel，圆角
      └─────────────┘
        45%          ← 处理中：名称位置直接显示百分比（橙色）
        文件名         ← 完成：绿色原名；空闲：默认色原名

    状态全部通过文件名文字表达：处理中直接显示「45%」、完成「绿色原名」、
    空闲恢复原名。照片上不再叠加任何徽章。
    """

    def __init__(self, name, image, thumb_size, parent=None):
        super().__init__(parent)
        self.thumb_size = thumb_size
        self._active = False
        self._alt_name = ""
        self._show_alt = False

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 2)
        v.setSpacing(3)

        # ---- 照片容器：ImageLabel（固定尺寸）+ 状态徽章绝对定位叠加 ----
        # 不用 QGridLayout：ImageLabel 放进 grid 后尺寸会塌成 0 导致不渲染。
        # 改为子控件绝对定位，ImageLabel 直接用固定尺寸（与最初可显示版本一致）。
        photo_container = QWidget()
        photo_container.setFixedSize(thumb_size, thumb_size)
        photo_container.setObjectName("thumbPhotoContainer")

        self.img_label = ImageLabel(photo_container)
        self.img_label.setFixedSize(thumb_size, thumb_size)
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setBorderRadius(6, 6, 6, 6)
        self.img_label.move(0, 0)

        # 状态徽章：叠在照片右下角（绝对定位，父容器固定尺寸坐标稳定）
        self._status_badge = QLabel(photo_container)
        self._status_badge.setAlignment(Qt.AlignCenter)
        self._status_badge.setFixedSize(22, 18)
        self._status_badge.move(thumb_size - 24, thumb_size - 20)
        self._status_badge.setVisible(False)
        self._status_badge.raise_()

        v.addWidget(photo_container, 0, Qt.AlignHCenter)

        # ---- 唯一名称标签：纯文字，无框无底色 ----
        self._base_name = str(name)
        self.name_label = CaptionLabel(name)
        from PySide6.QtGui import QFont
        f = self.name_label.font()
        f.setPixelSize(10)
        self.name_label.setFont(f)
        self.name_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.name_label.setWordWrap(True)
        v.addWidget(self.name_label)

        self._set_image(image)
        theme.register_theme_widget(self)

    def _set_image(self, image):
        pm = _cv2_to_pixmap(image, self.thumb_size)
        if pm is None:
            self.img_label.clear()
            self.img_label.setText("—")
        else:
            # ImageLabel 内部按固定尺寸 + 圆角自行缩放绘制
            self.img_label.setPixmap(pm)

    def set_status(self, text):
        """状态统一入口 —— 进度直接替换名称显示，完成后绿色原名。

        - "" / None        → 空闲：恢复原名、默认色、隐藏徽章
        - "✓"              → 完成：原名变绿、隐藏徽章
        - "0"~"100"（数字）→ 处理中：名称位置直接显示「45%」（橙色）、隐藏徽章
        - 其它文字          → 名称位置显示状态文字（橙色）、隐藏徽章
        """
        if text in ("", None):
            self._status_badge.setVisible(False)
            self._status_badge.setText("")
            self._status_badge.setStyleSheet("")
            self.name_label.setStyleSheet("")
            self.name_label.setText(self._base_name)
        elif text == "✓":
            # 完成后：原名变绿，不画徽章
            self._status_badge.setVisible(False)
            self._status_badge.setText("")
            self._status_badge.setStyleSheet("")
            self.name_label.setStyleSheet("color:#22C55E;")
            self.name_label.setText(self._base_name)
        else:
            self._status_badge.setVisible(False)
            self._status_badge.setText("")
            self._status_badge.setStyleSheet("")
            try:
                val = max(0, min(100, int(str(text).rstrip("%"))))
                # 处理中：百分比直接替换名称显示（不拼接原名）
                self.name_label.setStyleSheet("color:#F97316;")
                self.name_label.setText(f"{val}%")
            except ValueError:
                # 非数字状态文字直接显示在名称位置
                self.name_label.setStyleSheet("color:#F97316;")
                self.name_label.setText(f"{self._base_name}  {str(text)[:10]}")

    def set_alt_name(self, alt_name):
        """设置交替显示的第二名称（如处理后的文件名/输出名）。"""
        self._alt_name = str(alt_name) if alt_name else ""

    def toggle_name(self):
        """在原名和别名之间切换显示。"""
        if not self._alt_name:
            return
        self._show_alt = not self._show_alt
        self.name_label.setText(self._alt_name if self._show_alt else self.name_label.text() if hasattr(self, '_base_name') else self._alt_name)

    def set_names(self, primary, alternate=""):
        """设置主名称 + 可选的交替名称，立即显示主名称。"""
        self._base_name = str(primary)
        self._alt_name = str(alternate) if alternate else ""
        self._show_alt = False
        self.name_label.setText(self._base_name)

    def set_active(self, active):
        self._active = bool(active)
        # 选中态：无边框、无底色（干净默认样式）
        self.setStyleSheet("")

    def refresh_theme(self):
        """切明暗后刷新（当前选中态无边框，无需重绘）。"""
        pass


class ThumbnailList(ListWidget):
    """缩略图队列 / 网格。支持点击、双击、进度、✓ 标记。

    QListWidget -> Fluent ListWidget（QListWidget 子类）：
    视口底色 / 文字色 / 选中色全部由 Fluent 主题托管，明暗切换自动适配，
    不再需要手写 list_qss()（原方案是为绕开 Win11 原生样式的黑底黑字）。
    """

    itemSelected = Signal(int)
    itemDoubleClickedSig = Signal(int)

    def __init__(self, thumb_size=96, horizontal=False, name_max_len=24,
                 parent=None):
        super().__init__(parent)
        self.thumb_size = thumb_size
        self.name_max_len = name_max_len
        self.item_widgets = []

        self.setSpacing(4)
        self.setSelectionMode(ListWidget.SingleSelection)
        # 滚动条保留可用但压成 0 尺寸（看不见，仍能滚动）
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 仅叠加"隐藏滚动条"的尺寸样式，配色完全交给 Fluent 主题
        self.setStyleSheet(_HIDDEN_SCROLLBAR_QSS)
        if horizontal:
            self.setFlow(ListWidget.LeftToRight)
            self.setWrapping(False)
        else:
            self.setFlow(ListWidget.TopToBottom)
        self.setResizeMode(ListWidget.Adjust)

        self.itemClicked.connect(lambda it: self._emit(self.row(it), self.itemSelected))
        self.itemDoubleClicked.connect(
            lambda it: self._emit(self.row(it), self.itemDoubleClickedSig)
        )
        # 注册主题感知：切明暗时重设滚动条样式（弱引用）
        theme.register_theme_widget(self)

    def refresh_theme(self):
        """切明暗时重设滚动条尺寸样式（配色由 Fluent 主题自动处理）。"""
        self.setStyleSheet(_HIDDEN_SCROLLBAR_QSS)

    @staticmethod
    def _emit(row, signal):
        if row >= 0:
            signal.emit(row)

    def set_items(self, items):
        """items: [(name, image), ...]"""
        self._release_item_widgets()
        self.clear()
        self.item_widgets = []
        for name, image in items:
            self._add_row(name, image)

    def append_items(self, items):
        for name, image in items:
            self._add_row(name, image)

    def _release_item_widgets(self):
        """释放上一批 item 上挂的控件。

        QListWidget.clear() 只删 item，不会销毁 setItemWidget 挂上去的 QWidget。
        缩略图每次处理完都会整体重建，不显式 deleteLater 会持续泄漏内存。
        """
        for w in self.item_widgets:
            w.setParent(None)
            w.deleteLater()
        self.item_widgets = []

    def _add_row(self, name, image):
        disp = name if len(name) <= self.name_max_len else name[:self.name_max_len] + "…"
        w = ThumbItem(disp, image, self.thumb_size)
        it = QListWidgetItem()
        it.setSizeHint(w.sizeHint())
        self.addItem(it)
        self.setItemWidget(it, w)
        self.item_widgets.append(w)

    def select_index(self, idx):
        if 0 <= idx < self.count():
            self.setCurrentRow(idx)

    def set_status(self, idx, text):
        if 0 <= idx < len(self.item_widgets):
            self.item_widgets[idx].set_status(text)

    def set_item_active(self, idx):
        for i, w in enumerate(self.item_widgets):
            w.set_active(i == idx)

    def set_item_progress_done(self, idx):
        self.set_status(idx, "✓")

    def reset_item_progress(self, idx):
        self.set_status(idx, "")

    def scroll_to_item(self, idx):
        if 0 <= idx < self.count():
            self.scrollToItem(self.item(idx))


# ============================================================
#  顶部标签条（Pivot + QStackedWidget）
# ============================================================
class TabStrip(QWidget):
    """顶部标签切换 + 内容栈。tabs=[(key, text, widget), ...]。
    标签直接放 layout(不再包 ScrollArea),靠「2 字短标签 + fit 布局」自然放下。

    注：QStackedWidget 是纯逻辑容器——无边框、无底色、不绘制任何像素，
    不参与主题配色，也没有 Fluent 等价物（Fluent 的动画栈会引入切页淡入，
    在图片预览场景会闪烁），故保留原生实现。
    """

    def __init__(self, tabs, parent=None):
        super().__init__(parent)
        self.widgets = {}
        self.stack = QStackedWidget()

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        self.pivot = Pivot()
        for key, text, widget in tabs:
            self.widgets[key] = widget
            self.stack.addWidget(widget)
            self.pivot.addItem(key, text)
        # Pivot 的 currentItemChanged 信号才携带 routeKey；onClick 只收到 bool，故不能用。
        # 注意：信号连到 _apply 而不是 switch —— switch 会再调 setCurrentItem，
        # 直接连 switch 会形成「setCurrentItem → 信号 → switch → setCurrentItem」回环。
        self.pivot.currentItemChanged.connect(self._apply)
        v.addWidget(self.pivot)
        v.addWidget(self.stack, 1)

        if tabs:
            self.switch(tabs[0][0])

    def switch(self, key):
        """外部 / 初始化调用：同步 Pivot 高亮并切换内容栈。"""
        self.pivot.setCurrentItem(key)
        self._apply(key)  # 兜底：若 Pivot 当前已是该 key，信号不发射，此处保证内容同步

    def _apply(self, key):
        """Pivot 点击回调：只切内容栈，不再回头改 Pivot（避免信号回环）。"""
        w = self.widgets.get(key)
        if w is not None:
            self.stack.setCurrentWidget(w)

    def set_current(self, key):
        self.switch(key)
