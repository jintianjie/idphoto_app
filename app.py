# -*- coding: utf-8 -*-
"""
证件照制作工具 —— PyQt-Fluent-Widgets 主控制器
============================================================
职责：持有全部业务状态、构建窗口 / 导航 / 内容区 / 状态栏，并把用户操作
分发给后端模块（inference / photo_processor / layout_engine / image_utils）。

UI 与代码分离：
  * 本文件只做「状态 + 逻辑 + 信号接线」，不直接画控件；
  * 所有控件长相 / 布局都封装在 ui/ 包（views + widgets + theme）；
  * 后端模块（推理 / 处理 / 排版）保持原样复用，不改动。
"""
import os
import sys
import threading
import queue
import functools
import traceback

# 说明：numpy / cv2 保留在顶层导入。实测即便改为函数内按需导入也省不掉——
# photo_processor / image_utils 在顶层就要 import 它们，本文件终究会被
# 间接拖入，延迟只是把开销换个位置、还平添散落的 import 语句。
# 真正值得延迟的是 onnxruntime（见 inference.py），它确实能省下约 46ms。
import numpy as np
import cv2

# 确保能 import 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSplitter,
    QStackedWidget, QMessageBox, QFileDialog, QApplication, QSizePolicy, QDialog,
)
from PySide6.QtCore import Qt, QTimer, QObject, QEvent, QUrl
from PySide6.QtGui import QIcon, QPixmap, QImage, QPainter, QDesktopServices
from PySide6.QtPrintSupport import QPrinter, QPrintDialog, QPrinterInfo

from qfluentwidgets import Pivot, FluentIcon

from styles import WINDOW_WIDTH, WINDOW_HEIGHT, IMAGE_EXTENSIONS  # 复用尺寸/扩展名常量
from inference import EngineManager
from photo_processor import PhotoProcessor, FaceError
from layout_engine import LayoutEngine, LayoutItem
from color_utils import hex_to_rgb_tuple
from image_utils import (
    add_background_to_image, save_image_with_dpi, save_image_to_jpeg_kb,
)

from ui import theme, widgets
from ui.theme import make_logo_pixmap
from ui.preview_pane import PreviewPane
from ui.process_view import ProcessView
from ui.layout_view import LayoutView
from ui.queue_pane import ProcessQueuePane
from ui.layout_left_pane import LayoutLeftPane
from ui.model_download_dialog import ModelDownloadDialog
from ui.mirror_dialog import MirrorManagerDialog

from core.settings_store import SettingsStore, DEFAULT_SETTINGS, get_default_settings_path
from core.mirror_manager import MirrorManager

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_DIR = os.path.join(_BASE_DIR, "model")

# ============================================================
#  主线程回调调度器
# ------------------------------------------------------------
#  后台 worker 线程不能直接用 QTimer.singleShot 调度 UI 回调：
#  QTimer.singleShot 的 timer 依附于"调用它的线程"，而 worker 线程
#  没有运行事件循环，回调永远不会触发（实测会静默失效，导致处理完成后
#  UI 不更新、看起来"卡住没反应"）。
#  这里用一个在主线程运行的 QTimer 轮询线程安全队列，worker 线程只往
#  队列 put 回调，由主线程取出执行，彻底规避该陷阱。
# ============================================================
class MainThreadInvoker(QObject):
    def __init__(self, interval_ms=20):
        super().__init__()
        self._queue = queue.Queue()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    def invoke_later(self, func):
        self._queue.put(func)

    def stop(self):
        """窗口关闭时调用：停掉轮询定时器并清空待执行回调，避免线程/定时器残留。"""
        self._timer.stop()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _drain(self):
        while True:
            try:
                func = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                func()
            except Exception:
                traceback.print_exc()

IMAGE_FILE_FILTER = " ".join(ext for _label, ext in IMAGE_EXTENSIONS if ext.startswith("*."))


