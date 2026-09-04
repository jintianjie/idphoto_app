# -*- coding: utf-8 -*-
"""
Fluent 主题与统一控件工厂
============================================================
UI 与代码分离的核心：所有「样式 / 控件外观参数」集中在此，
视图层只调用工厂函数，避免在每个页面里散写重复的按钮 / 卡片 / 字号配置。

设计要点（对应需求）：
  * 统一主题层：主题模式(浅色/深色) + 强调色(可配置) 全部由本模块接管。
    ``apply_theme(settings)`` 一处读取设置、应用 Fluent 主题与强调色，并刷新
    全局调色板 ``PALETTE``。所有 QSS 工厂函数都引用 ``pal()`` / ``THEME_COLOR``，
    不再散写硬编码十六进制，避免「暗色壳 + 浅色控件」的割裂外观。
  * 合并可复用的 UI 参数：primary/secondary/danger/subtle 四种按钮工厂，
    SectionCard（带标题卡片）工厂，info 信息条工厂——凡是多处重复出现的
    控件参数都收敛成「一个函数」。
  * 不写绝对位置 / 绝对大小：工厂只管「外观」，布局交给各视图的
    QVBoxLayout / QHBoxLayout / QGridLayout / QSplitter。
  * 「紧凑模式」：右栏 280-320px 内塞 5 个 Tab + 多种参数行，整体紧凑字号、
    按钮 padding 小，避免显示不全。
"""
import traceback
import types
import weakref