# ============================================================
#  右侧「缩略图」区：结果缩略图 / 排版页缩略图 切换
# ============================================================
class RightThumbArea(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        card = theme.SectionCard("缩略图")
        self.result_list = widgets.ThumbnailList(
            thumb_size=80, horizontal=True, name_max_len=14)
        self.page_list = widgets.ThumbnailList(
            thumb_size=80, horizontal=True, name_max_len=14)
        self.result_list.itemSelected.connect(lambda i: self.c.on_result_select(i))
        self.result_list.itemDoubleClickedSig.connect(lambda i: self.c.on_result_zoom(i))
        self.page_list.itemSelected.connect(lambda i: self.c.on_layout_page_select(i))
        self.page_list.itemDoubleClickedSig.connect(lambda i: self.c.on_layout_page_select(i))

        self.result_ph = theme.label("tip", "（批量 / 单张处理后，此处显示处理后的证件照缩略图）")
        self.result_ph.setAlignment(Qt.AlignCenter)
        self.page_ph = theme.label("tip", "（排版后，此处显示页面缩略图）")
        self.page_ph.setAlignment(Qt.AlignCenter)

        self.stack = QStackedWidget()
        rp = QWidget(); rpv = QVBoxLayout(rp); rpv.setContentsMargins(0, 0, 0, 0)
        rpv.addWidget(self.result_list, 1); rpv.addWidget(self.result_ph, 1)
        pp = QWidget(); ppv = QVBoxLayout(pp); ppv.setContentsMargins(0, 0, 0, 0)
        ppv.addWidget(self.page_list, 1); ppv.addWidget(self.page_ph, 1)
        self.stack.addWidget(rp); self.stack.addWidget(pp)
        card.addWidget(self.stack, 1)
        v.addWidget(card)

    def set_mode(self, mode):
        if mode == "result":
            self.stack.setCurrentIndex(0)
        else:
            self.stack.setCurrentIndex(1)

    def refresh_results(self, items):
        """items: [(name, image), ...]"""
        if not items:
            self.result_list.hide()
            self.result_ph.show()
            return
        self.result_ph.hide()
        self.result_list.show()
        self.result_list.set_items(items)

    def refresh_pages(self, pages):
        if not pages:
            self.page_list.hide()
            self.page_ph.show()
            return
        self.page_ph.hide()
        self.page_list.show()
        items = [(f"第 {i + 1} 页", img) for i, img in enumerate(pages)]
        self.page_list.set_items(items)


# ============================================================
#  主控制器
# ============================================================
class IDPhotoApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("证件照制作工具")

        # ===== 状态数据 =====
        self.batch_images = []
        self.current_image_idx = -1
        self.matting_image = None
        self.standard_image = None
        self.hd_image = None
        self.standard_matting = None
        self.layout_pages = []
        self.layout_queue = []
        self.processed_results = {}
        self.ready_photos = []
        self.selected_ready_idx = -1
        self.current_page = None
        self.processing = False
        self._stop_batch = False
        self.preview_view = "auto"
        self._gallery_selected_idx = None
        self._result_thumb_indices = []

        # 主线程回调调度器（必须在主线程构造，其 QTimer 才能驱动 UI 回调）
        self._invoker = MainThreadInvoker()

        # ===== 引擎 =====
        self.model_dir = DEFAULT_MODEL_DIR
        self.model_available = self._check_models(self.model_dir)
        self.engine_manager = EngineManager(self.model_dir)
        self.photo_processor = PhotoProcessor(self.engine_manager)

        # ===== 设置 + 镜像源（本地 JSON） =====
        self._settings = SettingsStore(get_default_settings_path(_BASE_DIR), DEFAULT_SETTINGS)
        self._mirror_mgr = MirrorManager(_BASE_DIR)

        # 用持久化设置（主题模式 / 强调色 / 默认模型）点亮界面，必须在构建控件前调用
        theme.apply_theme(self._settings)
        # 同步 settings 里的默认模型到 engine_manager（设置里改的抠图/人脸模型生效）
        try:
            self.engine_manager.set_matting_model(
                self._settings.get("processing.default_matting_model",
                                   "modnet_photographic_portrait_matting"))
        except Exception:
            traceback.print_exc()
        try:
            self.engine_manager.set_face_model(
                self._settings.get("processing.default_face_model",
                                   "retinaface-resnet50"))
        except Exception:
            traceback.print_exc()

        # ===== 构建 UI =====
        self._build_header()   # 顶部：logo + 标题 + 模型状态
        self._apply_app_identity()  # 用持久化名称/图标点亮顶栏与窗口
        self._build_nav()      # 顶部下方：证件照处理 / 排版打印（独立一行）
        self._build_content()
        self._sync_engine_to_available_models()  # 让引擎加载“本地存在”的默认模型
        self._build_statusbar()
        self._setup_drop_targets()   # 支持拖拽图片到软件任意位置

        # 注册主题变更监听：设置里切浅/深或主题色后，重染顶部 chrome
        # （Fluent 原生控件 + 继承调色板的文本标签由 apply_theme 自动跟随）
        theme.add_theme_listener(self._on_theme_changed)

        self._show_page("process")
        # 启动只刷新「内嵌状态」(红/绿点 + 处理页缺失红字)，不再弹缺失询问框。
        QTimer.singleShot(150, self._update_model_status)
        QTimer.singleShot(200, self.start_refresh_printers)
        self._update_model_status()

    # ----------------------------------------------------------
    #  模型检查
    # ----------------------------------------------------------
    def _check_models(self, model_dir):
        """模型是否可用：每个类别（抠图 / 人脸）至少存在一个本地模型即可使用，
        不要求下载全部模型。具体哪几个缺失由 UI 禁用对应选项呈现。"""
        try:
            from model_downloader import list_missing_models, BUILTIN_MANIFEST
            miss = {m["name"] for m in list_missing_models(model_dir, BUILTIN_MANIFEST)}
        except Exception:
            miss = set()
        try:
            from styles import MATTING_MODELS, FACE_MODELS
            matting_present = any(k not in miss for k, _ in MATTING_MODELS)
            face_present = any(k not in miss for k, _ in FACE_MODELS)
        except Exception:
            matting_present = face_present = False
        return matting_present and face_present

    def _sync_engine_to_available_models(self):
        """让引擎加载“本地存在”的默认模型，而不是可能缺失的 settings 配置项。

        settings 里若记录了某个缺失模型作为默认，引擎加载会失败；这里回退到
        该类别第一个“存在”的模型，并写回 settings，保证 引擎 / UI 选择 / 配置 三者一致。
        即使全部缺失（两类别都无可用模型）也安全降级：引擎保持空、UI 全部禁用、
        处理时由 _prepare_process 的 model_available 拦截。
        """
        try:
            from model_downloader import list_missing_models, BUILTIN_MANIFEST
            from styles import MATTING_MODELS, FACE_MODELS
            miss = {m["name"] for m in list_missing_models(self.model_dir, BUILTIN_MANIFEST)}
        except Exception:
            return

        def pick(cfg_key, models):
            cfg = self._settings.get(cfg_key, models[0][0] if models else None)
            if cfg and cfg not in miss and any(cfg == k for k, _ in models):
                return cfg
            for k, _ in models:
                if k not in miss:
                    return k
            return cfg

        mdl = pick("processing.default_matting_model", MATTING_MODELS)
        fdl = pick("processing.default_face_model", FACE_MODELS)
        try:
            self.engine_manager.set_matting_model(mdl)
        except Exception:
            traceback.print_exc()
        try:
            self.engine_manager.set_face_model(fdl)
        except Exception:
            traceback.print_exc()
        self._settings.set("processing.default_matting_model", mdl)
        self._settings.set("processing.default_face_model", fdl)

    # ----------------------------------------------------------
    #  顶部头部（独占一行）：logo + 标题双行 + 状态卡 + 版本号
    # ----------------------------------------------------------
    def _build_header(self):
        from PySide6.QtWidgets import QFrame
        bar = QWidget()
        bar.setObjectName("headerBar")   # 供 ID 选择器限定背景只染顶栏自身
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(12)

        # ============ 左侧：logo + 标题双行 ============
        left = QWidget()
        lv = QHBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)

        # 新版证件照图标
        self._hdr_logo = QLabel()
        self._hdr_logo.setPixmap(make_logo_pixmap(36))
        self._hdr_logo.setFixedSize(36, 36)
        lv.addWidget(self._hdr_logo, 0, Qt.AlignVCenter)

        # 双行标题区(应用名 + 副标题)
        title_box = QWidget()
        tb = QVBoxLayout(title_box)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(0)

        # 走统一 label("title", ...) 工厂：14px Bold，背景 transparent，
        # 跟 SectionCard 标题、tab 标签同档的视觉权重。
        # 字号用 15px（顶栏稍大些），通过 lbl.style() 不被 QSS 覆盖即可。
        self._hdr_app_name = theme.label("title", "证件照制作工具")
        # 顶栏标题字号比 SectionCard 标题（12px）略大：直接调整 QSS size。
        self._hdr_app_name.setStyleSheet(self._hdr_app_name.styleSheet().replace(
            "font-size:12px", "font-size:15px").replace(
            "font-weight:500", "font-weight:700"))
        tb.addWidget(self._hdr_app_name)

        # 副标题走 label("chrome", ...) 工厂：11px Medium + pal('text2')。
        self._hdr_subtitle = theme.label("chrome", "ID Photo Studio  ·  本地离线处理")
        self._hdr_subtitle.setStyleSheet(
            self._hdr_subtitle.styleSheet()
            + "letter-spacing:0.3px;margin-top:2px;"
        )
        tb.addWidget(self._hdr_subtitle)

        lv.addWidget(title_box, 0, Qt.AlignVCenter)
        left.setFixedHeight(40)
        h.addWidget(left, 0, Qt.AlignVCenter)

        h.addSpacing(16)

        # ============ 中间分隔细线 ============
        self._hdr_sep = QFrame()
        self._hdr_sep.setObjectName("hdrSep")
        self._hdr_sep.setFrameShape(QFrame.VLine)
        self._hdr_sep.setFrameShadow(QFrame.Plain)
        # 分隔线只染自身（ID 选择器限定，无选择器声明会渗给子孙）
        self._hdr_sep.setStyleSheet(
            f"#hdrSep{{color:{theme.pal('border')};"
            f"background-color:{theme.pal('border')};}}")
        self._hdr_sep.setFixedHeight(28)
        h.addWidget(self._hdr_sep, 0, Qt.AlignVCenter)

        h.addStretch(1)

        # ============ 右侧：状态卡(带圆角背景) ============
        self._hdr_status_card = QWidget()
        self._hdr_status_card.setObjectName("hdrStatusCard")
        # 状态卡只染自身（ID 选择器限定；无选择器声明会渗给卡内状态文字）
        self._hdr_status_card.setStyleSheet(
            f"#hdrStatusCard{{background-color:{theme.pal('surface2')};"
            f"border:1px solid {theme.pal('border')};border-radius:6px;}}"
        )
        sc = QHBoxLayout(self._hdr_status_card)
        sc.setContentsMargins(10, 5, 12, 5)
        sc.setSpacing(8)

        self.model_dot = widgets.StatusDot("#22C55E", 9)
        sc.addWidget(self.model_dot, 0, Qt.AlignVCenter)

        # 状态文本两行(模型名 + 状态)
        st_box = QWidget()
        sb = QVBoxLayout(st_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(0)

        # 模型名走 label("chrome", ...) 工厂
        self._hdr_st_label = theme.label("chrome", "MODNet")
        self._hdr_st_label.setStyleSheet(
            self._hdr_st_label.styleSheet() + "letter-spacing:0.3px;"
        )
        sb.addWidget(self._hdr_st_label)

        # 模型状态文字（成功/失败色由本控件单独决定，不走 chrome 调色板）
        self.model_status_label = theme.label("chrome", "已就绪")
        self.model_status_label.setStyleSheet(
            self.model_status_label.styleSheet()
            + "color:#22C55E;"
        )
        sb.addWidget(self.model_status_label)

        sc.addWidget(st_box, 0, Qt.AlignVCenter)
        h.addWidget(self._hdr_status_card, 0, Qt.AlignVCenter)

        h.addSpacing(8)

        # 版本号小角标（chrome 工厂 + 容器色背景）
        self._hdr_ver = theme.label("chrome", "v1.0")
        self._hdr_ver.setStyleSheet(
            self._hdr_ver.styleSheet()
            + f"background-color:{theme.pal('bg')};"
              f"border:1px solid {theme.pal('border')};"
              f"border-radius:4px;padding:2px 6px;"
        )
        h.addWidget(self._hdr_ver, 0, Qt.AlignVCenter)

        self._header = bar
        # 顶栏背景跟随主题。
        # ⚠️ 必须用 objectName（ID）选择器限定：Qt 里不带选择器的声明
        #（如 "background-color:X;"）会作用于自身**及全部子孙控件**，
        # 把顶栏底色渗给所有子 QLabel（表现就是「标签下出现一块底色」）。
        # 实测复现见 2026-09-03 诊断（_diag_leak.py）。
        self._header.setStyleSheet(
            f"#headerBar{{background-color:{theme.pal('surface')};}}")

    # ----------------------------------------------------------
    #  软件名称 / 图标（来自设置，保存即生效，并成为下次启动默认值）
    # ----------------------------------------------------------
    def _apply_app_identity(self):
        """应用「软件名称 + 图标」：窗口标题、顶栏名称、窗口/顶栏图标。

        自定义图标路径失效（文件被删/移动）时自动回退到默认 logo。
        """
        name = (self._settings.get("appearance.app_name", "证件照制作工具")
                or "证件照制作工具")
        self.setWindowTitle(name)
        try:
            self._hdr_app_name.setText(name)
        except Exception:
            pass

        icon_path = self._settings.get("appearance.app_icon", "") or ""
        pm = QPixmap()
        if icon_path and os.path.isfile(icon_path):
            pm = QPixmap(icon_path)
        if pm.isNull():
            pm = make_logo_pixmap(36)
        try:
            self._hdr_logo.setPixmap(
                pm.scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            pass
        try:
            self.setWindowIcon(QIcon(pm))
        except Exception:
            pass

    # ----------------------------------------------------------
    #  主题样式热刷新（设置里改 浅/深 或 主题色 后即时反映到顶部 chrome）
    # ----------------------------------------------------------
    def _refresh_theme_styles(self):
        """主题切换后刷新顶部 chrome 的自定义 QSS。

        顶栏的标题/副标题/状态/版本号都来自统一 label(kind, ...) 工厂，
        它们已自动注册主题监听（弱引用），切浅/深时会被 _notify_theme_changed
        自动重染。这里只需要重设那些"由顶栏/中央容器自己决定"的非 label 样式：
        顶栏背景、nav 背景、central 背景、分隔线、状态卡容器、版本号容器的
        background（label 工厂只控制 label 内部色，控制不到 label 的容器）。

        注意：所有 setStyleSheet 都只能写 self 自身样式（不能写 "QWidget{...}" 宽选择器），
        否则会把 surface 底色传染给所有子 QWidget（QLabel），造成「标签下方出现一块
        surface 底色」的视觉污染。这是上一次迭代被截图明确指出的 bug。
        """
        p = theme.pal()
        # 容器背景：一律用 objectName（ID）选择器限定自身。
        # ⚠️ Qt 规则：不带选择器的声明作用于自身及全部子孙控件，
        # 会把底色渗给子 QLabel（=「标签下面有一块底色」的真正根因）。
        self._hdr_sep.setStyleSheet(
            f"#hdrSep{{color:{p['border']};background-color:{p['border']};}}")
        self._hdr_status_card.setStyleSheet(
            f"#hdrStatusCard{{background-color:{p['surface2']};"
            f"border:1px solid {p['border']};border-radius:6px;}}")
        self._hdr_ver.setStyleSheet(
            self._hdr_ver.styleSheet().split(";background-color")[0]
            + f";background-color:{p['bg']};"
              f"border:1px solid {p['border']};"
              f"border-radius:4px;padding:2px 6px;"
        )
        # 主窗口背景跟随浅/深模式
        try:
            self._central.setStyleSheet(
                f"#centralArea{{background-color:{p['bg']};}}")
            self._header.setStyleSheet(
                f"#headerBar{{background-color:{p['surface']};}}")
            self._nav_widget.setStyleSheet(
                f"#navBar{{background-color:{p['surface']};}}")
        except Exception:
            pass

    def _on_theme_changed(self):
        """apply_theme 触发：重染顶部 chrome 的自定义 QSS。

        Fluent 原生控件、以及继承 Fluent 调色板文字色的文本标签
        （small_label / tip_label / SectionCard 标题）已由 apply_theme
        自动跟随浅/深，无需逐个处理；这里只补刀标题栏等手写 QSS 部分。
        """
        try:
            self._refresh_theme_styles()
        except Exception:
            pass

    # ----------------------------------------------------------
    #  顶部导航（独占一行，独立于 header 下方）
    # ----------------------------------------------------------
    def _build_nav(self):
        self._nav_widget = QWidget()
        self._nav_widget.setObjectName("navBar")
        # nav 自身背景：必须用 ID 选择器限定（无选择器声明会渗给全部子孙）
        self._nav_widget.setStyleSheet(
            f"#navBar{{background-color:{theme.pal('surface')};}}")
        h = QHBoxLayout(self._nav_widget)
        h.setContentsMargins(12, 4, 12, 6)
        h.setSpacing(8)
        self.top_nav = Pivot()
        for key, text in [("process", "证件照处理"), ("layout", "排版打印")]:
            self.top_nav.addItem(key, text)
        # Pivot 的 currentItemChanged 信号才携带 routeKey；onClick 只收到 bool，故不能用。
        self.top_nav.currentItemChanged.connect(self._show_page)
        h.addWidget(self.top_nav, 0, Qt.AlignVCenter)
        h.addStretch(1)

        # 设置按钮（右侧固定位置，紧凑风）
        from qfluentwidgets import PushButton as _PB, FluentIcon as _FI
        self.settings_btn = _PB(_FI.SETTING.qicon(), "设置")
        self.settings_btn.setToolTip("主题、外观与各项默认设置")
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        h.addWidget(self.settings_btn, 0, Qt.AlignVCenter)

        # 开源按钮（位于「设置」右侧，点击跳转本项目开源仓库）
        self.open_source_btn = _PB(_FI.GITHUB.qicon(), "开源")
        self.open_source_btn.setToolTip("查看本项目开源地址")
        self.open_source_btn.clicked.connect(self._open_source_link)
        h.addWidget(self.open_source_btn, 0, Qt.AlignVCenter)

    # ----------------------------------------------------------
    #  内容区 (3 列): 左 = 队列 / 成品+排版 ｜ 中 = 预览画布 (最大) ｜ 右 = 参数面板
    # ----------------------------------------------------------
    def _build_content(self):
        self._central = QWidget()
        self._central.setObjectName("centralArea")
        # central 自身背景：必须用 ID 选择器限定（无选择器声明会渗给
        # 全部子孙 QLabel，造成卡片内标签出现一块 bg 底色 —— 2026-09-03 实测）
        self._central.setStyleSheet(
            f"#centralArea{{background-color:{theme.pal('bg')};}}")
        root = QVBoxLayout(self._central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header)    # 行 1：logo + 标题 + 模型状态
        root.addWidget(self._nav_widget)  # 行 2：顶部 Pivot 导航

        splitter = QSplitter(Qt.Horizontal)

        # ===== 左列：根据当前页面在「处理队列 / 成品 + 排版」之间切换 =====
        self.left_stack = QStackedWidget()
        self.queue_pane = ProcessQueuePane(self)
        self.layout_pane = LayoutLeftPane(self)
        self.left_stack.addWidget(self.queue_pane)    # idx 0: 处理队列
        self.left_stack.addWidget(self.layout_pane)   # idx 1: 成品 + 排版
        self.left_stack.setMinimumWidth(220)
        self.left_stack.setMaximumWidth(280)
        splitter.addWidget(self.left_stack)

        # ===== 中列：预览画布 (最大) + 下方缩略图条 =====
        self.preview_pane = PreviewPane()
        self.preview_pane.set_view_changed_callback(self._on_view_changed)
        self.right_thumb = RightThumbArea(self)

        middle = QWidget()
        mv = QVBoxLayout(middle)
        mv.setContentsMargins(8, 8, 8, 8)
        mv.setSpacing(8)
        mv.addWidget(self.preview_pane, 1)
        # 缩略图条固定 120-160px, 仅显示一行
        self.right_thumb.setMaximumHeight(160)
        self.right_thumb.setMinimumHeight(120)
        mv.addWidget(self.right_thumb, 0)
        splitter.addWidget(middle)

        # ===== 右列：根据当前页面在「处理参数面板 / 排版参数面板」之间切换 =====
        self.page_stack = QStackedWidget()
        self.pages = {
            "process": ProcessView(self),
            "layout": LayoutView(self),
        }
        self.page_stack.addWidget(self.pages["process"])
        self.page_stack.addWidget(self.pages["layout"])
        self.page_stack.setMinimumWidth(300)
        self.page_stack.setMaximumWidth(420)
        splitter.addWidget(self.page_stack)

        # 拉伸因子: 左小 | 中最大 | 右中
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        # 初始宽度: 左 240 / 中 自适应 / 右 320
        splitter.setSizes([240, 900, 320])
        # 防止左/右被拖到完全 0
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, False)

        root.addWidget(splitter, 1)

        self.setCentralWidget(self._central)

        # 屏幕自适应
        screen = QApplication.primaryScreen()
        sw, sh = (screen.availableGeometry().width(),
                  screen.availableGeometry().height()) if screen else (WINDOW_WIDTH, WINDOW_HEIGHT)
        w = min(WINDOW_WIDTH, max(1050, sw - 40))
        h = min(WINDOW_HEIGHT, max(700, sh - 80))
        self.resize(w, h)
        self.setMinimumSize(1000, 700)

    # ----------------------------------------------------------
    #  状态栏
    # ----------------------------------------------------------
    def _build_statusbar(self):
        bar = self.statusBar()
        # 状态栏文字走统一 label("field", ...) 工厂，背景 transparent、字色 pal('text')
        self.status_label = theme.label("field", "")
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(self.status_label, 1)
        self.progress_label = theme.label("field", "")
        bar.addPermanentWidget(self.progress_label)

    # ----------------------------------------------------------
    #  页面路由 (3 列布局: 同时切换 左stack / 右stack / 顶部 Pivot / 缩略图模式)
    # ----------------------------------------------------------
    def _show_page(self, page_id):
        if page_id not in self.pages:
            return
        self.current_page = page_id
        # 右列: 切换处理参数 / 排版参数
        self.page_stack.setCurrentWidget(self.pages[page_id])
        # 左列: 切换队列 / 成品+排版
        self.left_stack.setCurrentIndex(0 if page_id == "process" else 1)
        self.left_stack.setVisible(True)
        # 顶部 Pivot 高亮
        self.top_nav.setCurrentItem(page_id)
        # 下方缩略图条切换模式
        self.right_thumb.set_mode("result" if page_id == "process" else "layout")
        if page_id == "process":
            self._refresh_result_thumbnails()
        else:
            self.right_thumb.refresh_pages(self.layout_pages)
        # 切页时把预览视图重置为「自动」，让预览跟随当前页面
        # （处理页→证件照，排版页→成品照/排版图），
        # 否则会停在上一页手动选的视图上，看起来像"切不动"。
        self.preview_pane.set_view("auto")
        # 同步刷新预览
        self._refresh_right_previews()

    def _on_view_changed(self, view):
        self.preview_view = view
        self._refresh_right_previews()

    # ----------------------------------------------------------
    #  状态栏辅助
    # ----------------------------------------------------------
    def _set_status(self, text):
        max_chars = 72
        s = str(text or "")
        if len(s) > max_chars:
            cut = max(s.rfind("\\"), s.rfind("/"))
            s = s[:max_chars] + ("…" + s[cut:] if cut > 0 else "")
        self.status_label.setText(s)

    def _set_progress(self, text):
        self.progress_label.setText(text)

    def _update_model_status(self, in_use=False):
        color = "#22C55E" if self.model_available else "#EF4444"
        self.model_dot.set_color(color)
        if not self.model_available:
            text, text_color = "无可用模型", "#EF4444"
        elif in_use:
            text, text_color = "使用中…", "#F97316"
        else:
            text, text_color = "已就绪（可用现有模型）", "#22C55E"
        self.model_status_label.setText(text)
        # 只改色（不再重设 font-size/weight，由 label 工厂保证；也保留 background:transparent）
        cur = self.model_status_label.styleSheet()
        # 用正则就地改 color: 段（保持其它样式不动）
        import re as _re
        cur = _re.sub(r"color:\s*#[0-9A-Fa-f]+", f"color:{text_color}", cur)
        if "color:" not in cur:
            cur += f"color:{text_color};"
        self.model_status_label.setStyleSheet(cur)
        # 同步模型页内嵌状态；把「缺失列表」返回给调用方复用。
        # 原实现让启动阶段对模型目录扫描了 3 次（init 一次 + 延迟任务里两次），
        # 这里改为扫一次、结果共享。
        miss = []
        try:
            from model_downloader import list_missing_models, BUILTIN_MANIFEST
            miss = list_missing_models(self.model_dir, BUILTIN_MANIFEST)
            inline = ("✓ 所有模型已就绪" if not miss
                      else "⚠ 部分模型缺失，仍可使用现有模型：" + ", ".join(
                          m["info"].get("filename", m["name"]) for m in miss[:3])
                      + (f" 等 {len(miss)} 个" if len(miss) > 3 else ""))
            # 「去设置下载」按钮仅在“真正无法使用”（无可用模型）时出现，
            # 部分缺失时不强制弹下载，符合“不必下载全部即可使用”。
            self.pages["process"].set_model_status(
                inline, missing=not self.model_available)
        except Exception:
            pass
        return miss

    def _safe_after(self, ms, func, *args):
        """主线程安全调度。

        注意：不能在此直接调 QTimer.singleShot —— 本方法会被后台 worker 线程调用，
        而 QTimer.singleShot 的定时器依附于调用线程，worker 线程无事件循环会导致
        回调永不触发。统一经主线程的 MainThreadInvoker 队列调度。
        """
        if args:
            func = functools.partial(func, *args)
        self._invoker.invoke_later(func)

    # ----------------------------------------------------------
    #  属性
    # ----------------------------------------------------------
    @property
    def original_image(self):
        if 0 <= self.current_image_idx < len(self.batch_images):
            return self.batch_images[self.current_image_idx].get("image")
        return None

    @property
    def original_file_path(self):
        if 0 <= self.current_image_idx < len(self.batch_images):
            return self.batch_images[self.current_image_idx].get("path")
        return None

    # ----------------------------------------------------------
    #  队列 UI
    # ----------------------------------------------------------
    def _rebuild_queue_ui(self):
        items = [(it["name"], it["image"]) for it in self.batch_images]
        self.queue_pane.update_queue(items)

    def _name_at(self, idx):
        if 0 <= idx < len(self.batch_images):
            return self.batch_images[idx]["name"]
        return f"结果 {idx + 1}"

    # ----------------------------------------------------------
    #  图片导入 / 选择
    # ----------------------------------------------------------
    def on_add_images(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片（可多选）", "", f"图片文件 ({IMAGE_FILE_FILTER})")
        if not file_paths:
            return
        self._add_image_paths(file_paths)

    def _add_image_paths(self, file_paths, auto_select_new=False):
        """把一组文件路径导入队列（去重 + 解码失败跳过）。

        供「文件对话框」与「拖拽图片到窗口」两条入口共用，避免逻辑重复。
        在主线程同步执行（与原来 on_add_images 行为一致）。
        auto_select_new=True 时（拖拽场景），导入后自动选中第一张新图，
        让右侧预览立即跟随切换；文件对话框场景保持旧行为（仅当无任何选中时才选）。
        """
        if not file_paths:
            return
        old_len = len(self.batch_images)
        added = 0
        for fp in file_paths:
            if any(it.get("path") == fp for it in self.batch_images):
                continue
            image = self._load_image(fp)
            if image is None:
                continue
            self.batch_images.append({
                "name": os.path.basename(fp), "path": fp, "image": image,
            })
            added += 1
        if added:
            self._rebuild_queue_ui()
            if auto_select_new:
                # 拖入的图片自动成为选中项 → 预览自动切换过去
                self.on_select_queue_image(old_len)
            elif self.current_image_idx < 0 and self.batch_images:
                self.on_select_queue_image(0)
            self._set_status(f"已导入 {added} 张，共 {len(self.batch_images)} 张")
            self._refresh_right_previews()

    def _load_image(self, file_path):
        """读取图片，失败返回 None（静默，由调用方统一提示）。"""
        try:
            image = cv2.imdecode(np.fromfile(file_path, dtype=np.uint8),
                                 cv2.IMREAD_COLOR)
            if image is None:
                image = cv2.imread(file_path, cv2.IMREAD_COLOR)
            return image
        except Exception:
            return None

    # ----------------------------------------------------------
    #  拖拽图片到窗口（任意位置可接收）
    # ----------------------------------------------------------
    def _setup_drop_targets(self):
        """让整个软件窗口都能接收图片拖放，且对其它控件零侵入。

        采用「在 QApplication 层装一个全局事件过滤器」的方案：
        - 应用级过滤器能在事件到达目标控件之前截获它，因此**不需要**给每个子控件
          setAcceptDrops(True)（那样会改到预览标签 / 缩略图列表等的事件行为，
          曾导致预览不自动切换的回归）。
        - 只处理落在主窗口范围内的拖放（独立对话框不拦截），对非图片拖拽一律放行。
        """
        self.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)

    def _extract_image_paths(self, mime):
        """从拖放的 QMimeData 中提取本地图片文件路径（按扩展名过滤）。"""
        # IMAGE_EXTENSIONS 形如 ("PNG 图片", "*.png")，统一去掉可能的 "*" 前缀，
        # 与 os.path.splitext 产出的 ".png" 对齐。
        exts = set()
        for _, e in IMAGE_EXTENSIONS:
            e = e.lower().lstrip("*")
            if e:
                exts.add(e)
        out = []
        for u in mime.urls():
            if not u.isLocalFile():
                continue
            p = u.toLocalFile()
            if os.path.isfile(p) and os.path.splitext(p)[1].lower() in exts:
                out.append(p)
        return out

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            # 仅处理主窗口范围内的拖放（独立对话框不拦截，避免误伤其原有行为）
            if not (obj is self or self.isAncestorOf(obj)):
                return super().eventFilter(obj, event)
            mime = event.mimeData()
            if mime is not None and mime.hasUrls():
                paths = self._extract_image_paths(mime)
                if paths:
                    event.acceptProposedAction()
                    if et == QEvent.Drop:
                        self._add_image_paths(paths, auto_select_new=True)
                    return True
            # 非图片拖拽（如文本）→ 不拦截
            return False
        return super().eventFilter(obj, event)

    def on_remove_queue_image(self, idx):
        if not (0 <= idx < len(self.batch_images)):
            return
        name = self.batch_images[idx]["name"]
        self.batch_images.pop(idx)
        self.processed_results.pop(idx, None)
        if self.current_image_idx >= len(self.batch_images):
            self.current_image_idx = len(self.batch_images) - 1
        self._rebuild_queue_ui()
        self._refresh_result_thumbnails()
        self._refresh_right_previews()
        self._set_status(f"已移除「{name}」，剩 {len(self.batch_images)} 张")

    def on_clear_all_images(self):
        if not self.batch_images:
            return
        self.batch_images.clear()
        self.processed_results.clear()
        self.current_image_idx = -1
        self._rebuild_queue_ui()
        self._refresh_result_thumbnails()
        self._refresh_right_previews()
        self._set_status("已清空图片队列")

    def _clear_results(self):
        self.processed_results.clear()
        self.matting_image = self.standard_image = self.hd_image = None

    def on_select_queue_image(self, idx):
        if not (0 <= idx < len(self.batch_images)):
            return
        self.current_image_idx = idx
        # 同步预览目标：_gallery_selected_idx 决定"证件照"视图显示哪张结果，
        # 不在这里同步的话，点队列缩略图时预览会一直停在上一次处理完那张。
        self._gallery_selected_idx = idx
        self._select_image_at(idx)

    def _select_image_at(self, idx):
        self.queue_pane.select_queue_index(idx)
        self.queue_pane.set_item_active(idx)
        self._refresh_right_previews()

    def on_queue_zoom(self, idx):
        if not (0 <= idx < len(self.batch_images)):
            return
        self.current_image_idx = idx
        self._gallery_selected_idx = idx
        self._refresh_right_previews()

    # ----------------------------------------------------------
    #  模型切换
    # ----------------------------------------------------------
    def on_model_changed(self, model_name):
        try:
            self.engine_manager.set_matting_model(model_name)
        except Exception:
            traceback.print_exc()
        # 记住用户选择，保证下次启动引擎/UI/配置一致
        try:
            self._settings.set("processing.default_matting_model", model_name)
        except Exception:
            pass
        self._update_model_status()

    def on_face_model_changed(self, model_name):
        try:
            self.engine_manager.set_face_model(model_name)
        except Exception:
            traceback.print_exc()
        try:
            self._settings.set("processing.default_face_model", model_name)
        except Exception:
            pass

    # ----------------------------------------------------------
    #  处理参数收集
    # ----------------------------------------------------------
    def _get_process_params(self):
        params = self.pages["process"].get_advanced_params()
        if params is None:
            return None
        params["process_mode"] = self.pages["process"].get_process_mode()
        params["beauty"] = self.pages["process"].get_beauty_params()
        params["watermark"] = self.pages["process"].get_watermark_params()
        return params

    # ----------------------------------------------------------
    #  处理（单张 / 批量）
    # ----------------------------------------------------------
    def _prepare_process(self, require_selection):
        """单张 / 批量处理共用的前置校验：返回参数 dict，失败返回 None。

        require_selection=True  需要当前选中一张图（单张处理）
        require_selection=False 只要队列非空即可（批量处理）
        """
        if (self.original_image is None) if require_selection \
                else (not self.batch_images):
            theme.info(self, "warning", "提示", "请先上传图片。")
            self._show_page("process")
            return None
        if not self.model_available:
            theme.info(self, "error", "模型未找到",
                       f"请将模型文件放在以下目录:\n{DEFAULT_MODEL_DIR}")
            return None
        return self._get_process_params()

    def on_process(self):
        params = self._prepare_process(require_selection=True)
        if params is None:
            return
        self._show_page("process")
        self._start_processing("处理中...")
        self.queue_pane.set_progress(10, "正在加载模型并推理...")
        self.queue_pane.set_item_active(self.current_image_idx)
        self.queue_pane.scroll_to_item(self.current_image_idx)
        # 必须在主线程读取 UI 参数，再传入子线程（FluentWidgets 控件跨线程访问会死锁）
        bgr_color = self.pages["process"].get_background_color_bgr()
        render_mode = self.pages["process"].get_render_mode()
        threading.Thread(target=self._process_worker,
                         args=(self.original_image, params,
                               self.current_image_idx, bgr_color, render_mode),
                         daemon=True).start()

    def on_batch_process(self):
        params = self._prepare_process(require_selection=False)
        if params is None:
            return
        self._show_page("process")
        self._start_processing("批量处理中...")
        self._stop_batch = False
        # 必须在主线程读取 UI 参数，再传入子线程
        bgr_color = self.pages["process"].get_background_color_bgr()
        render_mode = self.pages["process"].get_render_mode()
        threading.Thread(target=self._batch_process_worker,
                         args=(params, bgr_color, render_mode), daemon=True).start()

    def _start_processing(self, status_text):
        self.processing = True
        self.queue_pane.set_processing_state(True)
        self.queue_pane.clear_queue_status()
        self._set_status(status_text)
        self._update_model_status(in_use=True)

    def _stop_processing(self):
        self.processing = False
        self.queue_pane.set_processing_state(False)
        self.queue_pane.set_progress(0, "")
        self._set_progress("")
        self._update_model_status(in_use=False)

    def _batch_process_worker(self, params, bgr_color, render_mode):
        """注意：bgr_color / render_mode 必须由主线程读取后传入。

        绝不能在本线程内访问 self.pages["process"] 等 Qt 控件——
        跨线程操作 QWidget 会死锁或崩溃。
        """
        total = len(self.batch_images)
        results = {}
        for idx, item in enumerate(self.batch_images):
            if self._stop_batch:
                break
            pct = int((idx / total) * 100)
            self._safe_after(0, lambda p=pct, name=item["name"]:
                             self.queue_pane.set_progress(
                                 p, f"{idx + 1}/{total} {name}"))
            self._safe_after(0, lambda i=idx: self.queue_pane.select_queue_index(i))
            self._safe_after(0, lambda i=idx: self.queue_pane.set_item_active(i))
            self._safe_after(0, lambda i=idx: self.queue_pane.scroll_to_item(i))
            self._safe_after(0, lambda i=idx: self.queue_pane.set_item_progress(i, 0))
            # 阶段进度回调：推理→换底→美颜/水印，经 _safe_after 回主线程刷进度条
            def _item_progress(f, i=idx):
                self._safe_after(0, lambda: self.queue_pane.set_item_progress(
                    i, int(f * 100)))
            try:
                std, hd, matting = self._process_one(
                    item["image"], params, bgr_color, render_mode,
                    progress=_item_progress)
                results[idx] = (std, hd, matting)
                self._safe_after(0, lambda i=idx: self.queue_pane.set_queue_status(i, "✓"))
                self._safe_after(0, lambda i=idx: self.queue_pane.set_item_progress_done(i))
                self._safe_after(0, lambda i=idx, s=std, m=matting:
                                 self._auto_import_ready(
                                     s, self.batch_images[i].get("path"), matting=m))
            except Exception as e:
                self._safe_after(0, lambda i=idx: self.queue_pane.reset_item_progress(i))
                self._safe_after(0, lambda name=item["name"], err=str(e):
                                 theme.info(self, "warning", "批量处理",
                                            f"{name} 处理失败: {err}"))
        if results:
            for i, (std, hd, matting) in results.items():
                self.processed_results[i] = {"std": std, "hd": hd, "matting": matting}
            self._safe_after(0, lambda: self._refresh_result_thumbnails())
            # 批量处理完后自动选中第一个结果并预览（受设置开关控制）
            if self._settings.get("ui.auto_select_first_after_batch", True):
                first_idx = min(results.keys())
                self._safe_after(0, lambda i=first_idx: self._on_gallery_select(i))
        self._safe_after(0, self._on_batch_done)

    def _on_batch_done(self):
        self._stop_processing()
        self._set_status("批量处理完成，已自动切换到排版页")
        self._show_page("layout")

    def _process_worker(self, image, params, image_idx, bgr_color, render_mode):
        """bgr_color / render_mode 由主线程读取后传入（禁止本线程访问 Qt 控件）。"""
        def _item_progress(f, i=image_idx):
            self._safe_after(0, lambda: self.queue_pane.set_item_progress(
                i, int(f * 100)))
        try:
            std, hd, matting = self._process_one(
                image, params, bgr_color, render_mode, progress=_item_progress)
            self.standard_matting = matting
            self._safe_after(0, lambda: self._on_process_done(std, hd, matting, image_idx))
            self._safe_after(0, lambda i=image_idx:
                             self.queue_pane.set_queue_status(i, "✓"))
            self._safe_after(0, lambda i=image_idx:
                             self.queue_pane.set_item_progress_done(i))
        except FaceError as e:
            msg = f"人脸检测: {e}"
            self._safe_after(0, lambda i=image_idx:
                             self.queue_pane.reset_item_progress(i))
            self._safe_after(0, lambda m=msg: self._on_process_error(
                m, "检测到多张人脸或无人脸，请上传仅包含单张人脸的照片。"))
        except Exception as e:
            msg = f"处理出错: {e}"
            detail = str(e)
            self._safe_after(0, lambda i=image_idx:
                             self.queue_pane.reset_item_progress(i))
            self._safe_after(0, lambda m=msg, d=detail: self._on_process_error(m, d))

    def _process_one(self, image, params, bgr_color, render_mode, progress=None):
        """progress: 可选回调 progress(frac)，frac∈0~1，用于单项进度条。
        阶段划分：推理 0.1~0.5 → 换底 0.65 → 美颜 0.8 → 水印 0.9 → 完成 1.0。
        """
        _report = (lambda f: progress(f)) if progress else (lambda f: None)
        _report(0.1)
        result = self.photo_processor.process_id_photo(
            image,
            size=params.get("size"),
            head_measure_ratio=params["head_measure_ratio"],
            head_height_ratio=params["head_height_ratio"],
            head_top_range=params["head_top_range"],
            face_confidence_threshold=params["face_confidence_threshold"],
            face_alignment=params.get("face_alignment", False),
            process_mode=params["process_mode"],
        )
        _report(0.5)
        standard_with_bg = add_background_to_image(result.standard, bgr_color, render_mode)
        hd_with_bg = add_background_to_image(result.hd, bgr_color, render_mode)
        _report(0.65)
        standard_with_bg = self._apply_beauty(standard_with_bg, params.get("beauty", {}))
        hd_with_bg = self._apply_beauty(hd_with_bg, params.get("beauty", {}))
        _report(0.8)
        standard_with_bg = self._apply_watermark(standard_with_bg, params.get("watermark", {}))
        hd_with_bg = self._apply_watermark(hd_with_bg, params.get("watermark", {}))
        _report(0.9)
        if params.get("flip"):
            standard_with_bg = cv2.flip(standard_with_bg, 1)
            hd_with_bg = cv2.flip(hd_with_bg, 1)
        _report(1.0)
        return standard_with_bg, hd_with_bg, result.matting

    @staticmethod
    def _apply_beauty(image, beauty):
        from image_utils import apply_beauty
        return apply_beauty(image, beauty)

    @staticmethod
    def _apply_watermark(image, watermark):
        from image_utils import apply_watermark
        return apply_watermark(image, watermark)

    def _on_process_done(self, std_img, hd_img, matting_img, idx=None):
        self.standard_image = std_img
        self.hd_image = hd_img
        self.matting_image = matting_img
        if idx is None:
            idx = self.current_image_idx
        if 0 <= idx < len(self.batch_images):
            self.processed_results[idx] = {
                "std": std_img, "hd": hd_img, "matting": matting_img}
        self._auto_import_ready(
            std_img,
            self.batch_images[idx].get("path") if 0 <= idx < len(self.batch_images) else None,
            matting=matting_img)
        self._refresh_result_thumbnails()
        # 不再强制切换视图，保持用户当前选择的模式（默认"自动"）
        self._refresh_right_previews()
        if not self.processing:
            return
        self._stop_processing()
        self._set_status("证件照处理完成")

    def _auto_import_ready(self, image, src_path, matting=None):
        if image is None:
            return
        if src_path:
            norm = os.path.normcase(os.path.normpath(src_path))
            for it in self.ready_photos:
                if it.get("path") and os.path.normcase(os.path.normpath(it["path"])) == norm:
                    it["image"] = image
                    if matting is not None:
                        it["matting"] = matting
                    break
            else:
                self.ready_photos.append({
                    "name": os.path.basename(src_path), "path": src_path,
                    "image": image, "matting": matting})
        else:
            name = f"照片_{len(self.ready_photos) + 1}"
            self.ready_photos.append({
                "name": name, "path": "", "image": image, "matting": matting})
        self.layout_pane.refresh_ready_list()

    # ----------------------------------------------------------
    #  结果缩略图 / 画廊
    # ----------------------------------------------------------
    def _refresh_result_thumbnails(self):
        keys = sorted(self.processed_results.keys())
        self._result_thumb_indices = keys
        items = [{
            "name": self._name_at(idx),
            "image": self.processed_results[idx].get("std"),
        } for idx in keys]
        self.right_thumb.refresh_results(
            [(it["name"], it["image"]) for it in items])

    def on_result_select(self, item_idx):
        keys = self._result_thumb_indices
        if 0 <= item_idx < len(keys):
            self._on_gallery_select(keys[item_idx])

    def on_result_zoom(self, item_idx):
        keys = self._result_thumb_indices
        if 0 <= item_idx < len(keys):
            self._on_gallery_zoom(keys[item_idx])

    def _on_gallery_select(self, idx):
        if not (0 <= idx < len(self.batch_images)):
            return
        self.current_image_idx = idx
        self.queue_pane.select_queue_index(idx)
        self._gallery_selected_idx = idx
        self._refresh_right_previews()

    def _on_gallery_zoom(self, idx):
        res = self.processed_results.get(idx)
        if not res or res.get("std") is None:
            return
        self._gallery_selected_idx = idx
        # 不再强制切换视图，保持用户当前选择的模式（默认"自动"）
        self._refresh_right_previews()

    # ----------------------------------------------------------
    #  排版
    # ----------------------------------------------------------
    def _collect_layout_source_photos(self):
        photos = []
        for it in self.ready_photos:
            if it.get("image") is not None:
                photos.append({"image": it["image"], "matting": it.get("matting")})
        return photos

    def _rebuild_layout_from_sources(self, show=True, source_photos=None):
        from image_utils import add_background_to_image
        mixed_items_config = self.layout_pane.get_mixed_items()
        if not mixed_items_config:
            theme.info(self, "warning", "提示", "排版页没有配置规格，请先添加至少一个尺寸条目。")
            return False
        if source_photos is None:
            source_photos = self._collect_layout_source_photos()
        if not source_photos:
            theme.info(self, "warning", "提示",
                       "没有可排版的照片。请先在「证件照处理」页面处理照片，"
                       "或在排版页导入成品证件照。")
            return False
        items = []
        for name, w, h, count, bg_hex in mixed_items_config:
            w, h, count = int(w), int(h), int(count)
            if w <= 0 or h <= 0 or count <= 0:
                continue
            for src in source_photos:
                if bg_hex:
                    matting = src.get("matting")
                    if matting is not None:
                        rgb = hex_to_rgb_tuple(bg_hex)
                        bgr_color = (rgb[2], rgb[1], rgb[0])
                        base_img = add_background_to_image(matting, bgr_color, render_mode=0)
                    else:
                        base_img = src["image"]
                else:
                    base_img = src["image"]
                resized = self._center_crop_to_size(base_img, w, h)
                for _ in range(count):
                    items.append(LayoutItem(image=resized.copy(), width=w, height=h, label=name))
        if not items:
            theme.info(self, "warning", "提示", "没有有效的排版条目。")
            return False
        self.layout_queue = items
        self._generate_layout_from_queue(show=show)
        return True

    def _generate_layout_from_queue(self, show=False):
        if not self.layout_queue:
            return
        items = list(self.layout_queue)
        crop_line = self.pages["layout"].get_crop_line()
        crop_line_full_page = self.pages["layout"].get_crop_line_full_page()
        pw, ph = self.pages["layout"].get_paper_px()
        gap_h, gap_v = self.pages["layout"].get_photo_interval()
        bleed = self.pages["layout"].get_bleed_margin()
        engine = LayoutEngine(
            canvas_width=pw, canvas_height=ph,
            photo_interval_h=gap_h, photo_interval_v=gap_v,
            side_interval_w=bleed, side_interval_h=bleed,
        )
        try:
            unique_sizes = {(int(it.width), int(it.height)) for it in items}
            if len(unique_sizes) == 1:
                w, h = int(items[0].width), int(items[0].height)
                self.layout_pages = engine.generate_photos(
                    [it.image for it in items], w, h, crop_line=crop_line,
                    crop_line_full_page=crop_line_full_page)
                skipped = []
                size_color_labels = []
            else:
                self.layout_pages = engine.generate_mixed_size(
                    items, crop_line=crop_line,
                    crop_line_full_page=crop_line_full_page)
                skipped = list(getattr(engine, "last_skipped_sizes", []))
                label_by_size = {}
                for it in items:
                    label_by_size.setdefault(
                        (int(it.width), int(it.height)), getattr(it, "label", "") or "")
                size_color_labels = [
                    (label_by_size.get(k, ""), v)
                    for k, v in getattr(engine, "last_size_colors", {}).items()
                ]
        except ValueError as e:
            theme.info(self, "error", "排版失败", str(e))
            return
        if not self.layout_pages:
            theme.info(self, "error", "排版失败", "没有可排版的有效照片。")
            return
        self.pages["layout"].set_layout_pages(self.layout_pages, size_color_labels)
        if show:
            self._show_page("layout")
        msg = f"生成排版：共 {len(self.layout_pages)} 页"
        if skipped:
            msg += "（过大尺寸已跳过：" + "、".join(
                f"{w}x{h}" for w, h in skipped) + "）"
        self._set_status(msg)

    def _on_process_error(self, short_msg, detail_msg):
        self._stop_processing()
        self._set_status(f"错误: {short_msg}")
        theme.info(self, "error", "处理失败", detail_msg)

    def on_generate_mixed_layout(self):
        mixed_items_config = self.layout_pane.get_mixed_items()
        if not mixed_items_config:
            theme.info(self, "warning", "提示", "请先添加至少一个尺寸条目。")
            return
        if not self.ready_photos:
            if self.standard_image is not None or self.standard_matting is not None:
                src = {
                    "image": self.standard_image if self.standard_image is not None
                    else self.standard_matting,
                    "matting": self.standard_matting,
                }
                self._rebuild_layout_from_sources(show=True, source_photos=[src])
            else:
                theme.info(self, "warning", "提示",
                           "请先在「证件照处理」页面完成证件照制作，"
                           "或在排版页导入成品证件照。")
                self._show_page("process")
            return
        self._rebuild_layout_from_sources(show=True)

    def _refresh_layout_thumbnails(self, pages):
        self.right_thumb.refresh_pages(pages)

    def on_layout_page_select(self, idx):
        if 0 <= idx < len(self.layout_pages):
            self.pages["layout"].current_page_idx = idx
            self._refresh_right_previews()

    # ----------------------------------------------------------
    #  成品证件照
    # ----------------------------------------------------------
    def on_add_ready_photos(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择成品证件照（可多选）", "", f"图片文件 ({IMAGE_FILE_FILTER})")
        if not file_paths:
            return
        existing = {os.path.normcase(os.path.normpath(it["path"]))
                    for it in self.ready_photos if it.get("path")}
        added = skipped = 0
        failed = []
        for fp in file_paths:
            norm = os.path.normcase(os.path.normpath(fp))
            if norm in existing:
                skipped += 1
                continue
            try:
                image = self._load_image(fp)
                if image is None:
                    failed.append(os.path.basename(fp))
                    continue
                self.ready_photos.append({
                    "name": os.path.basename(fp), "path": fp,
                    "image": image, "matting": None})
                existing.add(norm)
                added += 1
            except Exception as e:
                failed.append(f"{os.path.basename(fp)}: {e}")
        if added:
            self.layout_pane.refresh_ready_list()
            status = f"已导入 {added} 张成品证件照，共 {len(self.ready_photos)} 张"
            if skipped:
                status += f"（跳过重复 {skipped} 张）"
            self._set_status(status)
        if failed:
            theme.info(self, "warning", "部分图片加载失败",
                       "无法读取以下文件：\n" + "\n".join(failed[:5]))

    def on_remove_ready_photo(self, idx):
        if 0 <= idx < len(self.ready_photos):
            name = self.ready_photos[idx]["name"]
            self.ready_photos.pop(idx)
            self.layout_pane.refresh_ready_list()
            self._set_status(f"已移除成品照片「{name}」，剩 {len(self.ready_photos)} 张")

    def on_clear_ready_photos(self):
        if not self.ready_photos:
            return
        self.ready_photos.clear()
        self.selected_ready_idx = -1
        self.layout_pane.refresh_ready_list()
        self._set_status("已清空成品证件照列表")

    def on_select_ready_photo(self, idx):
        if 0 <= idx < len(self.ready_photos):
            self.selected_ready_idx = idx
            # 点成品照时把视图切回「自动」（自动模式下排版页优先展示成品照），
            # 这样 Pivot 高亮跟随、且用户随后仍能自由切到 原图/证件照/排版。
            # set_view 若已处于 auto 不会发信号，下面再兜底刷新一次。
            self.preview_pane.set_view("auto")
            self._refresh_right_previews()

    @staticmethod
    def _center_crop_to_size(image, target_w, target_h):
        import cv2
        if image is None or image.size == 0:
            return None
        ih, iw = image.shape[:2]
        if iw < target_w or ih < target_h:
            scale = max(target_w / iw, target_h / ih)
            image = cv2.resize(image, (int(round(iw * scale)), int(round(ih * scale))),
                               interpolation=cv2.INTER_CUBIC)
            ih, iw = image.shape[:2]
        ratio = target_w / target_h
        img_ratio = iw / ih
        if img_ratio > ratio:
            crop_w = int(round(ih * ratio))
            x0 = (iw - crop_w) // 2
            cropped = image[:, x0:x0 + crop_w]
        else:
            crop_h = int(round(iw / ratio))
            y0 = (ih - crop_h) // 2
            cropped = image[y0:y0 + crop_h, :]
        return cv2.resize(cropped, (int(target_w), int(target_h)),
                          interpolation=cv2.INTER_AREA)

    # ----------------------------------------------------------
    #  打印
    # ----------------------------------------------------------
    def _list_printers(self):
        try:
            import win32print
            printers = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
            names = [p[2] for p in printers if p[2]]
            if names:
                return ["系统默认"] + names
        except Exception:
            pass
        return ["系统默认"]

    def start_refresh_printers(self):
        def _worker():
            try:
                printers = self._list_printers()
            except Exception:
                printers = ["系统默认"]
            self._safe_after(0, lambda: self.pages["layout"].set_printer_list(printers))
        threading.Thread(target=_worker, daemon=True).start()

    def on_printer_properties(self):
        try:
            import win32print
            name = self.pages["layout"].get_printer_name()
            if name is None:
                name = win32print.GetDefaultPrinter()
            hprinter = win32print.OpenPrinter(name)
            try:
                DM_IN_PROMPT = getattr(win32print, "DM_IN_PROMPT", 0x00000004)
                DM_OUT_DEFAULT = getattr(win32print, "DM_OUT_DEFAULT", 0x00000002)
                # 关键修复：devmode 不能传 None —— pywin32 的 DocumentProperties
                # 在 devmode=None + DM_IN_PROMPT 时多数驱动直接报 87(参数错误)。
                # 正确做法：先用 GetPrinter(level=2) 拿驱动当前 DEVMODE，
                # 再把它同时作为输入/输出传进去弹出属性对话框。
                devmode = win32print.GetPrinter(hprinter, 2).get("pDevMode")
                if devmode is None:
                    raise RuntimeError("驱动未返回 DEVMODE，无法打开属性对话框")
                win32print.DocumentProperties(
                    int(self.winId()), hprinter, name,
                    devmode, devmode, DM_IN_PROMPT | DM_OUT_DEFAULT)
            finally:
                win32print.ClosePrinter(hprinter)
        except Exception as e:
            theme.info(self, "warning", "打印机属性",
                       f"无法打开打印机属性：{e}\n\n请在打印排版时通过系统对话框设置纸张/打印机。")

    # ----------------------------------------------------------
    #  导出
    # ----------------------------------------------------------
    def on_export(self, export_type):
        layout_page_idx = self.pages["layout"].current_page_idx if self.layout_pages else 0
        if self.layout_pages and 0 <= layout_page_idx < len(self.layout_pages):
            layout_image = self.layout_pages[layout_page_idx]
        else:
            layout_image = None

        export_map = {
            "standard": ("通用证件照", self.standard_image),
            "hd": ("高清证件照", self.hd_image),
            "matting": ("透明底抠图", self.matting_image),
            "layout": ("排版图", layout_image),
        }
        label, image = export_map.get(export_type, (None, None))
        if image is None:
            display = label or export_type or "图片"
            theme.info(self, "warning", "提示", f"没有可导出的{display}，请先完成相应操作。")
            return

        try:
            dpi = int(self.pages["process"].dpi_combo.currentText())
        except Exception:
            dpi = 300
        try:
            kb_limit = int(self.pages["process"].kb_limit_edit.text() or "0")
        except Exception:
            kb_limit = 0
        fmt = self.pages["layout"].get_format()

        is_rgba = (len(image.shape) == 3 and image.shape[2] == 4)
        if is_rgba:
            ext = "png"
            filetypes = "PNG 图片 (*.png);;JPEG 图片 (*.jpg)"
        else:
            ext = "jpg" if fmt == "jpg" or kb_limit > 0 else "png"
            filetypes = ("JPEG 图片 (*.jpg);;PNG 图片 (*.png)"
                         if ext == "jpg" else "PNG 图片 (*.png);;JPEG 图片 (*.jpg)")

        file_path, _ = QFileDialog.getSaveFileName(
            self, f"保存{label}", f"{label}.{ext}", filetypes)
        if not file_path:
            return

        actual_ext = os.path.splitext(file_path)[1].lower()
        if actual_ext in (".jpg", ".jpeg"):
            force_jpg = True
        elif actual_ext == ".png":
            force_jpg = False
        else:
            force_jpg = (ext == "jpg")

        try:
            if is_rgba or not force_jpg:
                save_image_with_dpi(image, file_path, dpi)
            else:
                save_image_to_jpeg_kb(image, file_path,
                                      kb_limit if kb_limit > 0 else 200, dpi)
            self._set_status(f"已保存: {os.path.basename(file_path)}")
            theme.info(self, "success", "保存成功", f"文件已保存至:\n{file_path}")
        except Exception as e:
            theme.info(self, "error", "保存失败", str(e))

    def on_print(self):
        """打印全部排版页（调用系统打印对话框，默认带入已选打印机）。

        多页排版时一次输出所有有效页，每页用 printer.newPage() 分页。
        """
        layout_pages = getattr(self, "layout_pages", None)
        if not layout_pages:
            theme.info(self, "warning", "提示",
                       "没有可打印的排版图，请先在排版页生成排版。")
            return
        # 收集有效（非 None）的页，保留原始序号用于提示
        pages_to_print = [(i, img) for i, img in enumerate(layout_pages)
                          if img is not None]
        if not pages_to_print:
            theme.info(self, "warning", "提示", "没有可打印的图像（所有页均为空）。")
            return

        # 用 QPrinterInfo 把 QPrinter 绑定到「下拉里选中的那台」，而不是 setPrinterName：
        # setPrinterName 拿 win32print 的名字去设，常和 Qt 打印后端内部名不匹配，
        # 导致 QPrinter 处于无效状态 —— pageRect 返回 (0,0) 图像被缩放成 0 尺寸 -> 空白，
        # 同时打印任务落到默认打印机。这就是「选了打印机却打空白/打错机」的根因。
        name = self.pages["layout"].get_printer_name()
        info = QPrinterInfo.printerInfo(name) if name else None
        if info is None or not info.printerName():
            info = QPrinterInfo.defaultPrinter()
        if info is not None and info.printerName():
            printer = QPrinter(info)
        else:
            printer = QPrinter(QPrinter.HighResolution)
        printer.setResolution(300)

        # 是否「直接打印（跳过选择对话框）」：开启且已能确定目标打印机时，
        # 不弹系统对话框，直接发送任务；否则走 QPrintDialog。
        skip = False
        try:
            skip = bool(self._settings.get("print.skip_dialog", False))
        except Exception:
            skip = False

        if not skip:
            dlg = QPrintDialog(printer, self)
            dlg.setWindowTitle("打印排版图")
            # 告诉对话框总页数，用户可在对话框里选范围（选「全部」则 fromPage()/toPage() 均为 0）
            dlg.setMinMax(1, len(pages_to_print))
            if dlg.exec() != QDialog.Accepted:
                return

            # 尊重用户在对话框里设置的页码范围（1-based；都为 0 表示全部）
            fp, tp = dlg.fromPage(), dlg.toPage()
            if fp > 0 and tp > 0 and tp >= fp:
                pages_to_print = [p for p in pages_to_print
                                  if fp - 1 <= p[0] <= tp - 1]

        # 记录本次实际使用的打印机，下次自动选中（与排版页下拉联动）
        try:
            used = printer.printerName()
            if used:
                self._settings.set("print.last_printer", used)
                self._settings.save()
        except Exception:
            pass

        painter = QPainter(printer)
        try:
            for i, (orig_idx, image) in enumerate(pages_to_print):
                qimg = widgets._cv2_to_qimage(image)
                if qimg is None:
                    continue
                if i > 0:
                    printer.newPage()  # 后续页换页，避免叠在同一张纸上
                # 先铺白底：排版图若有透明/alpha 区域，避免打印成黑块或空白
                painter.fillRect(printer.paperRect(QPrinter.DevicePixel), Qt.white)
                page_rect = printer.pageRect(QPrinter.DevicePixel)
                # ⚠️ pageRect 返回 QRectF（浮点尺寸），但 QImage.scaled 只接受 QSize（整数）
                # 必须显式 toSize()，否则类型不匹配导致缩放失败 → 空白输出
                target_size = page_rect.size().toSize()
                # 用 QPixmap 中转打印比 QImage 更可靠（避免 bytes 缓冲区生命周期问题）
                src = QPixmap.fromImage(qimg.copy())
                target = src.scaled(target_size,
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = (target_size.width() - target.width()) / 2
                y = (target_size.height() - target.height()) / 2
                painter.drawPixmap(int(x), int(y), target)
        finally:
            painter.end()
        self._set_status(f"已发送打印任务：{printer.printerName()}（{len(pages_to_print)} 页）")

    # ----------------------------------------------------------
    #  模型下载
    # ----------------------------------------------------------
    def open_model_download_dialog(self):
        self._open_download_dialog()

    def open_highspeed_download_dialog(self):
        self._open_download_dialog()

    def open_settings_dialog(self):
        """供页面跳转设置菜单（如模型页「去设置下载」按钮）。"""
        self._open_settings_dialog()

    def refresh_paper_presets(self):
        """设置菜单里增删自定义纸张后，刷新排版页下拉（保持向后兼容）。"""
        try:
            self.pages["layout"]._load_custom_paper_presets()
        except Exception:
            traceback.print_exc()

    def _open_download_dialog(self):
        """两个入口共用同一实现；exec() 结束后显式销毁，避免弹窗对象驻留内存。"""
        dlg = ModelDownloadDialog(self.model_dir, self,
                                  mirror_manager=self._mirror_mgr,
                                  on_finished=self._post_download_refresh)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    def _open_settings_dialog(self):
        """打开「设置」对话框。保存后写回 controller._settings + 实时主题刷新。"""
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self, self)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    def _open_source_link(self):
        """打开本项目开源仓库（默认 GitHub 地址，可在设置中覆盖）。"""
        url = self._settings.get("appearance.open_source_url",
                                 "https://github.com/jintianjie/HivisionIDPhotos")
        if not url:
            url = "https://github.com/jintianjie/HivisionIDPhotos"
        QDesktopServices.openUrl(QUrl(url))

    def _open_mirror_dialog(self):
        """打开「镜像源管理」对话框。"""
        dlg = MirrorManagerDialog(self._mirror_mgr, self)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    def _post_download_refresh(self):
        self.model_available = self._check_models(self.model_dir)
        self._update_model_status()
        # 下载完成后，之前禁用的模型变为可选（不改变当前选择）
        try:
            self.pages["process"].refresh_model_states()
        except Exception:
            pass

    # ----------------------------------------------------------
    #  右侧预览刷新
    # ----------------------------------------------------------
    def _resolve_idphoto_preview(self):
        """解析「证件照」视图该显示哪张图 —— 预览跟随当前选中项。

        选中项优先取结果画廊选中项（点结果缩略图时设置），否则跟随队列选中项。
        已出结果显示 std，未处理回退原图，都没有才用 standard_image。

        这样点任意一张缩略图预览都会跟着走，不会停在上一次处理完那张图上
        （旧实现先拿 standard_image 再被 _gallery_selected_idx 覆盖，
        点队列缩略图不更新 _gallery_selected_idx，预览就不动）。
        """
        idx = self._gallery_selected_idx
        if idx is None or idx not in self.processed_results:
            idx = self.current_image_idx
        res = self.processed_results.get(idx)
        if res and res.get("std") is not None:
            return res.get("std")
        if 0 <= idx < len(self.batch_images):
            return self.batch_images[idx].get("image")
        return self.standard_image

    def _refresh_right_previews(self, page_id=None):
        if page_id is None:
            page_id = self.current_page

        view = self.preview_view
        if view == "auto":
            view = {"process": "idphoto", "layout": "layout"}.get(page_id, "idphoto")

        image = None
        placeholder = "请选择图片并处理"
        # 成品照预览只在排版页 +「自动」视图下生效。
        # 之前这分支是无条件最高优先级，一旦在排版页点过成品照，
        # Pivot（原图/证件照/排版）和下方排版页缩略图就全都切不动了
        # （永远被那张成品照锁死）。现在仅当用户未显式指定视图时才走它。
        if (page_id == "layout" and self.preview_view == "auto"
                and 0 <= self.selected_ready_idx < len(self.ready_photos)):
            placeholder = "成品证件照"
            image = self.ready_photos[self.selected_ready_idx].get("image")
        elif view == "original":
            placeholder = "导入后显示原图"
            if 0 <= self.current_image_idx < len(self.batch_images):
                image = self.batch_images[self.current_image_idx].get("image")
        elif view == "idphoto":
            placeholder = "处理后显示证件照"
            image = self._resolve_idphoto_preview()
        elif view == "layout":
            placeholder = "排版后显示效果图"
            layout_page = self.pages["layout"]
            if self.layout_pages:
                idx = layout_page.current_page_idx if page_id == "layout" else 0
                if 0 <= idx < len(self.layout_pages):
                    image = self.layout_pages[idx]
            else:
                # 无排版结果时显示空白纸张底图
                try:
                    pw, ph = layout_page.get_paper_px()
                    image = np.full((ph, pw, 3), 255, dtype=np.uint8)
                except Exception:
                    image = None

        self.preview_pane.show_image(image, placeholder)

    # ----------------------------------------------------------
    #  窗口关闭：停止后台调度、置停批量处理标志，确保进程干净退出
    # ----------------------------------------------------------
    def closeEvent(self, event):
        self._stop_batch = True
        self.processing = False
        try:
            self._invoker.stop()
        except Exception:
            pass
        # 落盘设置
        try:
            self._settings.save()
        except Exception:
            pass
        super().closeEvent(event)


# ============================================================
#  入口
# ============================================================
def main():
    app = QApplication(sys.argv)
    theme.apply_theme()
    win = IDPhotoApp()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