from PySide6.QtWidgets import (
    QApplication, QPushButton, QVBoxLayout, QLabel, QSizePolicy,
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import (
    QColor, QIcon, QPainter, QPixmap, QPen, QBrush, QLinearGradient,
    QPalette,
)

from qfluentwidgets import (
    setTheme, Theme, setThemeColor,
    PrimaryPushButton, PushButton, TransparentPushButton,
    CardWidget, InfoBar, InfoBarPosition, setCustomStyleSheet,
)

# ============================================================
#  全局主题状态（由 apply_theme 刷新）
# ============================================================
# 强调色（默认橙色），可被设置覆盖。全工程唯一来源。
THEME_COLOR = "#F97316"
# 主题模式：'light' 浅色（默认） / 'dark' 深色
THEME_MODE = "light"
# 全局字号系数（1.0 = Fluent 原厂字号），由 apply_theme → set_ui_font_scale 刷新
UI_FONT_SCALE = 1.0

# 中性色调色板：浅色与深色两套，按当前模式切换。
# 语义键：text 主文字 / text2 次文字 / text3 说明文字 / border 描边 /
#        surface 微凸表面(输入框/按钮底) / surface2 更凸表面(卡片) / bg 背景
#
# 设计原则：浅色模式用更高对比度的暗色（黑/深灰），深色模式用纯白/近白，
# 避免 OS 系统深色模式把 QLabel 默认成白字后白底卡片字消失。
# text3 在浅色下原本 #71717A（视觉太淡、用户反馈"看不清"），已加深到 #3F3F46。
_LIGHT = {
    "text":   "#18181B",
    "text2":  "#3F3F46",
    "text3":  "#3F3F46",   # 与 text2 保持一致：保证说明文字也有高对比度
    "border": "#D4D4D8",
    "surface": "#FFFFFF",
    "surface2": "#F4F4F5",
    "bg":      "#FAFAFA",
    "success": "#15803D",  # 绿色"完成"文字（浅底配深绿，对比度 ≥ 4.5:1）
}
_DARK = {
    "text":   "#FAFAFA",
    "text2":  "#D4D4D8",
    "text3":  "#D4D4D8",   # 与 text2 保持一致：保证说明文字也能看清
    "border": "#3F3F46",
    "surface": "#27272A",
    "surface2": "#1F1F23",
    "bg":      "#18181B",
    "success": "#4ADE80",  # 绿色"完成"文字（深底配浅绿，对比度 ≥ 4.5:1）
}
PALETTE = dict(_LIGHT)


def pal(key=None):
    """取当前调色板。无参返回整个 dict（便于 f-string 解包）。"""
    if key is None:
        return PALETTE
    return PALETTE.get(key, "#000000")


def _shade(hex_color: str, factor: float) -> str:
    """对十六进制颜色按比例提亮(factor>1)/压暗(factor<1)，用于 hover/pressed。

    强调色是用户可配的，不能写死 hover 色，统一用本函数从强调色推导。
    """
    try:
        c = QColor(hex_color)
        r = max(0, min(255, int(round(c.red() * factor))))
        g = max(0, min(255, int(round(c.green() * factor))))
        b = max(0, min(255, int(round(c.blue() * factor))))
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return hex_color


def accent_tint(mix_white: float = 0.85) -> str:
    """把强调色向白色混合 mix_white 比例，得到浅染背景色。

    用于浅色主题下「选中 / 高亮」的轻微底色（既不抢眼、又有强调色呼应）。
    mix_white=0.85 → 接近白、略带强调色；值越大越浅。
    """
    try:
        c = QColor(THEME_COLOR)
        w = QColor("#FFFFFF")
        t = max(0.0, min(1.0, mix_white))
        r = int(round(c.red() * (1 - t) + w.red() * t))
        g = int(round(c.green() * (1 - t) + w.green() * t))
        b = int(round(c.blue() * (1 - t) + w.blue() * t))
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return "#FFFFFF"


def _apply_neutral_palette():
    """给裸 QLabel / 裸 QWidget 一个中性调色板，中和 Windows 11 / macOS
    系统级深色模式的影响（否则裸 QLabel 默认渲染成白字，放到浅色卡片里彻底消失）。

    ⚠️ 这里**绝不能**用 ``QApplication.setStyle("Fusion")``。
    setStyle 会销毁旧 QStyle 并对**全应用所有 widget** 发送 StyleChange 事件
    + 重新 polish，代价极高；更致命的是它会在设置对话框保存时与
    ``dlg.deleteLater()`` 竞争——dialog 的子控件此时已排队待销毁，
    polish 踩到它们就是 use-after-free → **卡死 / 闪退**（2026-09-02 实测根因）。

    setPalette 只替换调色板对象，不重建 QStyle、不触发全局 StyleChange，
    代价与 setStyle 差两个数量级，且与 widget 销毁流程无竞争。
    Fluent 原生控件由 setTheme 自己管理调色板，不受此处影响。
    """
    app = QApplication.instance()
    if app is None:
        return
    try:
        p = QPalette()
        surface = QColor(PALETTE["surface"])
        surface2 = QColor(PALETTE["surface2"])
        bg = QColor(PALETTE["bg"])
        text = QColor(PALETTE["text"])
        text3 = QColor(PALETTE["text3"])
        p.setColor(QPalette.Window, surface)
        p.setColor(QPalette.WindowText, text)
        p.setColor(QPalette.Base, bg)
        p.setColor(QPalette.AlternateBase, surface2)
        p.setColor(QPalette.Text, text)
        p.setColor(QPalette.Button, surface)
        p.setColor(QPalette.ButtonText, text)
        p.setColor(QPalette.ToolTipBase, surface)
        p.setColor(QPalette.ToolTipText, text)
        p.setColor(QPalette.PlaceholderText, text3)
        p.setColor(QPalette.BrightText, QColor("#FFFFFF"))
        p.setColor(QPalette.Highlight, QColor(THEME_COLOR))
        p.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
        p.setColor(QPalette.Disabled, QPalette.Text, text3)
        p.setColor(QPalette.Disabled, QPalette.WindowText, text3)
        p.setColor(QPalette.Disabled, QPalette.ButtonText, text3)
        app.setPalette(p)
    except Exception:
        traceback.print_exc()


# ============================================================
#  全局字号缩放（界面文字放不下时统一调小）
# ============================================================
_FONT_SCALE_HOOKED = False


def set_ui_font_scale(scale=1.0):
    """把全应用文字统一放大/缩小一档（含 Fluent 原生控件）。

    为什么需要它：qfluentwidgets 的字号是**构造时硬编码的像素值**
    （BodyLabel=14px / CaptionLabel=12px / SubtitleLabel=20px /
    PushButton=14px / InfoBadge=11px / Pivot=18px…），官方没有全局缩放开关。
    全库唯一的统一出口是 ``qfluentwidgets.common.font.getFont`` ——
    所有 ``setFont(widget, size)`` 最终都调用它。因此在**构造任何控件之前**
    包装一次该函数，把 pixelSize 乘上系数，即可一次性作用于全部 Fluent 控件。

    ⚠️ 必须在创建第一个 Fluent 控件之前调用（main() 里 apply_theme 在
    IDPhotoApp() 之前，满足要求）。已构造的控件不会回溯更新。

    原生 Qt 控件（QColorDialog / QMessageBox 等）不吃 Fluent 这套，
    靠同步缩放 QApplication 默认字体覆盖。
    """
    global UI_FONT_SCALE, _FONT_SCALE_HOOKED
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    # 下限 0.8（再小中文糊），上限 1.2（再大窄栏放不下）
    UI_FONT_SCALE = max(0.8, min(1.2, scale))

    if not _FONT_SCALE_HOOKED:
        from qfluentwidgets.common import font as _qf_font
        from PySide6.QtGui import QFont as _QFont

        _orig_get_font = _qf_font.getFont

        def _scaled_get_font(fontSize=14, weight=_QFont.Normal):
            f = _orig_get_font(fontSize, weight)
            s = UI_FONT_SCALE
            if f.pixelSize() > 0:
                f.setPixelSize(max(9, int(round(f.pixelSize() * s))))
            elif f.pointSizeF() > 0:
                f.setPointSizeF(max(7.0, f.pointSizeF() * s))
            return f

        _qf_font.getFont = _scaled_get_font
        # 关键补丁：多数子模块写的是 ``from ...common.font import getFont``
        # （如 components/widgets/label.py），它们在 import 时就把函数引用**复制**
        # 到了自己的模块命名空间，只改 common.font.getFont 对它们无效
        # （实测：PushButton 生效、BodyLabel 不变就是这个原因）。
        # 所以要把包内**所有**模块的 getFont 引用一并换掉，并先把惰性模块导入进来。
        import sys as _sys
        import pkgutil as _pkgutil
        import importlib as _importlib
        import qfluentwidgets as _qf_pkg
        try:
            for _info in _pkgutil.walk_packages(_qf_pkg.__path__,
                                                _qf_pkg.__name__ + "."):
                try:
                    _importlib.import_module(_info.name)
                except Exception:
                    pass  # 可选依赖缺失的子模块直接跳过
        except Exception:
            pass
        for _name, _mod in list(_sys.modules.items()):
            if not _name.startswith("qfluentwidgets"):
                continue
            try:
                if getattr(_mod, "getFont", None) is _orig_get_font:
                    setattr(_mod, "getFont", _scaled_get_font)
            except Exception:
                pass
        _FONT_SCALE_HOOKED = True

    # 原生控件：同步缩放应用默认字体
    try:
        app = QApplication.instance()
        if app is not None:
            f = app.font()
            s = UI_FONT_SCALE
            if f.pointSizeF() > 0:
                f.setPointSizeF(max(7.0, f.pointSizeF() * s))
            else:
                f.setPixelSize(max(9, int(round(f.pixelSize() * s))))
            app.setFont(f)
    except Exception:
        traceback.print_exc()


def apply_theme(settings=None):
    """应用主题（浅/深模式 + 强调色）。程序启动与设置保存时都调用。

    settings 为 SettingsStore 实例或 None：
      * 传 settings → 读取 appearance.theme_mode / appearance.accent_color
        （缺失则回退默认：浅色 + 橙色）。
      * 不传 → 使用内置默认（浅色 + 橙色），供启动页在 SettingsStore
        构造之前先点亮界面。

    注意：不要在此追加全局 QSS 修复 SpinBox。
    QFluentWidgets 的控件级样式优先级高于 QApplication 全局样式表，
    全局写法实际不生效；SpinBox 配色统一在各自的控件实例上设置
    （见 widgets.SliderRow / layout_left_pane 的数量框）。向全局样式表
    追加内容还会污染 Fluent 主题，影响其它控件。
    """
    global THEME_COLOR, THEME_MODE, PALETTE
    mode = "light"
    accent = "#F97316"
    if settings is not None:
        mode = (settings.get("appearance.theme_mode") or "light")
        accent = (settings.get("appearance.accent_color") or "#F97316")

    # 全局字号缩放：必须在构造任何控件之前（本函数在 main() 里先于主窗口运行）。
    # 优先读设置 ui.font_scale，缺失则回退 styles.UI_FONT_SCALE。
    try:
        _scale = None
        if settings is not None:
            _scale = settings.get("ui.font_scale")
        if _scale is None:
            from styles import UI_FONT_SCALE as _scale  # 项目常量层默认值
        set_ui_font_scale(_scale)
    except Exception:
        traceback.print_exc()

    THEME_MODE = mode if mode in ("light", "dark") else "light"
    THEME_COLOR = accent
    PALETTE = dict(_LIGHT if THEME_MODE == "light" else _DARK)
    setTheme(Theme.LIGHT if THEME_MODE == "light" else Theme.DARK)
    setThemeColor(QColor(THEME_COLOR))
    # 必须放在 setTheme / setThemeColor 之后：FluentWidgets 内部也会动
    # QApplication 调色板，先设会被它覆盖掉。
    _apply_neutral_palette()
    # 通知已注册的监听者（如主窗口）重染自定义 chrome，使浅/深切换即时生效
    _notify_theme_changed()


# ============================================================
#  主题变更监听（让自定义控件在浅/深切换时即时重染）
# ============================================================
_THEME_LISTENERS = []


def add_theme_listener(fn):
    """注册主题变更回调；apply_theme 末尾会逐个调用。重复注册忽略。"""
    if callable(fn) and fn not in _THEME_LISTENERS:
        _THEME_LISTENERS.append(fn)


def _notify_theme_changed():
    for fn in list(_THEME_LISTENERS):
        try:
            fn()
        except Exception:
            traceback.print_exc()
    # 控件级监听者：持有 refresh_theme() 的自定义控件（缩略图、色板、主题按钮等），
    # 用弱引用注册，控件被销毁后自动从列表移除，无内存泄漏、也不存在悬空调用。
    for ref, method in list(_WIDGET_LISTENERS):
        obj = ref()
        if obj is None:
            continue
        try:
            getattr(obj, method)()
        except Exception:
            traceback.print_exc()


# ============================================================
#  控件级主题监听（弱引用，自动回收）
# ============================================================
# 元素为 (weakref.ref, method_name)。apply_theme 末尾统一调用 refresh_theme()。
_WIDGET_LISTENERS = []


def register_theme_widget(widget, method="refresh_theme"):
    """注册一个带 refresh_theme() 的控件，浅/深切换时自动重染。

    使用弱引用：控件销毁后自动从列表移除，无需手动反注册，也不会阻止 GC。
    仅当控件存在指定方法时才注册（不存在则静默忽略）。
    """
    if not hasattr(widget, method):
        return
    ref = weakref.ref(widget, _remove_widget_listener)
    _WIDGET_LISTENERS.append((ref, method))


def _remove_widget_listener(ref):
    global _WIDGET_LISTENERS
    _WIDGET_LISTENERS = [(r, m) for (r, m) in _WIDGET_LISTENERS if r is not ref]


# ============================================================
#  QSS 工厂（全部引用 pal() / THEME_COLOR，杜绝硬编码暗色）
# ============================================================
def danger_qss():
    p = pal()
    return (
        f"QPushButton{{background-color:#EF4444;color:#FFFFFF;border:none;"
        f"border-radius:5px;padding:4px 10px;font-weight:bold;font-size:11px;}}"
        f"QPushButton:hover{{background-color:#F87171;}}"
        f"QPushButton:pressed{{background-color:#DC2626;}}"
        f"QPushButton:disabled{{background-color:{p['surface2']};"
        f"color:{p['text3']};}}"
    )


def compact_primary_qss():
    """紧凑主按钮：强调色实心，hover/pressed 由强调色推导。"""
    return (
        f"QPushButton{{background-color:{THEME_COLOR};color:#FFFFFF;border:none;"
        f"border-radius:4px;padding:3px 8px;font-size:11px;font-weight:600;}}"
        f"QPushButton:hover{{background-color:{_shade(THEME_COLOR, 1.12)};}}"
        f"QPushButton:pressed{{background-color:{_shade(THEME_COLOR, 0.88)};}}"
        f"QPushButton:disabled{{background-color:{pal('surface2')};"
        f"color:{pal('text3')};}}"
    )


def compact_secondary_qss():
    p = pal()
    return (
        f"QPushButton{{background-color:{p['surface2']};color:{p['text']};"
        f"border:1px solid {p['border']};"
        f"border-radius:4px;padding:3px 8px;font-size:11px;}}"
        f"QPushButton:hover{{background-color:{p['border']};color:{p['text']};}}"
        f"QPushButton:pressed{{background-color:{_shade(p['border'], 0.9)};}}"
        f"QPushButton:disabled{{background-color:{p['bg']};"
        f"color:{p['text3']};border-color:{p['border']};}}"
    )


def compact_danger_qss():
    return (
        "QPushButton{background-color:#EF4444;color:#FFFFFF;border:none;"
        "border-radius:4px;padding:3px 8px;font-size:11px;font-weight:600;}"
        "QPushButton:hover{background-color:#F87171;}"
        "QPushButton:pressed{background-color:#DC2626;}"
        f"QPushButton:disabled{{background-color:{pal('surface2')};"
        f"color:{pal('text3')};}}"
    )


def block_primary_qss():
    """导出区大主按钮（强调色实心，36px 高）。"""
    return (
        f"QPushButton{{background-color:{THEME_COLOR};color:#FFFFFF;border:none;"
        f"border-radius:6px;padding:8px 14px;font-size:13px;font-weight:bold;"
        f"min-height:36px;}}"
        f"QPushButton:hover{{background-color:{_shade(THEME_COLOR, 1.12)};}}"
        f"QPushButton:pressed{{background-color:{_shade(THEME_COLOR, 0.88)};}}"
    )


def block_secondary_qss():
    """导出区大次按钮（表面色 + 描边）。"""
    p = pal()
    return (
        f"QPushButton{{background-color:{p['surface2']};color:{p['text']};"
        f"border:1px solid {p['border']};border-radius:6px;padding:8px 14px;"
        f"font-size:13px;min-height:36px;}}"
        f"QPushButton:hover{{background-color:{p['border']};color:{p['text']};}}"
        f"QPushButton:pressed{{background-color:{_shade(p['border'], 0.9)};}}"
    )


def spinbox_qss(cls="QSpinBox"):
    """SpinBox / DoubleSpinBox 的配色修正样式（实例级设置，勿设到全局）。

    跟随主题模式反转文字/底色，保证浅色下深色字、深色下浅色字，都有对比度；
    右侧 padding 18px 是给原生上下箭头留位，避免箭头压住数字。
    """
    p = pal()
    return (
        f"{cls}{{color:{p['text']};background-color:{p['surface2']};"
        f"border:1px solid {p['border']};border-radius:4px;"
        f"padding:2px 18px 2px 6px;}}"
    )


def list_qss():
    """裸 QListWidget 的统一 QSS（成品列表 / 混排列表 / 缩略图列表共用）。

    必须显式写 background + color：Win11 系统深色模式下 Qt 原生
    windows11 样式会给 item view 视口配自己的深色底 + 深色字
    （QPalette 修复压不住它），表现为「黑底黑字 / 白底白字看不见」。
    显式 QSS 优先级高于原生样式，可彻底压住。
    """
    p = pal()
    return (
        f"QListWidget{{background-color:{p['surface']};color:{p['text']};"
        f"border:none;border-radius:4px;}}"
        f"QListWidget::item{{color:{p['text']};background:transparent;}}"
        f"QListWidget::item:hover{{background-color:{p['surface2']};}}"
        # 选中态 = 描边框（不要色块填充）。描边画在 ::item 上会被
        # setItemWidget 的子控件盖住，因此 ThumbItem 自身在 set_active
        # 里画同样的框；这里的 border 主要给纯文本列表（成品/混排）用。
        f"QListWidget::item:selected{{background:transparent;"
        f"border:2px solid {THEME_COLOR};border-radius:6px;"
        f"color:{p['text']};}}"
    )


def accent_selected_bg():
    """缩略图选中态底色（ThumbItem 自绘，位于 ::item 选中色之上）。

    浅色 = 强调色向白浅染（配深色文字）；深色 = 强调色压暗（配浅色文字）。
    """
    if THEME_MODE == "dark":
        return _shade(THEME_COLOR, 0.38)
    return accent_tint(0.86)


def _qicon(fi):
    if fi is None:
        return QIcon()
    return fi.qicon()


# ============================================================
#  按钮工厂（合并「文字 + 图标 + 回调」三类重复参数）
# ============================================================
def primary_button(text, icon=None, on_click=None, parent=None):
    """主操作按钮：强调色实心（PrimaryPushButton）。"""
    b = PrimaryPushButton(text, parent)
    if icon is not None:
        b.setIcon(_qicon(icon))
    if on_click:
        b.clicked.connect(on_click)
    return b


def secondary_button(text, icon=None, on_click=None, parent=None):
    """次要按钮：Fluent 默认表面色（PushButton）。"""
    b = PushButton(text, parent)
    if icon is not None:
        b.setIcon(_qicon(icon))
    if on_click:
        b.clicked.connect(on_click)
    return b


def subtle_button(text, icon=None, on_click=None, parent=None):
    """弱操作：透明背景按钮（TransparentPushButton）。"""
    b = TransparentPushButton(text, parent)
    if icon is not None:
        b.setIcon(_qicon(icon))
    if on_click:
        b.clicked.connect(on_click)
    return b


def danger_button(text, icon=None, on_click=None, parent=None):
    """危险操作：红色按钮（删除 / 清空 / 移除）。紧凑 11px 用于拥挤面板。"""
    b = ThemedPushButton(danger_qss, text, parent)
    if icon is not None:
        b.setIcon(_qicon(icon))
    if on_click:
        b.clicked.connect(on_click)
    return b


# ============================================================
#  紧凑按钮工厂 —— 用于左/右拥挤面板内的按钮行
# ============================================================
class ThemedPushButton(QPushButton):
    """QPushButton 子类：持有「重新生成 QSS 的工厂函数」，主题切换时自动重染。

    用于紧凑按钮、导出大按钮、危险按钮等需要跟随浅/深模式与强调色的自定义按钮。
    构造时即应用一次工厂样式，并注册到主题监听；切浅/深或改强调色后，
    ``refresh_theme()`` 会重新生成并把最新 ``THEME_COLOR`` / ``pal()`` 写回样式表，
    使橙色主按钮、表面色次按钮即时跟随，无需各视图手动维护。
    """

    def __init__(self, qss_factory=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._qss_factory = qss_factory
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        if qss_factory is not None:
            self.setStyleSheet(qss_factory())
        # 弱引用注册：控件销毁后自动反注册，无内存泄漏
        register_theme_widget(self)

    def refresh_theme(self):
        if self._qss_factory is not None:
            self.setStyleSheet(self._qss_factory())


def compact_primary_button(text, icon=None, on_click=None, parent=None):
    """紧凑主按钮：3px/8px padding + 11px 字号 + 强调色实心。"""
    b = ThemedPushButton(compact_primary_qss, text, parent)
    if icon is not None:
        b.setIcon(_qicon(icon))
    if on_click:
        b.clicked.connect(on_click)
    return b


def compact_secondary_button(text, icon=None, on_click=None, parent=None):
    """紧凑次按钮：3px/8px padding + 11px 字号 + 表面色。"""
    b = ThemedPushButton(compact_secondary_qss, text, parent)
    if icon is not None:
        b.setIcon(_qicon(icon))
    if on_click:
        b.clicked.connect(on_click)
    return b


def compact_danger_button(text, icon=None, on_click=None, parent=None):
    """紧凑危险按钮：3px/8px padding + 11px 字号 + 红色。"""
    b = ThemedPushButton(compact_danger_qss, text, parent)
    if icon is not None:
        b.setIcon(_qicon(icon))
    if on_click:
        b.clicked.connect(on_click)
    return b


def block_button(text, primary=True, icon=None, on_click=None, parent=None):
    """导出区大按钮（36px 高）：primary 用强调色实心，否则表面色 + 描边。

    通过 ThemedPushButton 自动跟随主题与强调色（替代原先的 setStyleSheet 覆盖写法）。
    """
    factory = block_primary_qss if primary else block_secondary_qss
    b = ThemedPushButton(factory, text, parent)
    if icon is not None:
        b.setIcon(_qicon(icon))
    if on_click:
        b.clicked.connect(on_click)
    return b


# ============================================================
#  复选/切换控件 —— SwitchButton / LineEdit 主题感知版
# ============================================================
def switch_qss():
    """开关：替换默认的硬编码黑灰，使浅色下 OFF 轨道不再灰扑扑。

    qfluentwidgets SwitchButton 用 Indicator 自绘，三色取自 hardcoded
    rgba(0,0,0,133/...) 而不是调色板。下方两个 ThemedSwitchButton 工厂做
    Indicator 子类实例替换/方法替身；此 QSS 仅负责 label 字色和间距等二级控制。
    """
    return (
        f"SwitchButton{{background:transparent;border:none;}}"
        f"SwitchButton>QLabel{{color:{pal('text')};"
        f"font:14px 'Segoe UI','Microsoft YaHei','PingFang SC';}}"
    )


def line_edit_qss():
    """LineEdit：底色跟随 surface，描边用 border；底部 focus 前不再画一条
    独立的「灰条」——focus 时再加深，模拟 Fluent 设计意图。"""
    p = pal()
    return (
        f"LineEdit, TextEdit, PlainTextEdit, TextBrowser{{"
        f"color:{p['text']};"
        f"background-color:{p['surface']};"
        f"border:1px solid {p['border']};"
        f"border-radius:4px;"
        f"}}"
        f"LineEdit:focus, TextEdit:focus, PlainTextEdit:focus, TextBrowser:focus{{"
        f"border:1px solid {THEME_COLOR};"
        f"}}"
    )


# 动态载入 qfluentwidgets 的 SwitchButton 作为主题感知版的基类。
# SwitchButton 入口定义在 widget 内部组件模块而非顶层 __init__，需要
# 按路径导入；为避免在 import 阶段把该模块拉到顶层，只在这里用一次。
_SwitchButtonBase = __import__(
    'qfluentwidgets.components.widgets.switch_button',
    fromlist=['SwitchButton'],
).SwitchButton


def _patch_indicator_colors(ind):
    """把 Indicator 的三色计算方法改写为读取 theme.pal() / THEME_COLOR。

    Indicator 是 qfluentwidgets 内部组件，没有公开扩展点，最直接的方案是
    在实例上替换它的三个方法；每个方法继续读 isChecked/isPressed/isHover
    等实例状态以保留 hover/press 等动画。
    """
    def _bg(self):
        if ind.isChecked():
            if not ind.isEnabled():
                return QColor(0, 0, 0, 56)
            if ind.isPressed:
                return QColor(_shade(THEME_COLOR, 0.88))
            if ind.isHover:
                return QColor(_shade(THEME_COLOR, 1.12))
            return QColor(THEME_COLOR)
        # OFF：保持透明底，让 border 当"轨道"
        if not ind.isEnabled():
            return QColor(0, 0, 0, 0)
        return QColor(0, 0, 0, 0)

    def _border(self):
        if ind.isChecked():
            if ind.isEnabled():
                return QColor(THEME_COLOR)
            return QColor(0, 0, 0, 0)
        # OFF：用主题 border 色，浅色 = #D4D4D8（与 surface 对比）
        return QColor(pal('border'))

    def _slider(self):
        if ind.isChecked():
            if ind.isEnabled():
                return QColor("#FFFFFF")     # ON 把手保持白
            return QColor(255, 255, 255, 77)
        # OFF 把手：用 text2 而非硬编码的黑灰半透明，跨主题一致
        if ind.isEnabled():
            return QColor(pal('text2'))
        return QColor(0, 0, 0, 91)

    ind._backgroundColor = types.MethodType(_bg, ind)
    ind._borderColor = types.MethodType(_border, ind)
    ind._sliderColor = types.MethodType(_slider, ind)


class ThemedSwitch(_SwitchButtonBase):
    """SwitchButton 主题感知版：替换 Indicator 自绘色为调色板派生色。

    解决两个问题：
    - 浅色 OFF 状态原本是 rgba(0,0,0,52%) 灰轨 + rgba(0,0,0,61%) 灰把——被
      用户吐槽"像进度条拖拽条都灰色"。
    - 深色 OFF 原本是 rgba(255,255,255,60%) 浅白轨——不够深，对比度弱。

    改为：
    - OFF：track=trans，border=pal('border')；handle=pal('text2')
    - ON ：fill=accent；border=accent；handle=#FFFFFF（白）
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _patch_indicator_colors(self.indicator)
        # 主题切换时让 Indicator 重新读最新调色板
        register_theme_widget(self)

    def refresh_theme(self):
        """浅/深切换或强调色变更时重染 Indicator。"""
        _patch_indicator_colors(self.indicator)
        self.indicator.update()


def themed_switch(text=None, parent=None):
    """工厂：构造一个 ThemedSwitch（主题感知 SwitchButton）。

    用法：``sw = theme.themed_switch()`` / ``sw = theme.themed_switch("提示文字")``
    与 SwitchButton 完全等价，但 OFF 颜色跟随主题调色板、不再像灰色进度条。
    """
    if text is None:
        sw = ThemedSwitch(parent)
    else:
        sw = ThemedSwitch(text, parent)
    return sw


def themed_line_edit(parent=None):
    """工厂：构造一个 QFluentWidgets LineEdit，自动套用 pal() 配色（无黑边）。

    替换默认 ``border-bottom: 1px solid rgba(0,0,0,100%)`` 那条显眼的灰线，
    改为统一 1px 四边描边（focus 时再变为强调色），看起来更像「白底带轮廓的
    普通输入框」而不是「底下压一根灰条的输入框」。
    """
    from qfluentwidgets import LineEdit
    le = LineEdit(parent)
    le.setStyleSheet(line_edit_qss())
    # 浅/深切换时重设样式（弱引用，自动反注册）
    register_theme_widget(le)
    le.refresh_theme = lambda: le.setStyleSheet(line_edit_qss())
    # 立即触发一次以保证第一帧就显示正确样式
    le.refresh_theme()
    return le


# ============================================================
#  卡片工厂（合并「标题 + 容器」重复参数）
# ============================================================
# ============================================================
#  文字工厂 —— 用「一个 kind 枚举」控制颜色/字号/背景
# ------------------------------------------------------------
#  调用方只传 kind + text，不接受颜色、字号、背景等任何具体样式参数。
#  浅色 / 深色两套全部由 pal() 派生，主题切换自动跟随。
#  取代了原先散落在各 view 的
#    BodyLabel("xxx").setStyleSheet("color:...") / CaptionLabel("xxx") / QLabel("xxx")
#  等十几种写法的重复硬编码。
# ============================================================
# kind 语义一览（一个枚举值控制一组已配好的样式）：
#   "title"  —— 卡片/页面大标题（12px Medium, pal('text')）
#   "field"  —— 表单字段标签（11px Regular, pal('text')），与 small_label 等价
#   "tip"    —— 提示/说明文字（11px Regular, pal('text2'), 自动换行）
#   "chrome" —— 顶栏副标题/版本号/状态文字（11px Medium, pal('text2')）
#   "label"  —— 通用回退（11px Regular, pal('text')，background:transparent）
_LABEL_KINDS = {
    "title":  {"size": "12px", "weight": "500", "color_key": "text",   "wrap": False, "bg": "transparent"},
    "field":  {"size": "11px", "weight": "400", "color_key": "text",   "wrap": False, "bg": "transparent"},
    "tip":    {"size": "11px", "weight": "400", "color_key": "text2",  "wrap": True,  "bg": "transparent"},
    "chrome": {"size": "11px", "weight": "600", "color_key": "text2",  "wrap": False, "bg": "transparent"},
    "label":  {"size": "11px", "weight": "400", "color_key": "text",   "wrap": False, "bg": "transparent"},
}


def label(kind, text, parent=None):
    """统一文字工厂。

    用法: ``theme.label("field", "面比")`` / ``theme.label("tip", "提示:xxx")`` /
          ``theme.label("chrome", "v1.0")``

    所有可调样式参数（color/size/background）都从 ``_LABEL_KINDS[kind]`` 派生，
    跟随当前主题调色板（浅/深模式 + 强调色）。调用方只能选 kind、不能传样式值，
    避免「一处黑字一处白字一处 12px 一处 14px」的散乱。
    """
    cfg = _LABEL_KINDS.get(kind) or _LABEL_KINDS["label"]
    lbl = QLabel(text, parent)
    lbl.setStyleSheet(
        f"QLabel{{font-size:{cfg['size']};font-weight:{cfg['weight']};"
        f"color:{pal(cfg['color_key'])};background:{cfg['bg']};}}"
    )
    if cfg.get("wrap"):
        lbl.setWordWrap(True)
    # 主题切换时自动跟随（弱引用，控件销毁后自动反注册）
    register_theme_widget(lbl)
    lbl.refresh_theme = (
        lambda: lbl.setStyleSheet(
            f"QLabel{{font-size:{cfg['size']};font-weight:{cfg['weight']};"
            f"color:{pal(cfg['color_key'])};background:{cfg['bg']};}}"
        )
    )
    lbl.refresh_theme()
    return lbl


def title_qss():
    """栏目标题样式（12px、Medium、显式文字色）。

    必须显式 setStyleSheet color: {pal('text')} —— 单纯依赖调色板继承在
    Windows 11 深色模式下会让 QLabel 默认渲染白字，结果放到白底卡片里就消失了。
    本工程内所有 SectionCard 标题、栏目标题、小标签统一走显式色，保证
    「浅色=黑字、深色=浅字」的可视性与跨 OS 一致性。
    """
    # 用 f-string + 「双花括号」转义 CSS 的 {}，不然 .format 会把 {font-size}
    # 当成占位符炸 KeyError。
    return (f"QLabel{{font-size:12px;font-weight:500;color:{pal('text')};"
            f"background:transparent;padding:0 0 2px 0;letter-spacing:0.4px;}}")


class SectionCard(CardWidget):
    """带轻量标题的卡片容器，统一替代 tkinter 的 LabelFrame。

    用法：
        card = SectionCard("背景设置")
        card.addWidget(some_widget)
        card.addLayout(some_layout)
        card.addSpacing(8)
    标题固定在卡片顶部，使用与 Pivot 标签同档的小字 / 显式黑/浅色样式
    （不去依赖调色板继承，避免 Windows 11 深色模式把裸 QLabel 默认成白字、
    然后白底卡片上彻底看不见）。标题样式内已显式 color: {pal('text')}，
    apply_theme 时通过 register_theme_widget 自动重染。

    同时给 CardWidget 自身的底色也做主题感知：浅色 = 白，深色 = 暗灰，
    而不是让 qfluentwidgets 自己搞出灰中灰。
    """

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.root = QVBoxLayout(self)
        # 统一卡片内边距 14px（与 CardSection / 全局规范一致）
        self.root.setContentsMargins(14, 14, 14, 14)
        self.root.setSpacing(6)
        if title:
            self.title_label = QLabel(title)
            self.title_label.setStyleSheet(title_qss())
            self.root.addWidget(self.title_label)
        else:
            self.title_label = None
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        self.root.addLayout(self.body)
        # 一次性刷底色 + 注册主题监听（浅/深切换后即时重染）
        self.refresh_theme()
        register_theme_widget(self)

    def addWidget(self, w, stretch=0):
        self.body.addWidget(w, stretch)

    def addLayout(self, l, stretch=0):
        self.body.addLayout(l, stretch)

    def addSpacing(self, s):
        self.body.addSpacing(s)

    def refresh_theme(self):
        """主题切换时：标题刷一次色，卡片本体底色跟随当前模式。

        qfluentwidgets 的 CardWidget 有自己的 QSS，会按 Fluent 主题重染，
        我们再叠一层 surface 色让"主题色变了"也能保持视觉协调。
        """
        p = pal()
        # CardWidget 底色（surface 比 surface2 略浅/亮，对比更清晰）
        self.setStyleSheet(
            f"CardWidget{{background-color:{p['surface']};"
            f"border:none;border-radius:6px;}}"
        )
        # 标题色（再次重设，盖掉 qfluentwidgets 对 FluentLabelBase 的侵入）
        if self.title_label is not None:
            self.title_label.setStyleSheet(title_qss())


# ============================================================
#  栏目标题工厂 —— 与 SectionCard 标题同档的轻量小字（顶部一排标签）
# ============================================================
def pane_title(text, parent=None):
    """顶部一排栏目标题：12px、显式黑/浅色、Medium，不抢戏，与 Pivot 标签视觉一致。

    用于 SectionCard 之外的独立小栏目标题（如「处理进度」）。
    显式 color: {pal('text')} —— 不写死、不依赖调色板继承（OS 深色会让白字消失）。
    """
    return label("title", text, parent)


def small_label(text, parent=None):
    """小字标签（11px），用于拥挤行内的字段标签。

    走统一 label("field", text) 工厂：浅色下深字、深色下浅字，背景透明。
    """
    return label("field", text, parent)


def tip_label(text, parent=None):
    """提示文字（11px），用于出血线/格式说明等小字。

    走统一 label("tip", text) 工厂：text2 色（次级提示）+ 自动换行。
    """
    return label("tip", text, parent)


# ============================================================
#  信息条（合并 messagebox 的各类提示）
# ============================================================
def info(parent, level, title, content, duration=2500):
    """统一信息条。level: 'info' | 'success' | 'warning' | 'error'。"""
    pos = InfoBarPosition.TOP
    if level == "success":
        InfoBar.success(title, content, position=pos, duration=duration, parent=parent)
    elif level == "warning":
        InfoBar.warning(title, content, position=pos, duration=duration, parent=parent)
    elif level == "error":
        InfoBar.error(title, content, position=pos, duration=duration, parent=parent)
    else:
        InfoBar.info(title, content, position=pos, duration=duration, parent=parent)


# ============================================================
#  Logo —— 强调色证件照风格图标(圆角相框 + 人像剪影)
# ============================================================
def make_logo_pixmap(size=36):
    """绘制强调色渐变背景 + 圆角白底 + 人头/肩剪影。

    设计灵感:证件照相框抽象图。整体在 size 矩形内居中绘制,scale 安全。
    渐变端点与剪影色都跟随强调色，换主题色时 Logo 同步变化。
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    pad = max(1, size // 14)         # 整体内边距
    box = size - 2 * pad
    radius = max(4, size // 5)       # 圆角

    # 1) 强调色渐变圆角矩形底 —— 主体
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(_shade(THEME_COLOR, 1.15)))  # 浅
    grad.setColorAt(1.0, QColor(_shade(THEME_COLOR, 0.85)))  # 深
    p.setBrush(QBrush(grad))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(pad, pad, box, box, radius, radius)

    # 2) 白色圆角小框(证件照相框) —— 内嵌
    inner_pad = max(2, size // 6)
    inner_size = box - 2 * inner_pad
    if inner_size > 0:
        p.setBrush(QBrush(QColor(255, 255, 255, 235)))
        p.drawRoundedRect(pad + inner_pad, pad + inner_pad,
                          inner_size, inner_size,
                          max(2, size // 9), max(2, size // 9))

        # 3) 人像剪影(头 + 肩),剪裁到相框内,深橙棕色
        # 头(圆)
        head_r = inner_size * 0.14
        head_cy = pad + inner_pad + inner_size * 0.34
        head_cx = size / 2
        p.setBrush(QBrush(QColor(_shade(THEME_COLOR, 0.5))))  # 深强调色
        p.drawEllipse(QPoint(head_cx, head_cy), head_r, head_r)

        # 肩/身体(椭圆,只在相框内可见,下半截被相框边切)
        shoulder_w = inner_size * 0.42
        shoulder_h = inner_size * 0.30
        shoulder_y = pad + inner_pad + inner_size * 0.50
        p.setBrush(QBrush(QColor(_shade(THEME_COLOR, 0.5))))
        p.drawEllipse(head_cx - shoulder_w / 2, shoulder_y,
                      shoulder_w, shoulder_h * 2)

        # 4) 相框外圈描边,半透明白,让相框边缘更精致
        pen = QPen(QColor(255, 255, 255, 90))
        pen.setWidth(max(1, size // 30))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(pad + inner_pad, pad + inner_pad,
                          inner_size, inner_size,
                          max(2, size // 9), max(2, size // 9))

    p.end()
    return pm
