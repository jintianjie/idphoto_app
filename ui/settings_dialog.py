# -*- coding: utf-8 -*-
"""
设置对话框 —— 主题色 / 外观 / 处理默认 / 下载 持久化设置
============================================================
从 controller._settings 初始化各控件；保存时写回 controller._settings
+ save() + 实时 theme.apply_theme，使设置成为下次启动默认值。

UI 与样式解耦：
  * 所有配色引用 theme.pal() / theme.THEME_COLOR，跟随主题模式(浅/深)。
  * 控件外观用 FluentWidgets 原生组件（不写自定义 QSS / 不引入暗色壳）。
"""
import os
import traceback

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy, QScrollArea,
    QFileDialog, QListWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap

from qfluentwidgets import (
    CardWidget, BodyLabel, StrongBodyLabel, CaptionLabel,
    ComboBox, SpinBox, ColorPickerButton, PushButton, PrimaryPushButton,
    FluentIcon, InfoBar, InfoBarPosition, LineEdit, Dialog, MessageBox,
)

from . import theme, widgets
from .layout_left_pane import LayoutLeftPane
from .layout_view import LayoutView
from .theme import make_logo_pixmap
from styles import RENDER_MODES, MATTING_MODELS, FACE_MODELS, PRESET_SIZES, PAPER_PRESETS


class SettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.setWindowTitle("设置")
        self.resize(540, 720)
        if parent is not None:
            self.setWindowModality(Qt.WindowModal)

        self._build()
        self._apply_dialog_theme()
        self._load_from_settings()

    # ----------------------------------------------------------
    def _apply_dialog_theme(self):
        """对话框底色 + 滚动区底色 跟随当前主题（浅/深模式）。"""
        bg = theme.pal("bg")
        surface = theme.pal("surface")
        self.setStyleSheet(
            f"QDialog{{background-color:{bg};}}"
            f"QScrollArea{{background-color:{bg};border:none;}}"
            f"QWidget#settingsScrollContent{{background-color:{bg};}}"
        )
        if hasattr(self, "_scroll_content"):
            self._scroll_content.setObjectName("settingsScrollContent")
        if hasattr(self, "_scroll"):
            self._scroll.viewport().setStyleSheet(
                f"background-color:{bg};")

    # ----------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        root.addWidget(StrongBodyLabel("设置"))
        root.addWidget(BodyLabel("修改后点击「保存」立即生效，并成为下次启动的默认值。"))

        # 内容区可滚动，避免设置项增多时超出对话框高度被裁剪
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_content = QWidget()
        self._scroll_content.setObjectName("settingsScrollContent")
        cl = QVBoxLayout(self._scroll_content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        root.addWidget(self._scroll, 1)
        self._scroll.setWidget(self._scroll_content)

        # ============ 外观 ============
        appearance = theme.SectionCard("外观")
        # 主题模式：浅色 / 深色
        mode_row = QHBoxLayout()
        mode_row.addWidget(theme.small_label("主题模式："))
        mode_row.addStretch(1)
        self.mode_group = widgets.RadioGroup(
            [("light", "浅色"), ("dark", "深色")], default="light")
        mode_row.addWidget(self.mode_group)
        appearance.addLayout(mode_row)

        # 主题色（强调色）
        color_row = QHBoxLayout()
        color_row.addWidget(theme.small_label("主题色："))
        self.accent_btn = ColorPickerButton(
            QColor(theme.THEME_COLOR), "选择主题色", self, enableAlpha=False)
        self.accent_btn.setFixedWidth(120)
        self.accent_swatch = QLabel()
        self.accent_swatch.setFixedSize(22, 22)
        self.accent_swatch.setStyleSheet(
            f"background-color:{theme.THEME_COLOR};border:1px solid "
            f"{theme.pal('border')};border-radius:4px;")
        self.accent_btn.colorChanged.connect(self._on_accent_changed)
        color_row.addWidget(self.accent_btn)
        color_row.addWidget(self.accent_swatch)
        color_row.addStretch(1)
        appearance.addLayout(color_row)

        # 软件名称
        name_row = QHBoxLayout()
        name_row.addWidget(theme.small_label("软件名称："))
        name_row.addStretch(1)
        self.app_name_edit = theme.themed_line_edit()
        self.app_name_edit.setFixedWidth(220)
        self.app_name_edit.setPlaceholderText("证件照制作工具")
        name_row.addWidget(self.app_name_edit)
        appearance.addLayout(name_row)

        # 软件图标
        icon_row = QHBoxLayout()
        icon_row.addWidget(theme.small_label("软件图标："))
        self.app_icon_preview = QLabel()
        self.app_icon_preview.setFixedSize(32, 32)
        self.app_icon_preview.setStyleSheet(
            f"border:1px solid {theme.pal('border')};border-radius:6px;")
        icon_row.addWidget(self.app_icon_preview)
        self.app_icon_btn = PushButton(FluentIcon.PHOTO.qicon(), "选择图标")
        self.app_icon_btn.clicked.connect(self._on_choose_icon)
        icon_row.addWidget(self.app_icon_btn)
        self.app_icon_reset_btn = PushButton("恢复默认")
        self.app_icon_reset_btn.clicked.connect(self._on_reset_icon)
        icon_row.addWidget(self.app_icon_reset_btn)
        icon_row.addStretch(1)
        appearance.addLayout(icon_row)
        cl.addWidget(appearance)

        # ============ 处理默认 ============
        proc = theme.SectionCard("处理默认")
        # 渲染模式
        rm_row = QHBoxLayout()
        rm_row.addWidget(theme.small_label("默认渲染模式："))
        rm_row.addStretch(1)
        self.render_combo = ComboBox()
        for name, _val in RENDER_MODES:
            self.render_combo.addItem(name)
        self.render_combo.setFixedWidth(140)
        rm_row.addWidget(self.render_combo)
        proc.addLayout(rm_row)

        # 默认抠图模型
        # 注: qfluentwidgets.ComboBox.addItem(text, userData) 不支持 userData
        # (itemData(i) 始终返回 None), 用并行的 _values 列表保存"显示文本 -> 实际值"
        matting_row = QHBoxLayout()
        matting_row.addWidget(theme.small_label("默认抠图模型："))
        matting_row.addStretch(1)
        self.matting_combo = ComboBox()
        self._matting_values = [val for val, _disp in MATTING_MODELS]
        for _val, display in MATTING_MODELS:
            self.matting_combo.addItem(display)
        self.matting_combo.setFixedWidth(220)
        matting_row.addWidget(self.matting_combo)
        proc.addLayout(matting_row)

        # 默认人脸检测模型
        face_row = QHBoxLayout()
        face_row.addWidget(theme.small_label("默认人脸模型："))
        face_row.addStretch(1)
        self.face_combo = ComboBox()
        self._face_values = [val for val, _disp in FACE_MODELS]
        for _val, display in FACE_MODELS:
            self.face_combo.addItem(display)
        self.face_combo.setFixedWidth(220)
        face_row.addWidget(self.face_combo)
        proc.addLayout(face_row)

        # 水印文字
        wm_row = QHBoxLayout()
        wm_row.addWidget(theme.small_label("默认水印文字："))
        wm_row.addStretch(1)
        self.wm_edit = theme.themed_line_edit()
        self.wm_edit.setFixedWidth(140)
        wm_row.addWidget(self.wm_edit)
        proc.addLayout(wm_row)

        # 自动选中首张
        auto_row = QHBoxLayout()
        auto_row.addWidget(theme.small_label("批量完成后自动选中首张："))
        auto_row.addStretch(1)
        self.auto_switch = theme.themed_switch()
        auto_row.addWidget(self.auto_switch)
        proc.addLayout(auto_row)

        # 默认导出 DPI
        dpi_row = QHBoxLayout()
        dpi_row.addWidget(theme.small_label("默认导出 DPI："))
        dpi_row.addStretch(1)
        self.dpi_combo = ComboBox()
        self.dpi_combo.addItems(["300", "600"])
        self.dpi_combo.setFixedWidth(150)
        dpi_row.addWidget(self.dpi_combo)
        proc.addLayout(dpi_row)

        # 默认 KB 限制
        kb_row = QHBoxLayout()
        kb_row.addWidget(theme.small_label("默认 KB 限制："))
        kb_row.addStretch(1)
        self.kb_spin = SpinBox()
        self.kb_spin.setRange(0, 2000)
        self.kb_spin.setSingleStep(50)
        self.kb_spin.setFixedWidth(150)
        kb_row.addWidget(self.kb_spin)
        proc.addLayout(kb_row)
        cl.addWidget(proc)

        # ============ 导出默认 ============
        exp = theme.SectionCard("导出默认")
        # 输出格式（PNG / JPEG）
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(theme.small_label("输出格式："))
        fmt_row.addStretch(1)
        self.export_format_group = widgets.RadioGroup(
            [("png", "PNG"), ("jpg", "JPEG")], default="png")
        fmt_row.addWidget(self.export_format_group)
        fmt_row.addStretch(1)
        exp.addLayout(fmt_row)
        cl.addWidget(exp)

        # ============ 排版默认 ============
        layout_def = theme.SectionCard("排版默认")
        # 显示裁剪线
        crop_row = QHBoxLayout()
        crop_row.addWidget(theme.small_label("显示裁剪线："))
        crop_row.addStretch(1)
        self.layout_crop_switch = theme.themed_switch()
        self.layout_crop_switch.setChecked(True)
        crop_row.addWidget(self.layout_crop_switch)
        layout_def.addLayout(crop_row)

        # 空白区也绘制切割线
        crop_full_row = QHBoxLayout()
        crop_full_row.addWidget(theme.small_label("空白区绘制切割线："))
        crop_full_row.addStretch(1)
        self.layout_crop_full_switch = theme.themed_switch()
        self.layout_crop_full_switch.setChecked(True)
        crop_full_row.addWidget(self.layout_crop_full_switch)
        layout_def.addLayout(crop_full_row)

        # 出血线
        bleed_row = QHBoxLayout()
        bleed_row.addWidget(theme.small_label("出血线(px)："))
        bleed_row.addStretch(1)
        self.layout_bleed_spin = SpinBox()
        self.layout_bleed_spin.setRange(0, 200)
        self.layout_bleed_spin.setValue(50)
        self.layout_bleed_spin.setFixedWidth(150)
        bleed_row.addWidget(self.layout_bleed_spin)
        layout_def.addLayout(bleed_row)

        # 横距 + 纵距
        gap_row = QHBoxLayout()
        gap_row.addWidget(theme.small_label("横距："))
        self.layout_gap_h_spin = SpinBox()
        self.layout_gap_h_spin.setRange(0, 50)
        self.layout_gap_h_spin.setValue(1)
        self.layout_gap_h_spin.setFixedWidth(150)
        gap_row.addWidget(self.layout_gap_h_spin)
        gap_row.addSpacing(8)
        gap_row.addWidget(theme.small_label("纵距："))
        self.layout_gap_v_spin = SpinBox()
        self.layout_gap_v_spin.setRange(0, 50)
        self.layout_gap_v_spin.setValue(1)
        self.layout_gap_v_spin.setFixedWidth(150)
        gap_row.addWidget(self.layout_gap_v_spin)
        gap_row.addStretch(1)
        layout_def.addLayout(gap_row)
        cl.addWidget(layout_def)

        # ============ 打印 ============
        print_card = theme.SectionCard("打印")
        # 直接打印（跳过系统选择对话框）：开启后点「打印」直接发给已选/
        # 上次使用的打印机，不再弹出选择打印机/页码的对话框。
        skip_row = QHBoxLayout()
        skip_row.addWidget(theme.small_label("直接打印(跳过选择对话框)："))
        skip_row.addStretch(1)
        self.print_skip_switch = theme.themed_switch()
        skip_row.addWidget(self.print_skip_switch)
        print_card.addLayout(skip_row)
        print_card.addWidget(CaptionLabel(
            "开启后直接发送给所选/上次使用的打印机，不再弹窗；需重选可关闭。"))
        cl.addWidget(print_card)

        # ============ 自定义纸张规格 ============
        paper_card = theme.SectionCard("自定义纸张规格")
        paper_card.addWidget(CaptionLabel(
            "在此新增或删除自定义纸张尺寸（单位：毫米）。内置纸张不可删除。"))
        # 自定义纸张列表
        self._paper_list = QListWidget()
        self._paper_list.setMaximumHeight(120)
        self._paper_list.setStyleSheet(theme.list_qss())
        paper_card.addWidget(self._paper_list)
        # 操作按钮
        paper_btns = QHBoxLayout()
        paper_btns.setSpacing(6)
        self._paper_add_btn = PrimaryPushButton(
            FluentIcon.ADD, "新增")
        self._paper_add_btn.clicked.connect(self._on_add_paper)
        paper_btns.addWidget(self._paper_add_btn)
        self._paper_del_btn = PushButton(
            FluentIcon.DELETE, "删除")
        self._paper_del_btn.clicked.connect(self._on_delete_paper)
        paper_btns.addWidget(self._paper_del_btn)
        paper_btns.addStretch(1)
        paper_card.addLayout(paper_btns)
        cl.addWidget(paper_card)

        # ============ 规格预设管理 ============
        preset_card = theme.SectionCard("规格预设管理")
        preset_card.addWidget(CaptionLabel(
            "在此新增、删除或修改自定义证件照规格（内置规格不可删除）。"))
        # 预设列表
        self._preset_list = QListWidget()
        self._preset_list.setMaximumHeight(120)
        self._preset_list.setStyleSheet(theme.list_qss())
        preset_card.addWidget(self._preset_list)
        # 操作按钮
        preset_btns = QHBoxLayout()
        preset_btns.setSpacing(6)
        self._preset_add_btn = PrimaryPushButton(
            FluentIcon.ADD, "新增")
        self._preset_add_btn.clicked.connect(self._on_add_preset)
        preset_btns.addWidget(self._preset_add_btn)
        self._preset_edit_btn = PushButton(
            FluentIcon.EDIT, "修改")
        self._preset_edit_btn.clicked.connect(self._on_edit_preset)
        preset_btns.addWidget(self._preset_edit_btn)
        self._preset_del_btn = PushButton(
            FluentIcon.DELETE, "删除")
        self._preset_del_btn.clicked.connect(self._on_delete_preset)
        preset_btns.addWidget(self._preset_del_btn)
        preset_btns.addStretch(1)
        preset_card.addLayout(preset_btns)
        cl.addWidget(preset_card)

        # ============ 模型下载 ============
        mdl = theme.SectionCard("模型下载")
        # 高级模型状态展示：每个模型一张小卡（名称 + 版本 + 状态指示器）
        self._model_cards_layout = QVBoxLayout()
        self._model_cards_layout.setSpacing(6)
        mdl.addLayout(self._model_cards_layout)

        # 操作按钮行
        mdl_btn_row = QHBoxLayout()
        mdl_btn_row.setSpacing(8)
        self.mdl_download_btn = PrimaryPushButton(
            FluentIcon.DOWNLOAD, "检查并下载缺失模型")
        self.mdl_download_btn.clicked.connect(
            lambda: self.c.open_model_download_dialog())
        mdl_btn_row.addWidget(self.mdl_download_btn)
        self.mdl_highspeed_btn = PushButton(FluentIcon.SPEED_HIGH, "高速下载")
        self.mdl_highspeed_btn.setToolTip("高速直链下载（需密码）")
        self.mdl_highspeed_btn.clicked.connect(self._open_highspeed_gate)
        mdl_btn_row.addWidget(self.mdl_highspeed_btn)
        mdl.addLayout(mdl_btn_row)
        cl.addWidget(mdl)

        # ============ 下载 ============
        dl = theme.SectionCard("下载")
        # 默认下载源
        src_row = QHBoxLayout()
        src_row.addWidget(theme.small_label("默认下载源："))
        src_row.addStretch(1)
        self.source_combo = ComboBox()
        self._source_values = ["mirror", "original"]
        self.source_combo.addItems([
            "加速地址（hf-mirror，国内直连，推荐）",
            "原地址（GitHub/HF，通常需特殊网络）",
        ])
        self.source_combo.setFixedWidth(280)
        src_row.addWidget(self.source_combo)
        dl.addLayout(src_row)

        # 超时
        to_row = QHBoxLayout()
        to_row.addWidget(theme.small_label("下载超时(秒)："))
        to_row.addStretch(1)
        self.timeout_spin = SpinBox()
        self.timeout_spin.setRange(10, 600)
        self.timeout_spin.setSingleStep(10)
        self.timeout_spin.setFixedWidth(150)
        to_row.addWidget(self.timeout_spin)
        dl.addLayout(to_row)

        # 记住密码
        rmb_row = QHBoxLayout()
        rmb_row.addWidget(theme.small_label("记住高速下载密码(会话内)："))
        rmb_row.addStretch(1)
        self.remember_switch = theme.themed_switch()
        rmb_row.addWidget(self.remember_switch)
        dl.addLayout(rmb_row)
        cl.addWidget(dl)

        cl.addStretch(1)

        # ============ 底部按钮 ============
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        folder_btn = PushButton(FluentIcon.FOLDER.qicon(), "打开配置文件夹")
        folder_btn.clicked.connect(self._open_config_folder)
        btn_row.addWidget(folder_btn)
        file_btn = PushButton(FluentIcon.DOCUMENT.qicon(), "打开配置文件")
        file_btn.setToolTip("用默认程序打开 settings.json，可直接编辑")
        file_btn.clicked.connect(self._open_config_file)
        btn_row.addWidget(file_btn)
        btn_row.addStretch(1)
        self.cancel_btn = PushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = PushButton("保存")
        self.save_btn.setIcon(FluentIcon.SAVE.qicon())
        self.save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)

    # ----------------------------------------------------------
    def _load_from_settings(self):
        s = self.c._settings
        mode = s.get("appearance.theme_mode", "light")
        self.mode_group.set(mode if mode in ("light", "dark") else "light")
        accent = s.get("appearance.accent_color", "#F97316")
        self.accent_btn.setColor(QColor(accent))
        self._sync_swatch(accent)

        # 软件名称 + 图标
        self.app_name_edit.setText(
            str(s.get("appearance.app_name", "证件照制作工具") or "证件照制作工具"))
        self._icon_path = str(s.get("appearance.app_icon", "") or "")
        pm = QPixmap(self._icon_path) if self._icon_path else QPixmap()
        self._set_icon_preview(pm if not pm.isNull() else None)

        render_val = int(s.get("ui.last_render_mode", 0) or 0)
        for i, (_name, val) in enumerate(RENDER_MODES):
            if val == render_val:
                self.render_combo.setCurrentIndex(i)
                break

        # 默认抠图 / 人脸模型
        matting_default = s.get("processing.default_matting_model",
                                "modnet_photographic_portrait_matting")
        if matting_default in self._matting_values:
            self.matting_combo.setCurrentIndex(
                self._matting_values.index(matting_default))
        face_default = s.get("processing.default_face_model",
                             "retinaface-resnet50")
        if face_default in self._face_values:
            self.face_combo.setCurrentIndex(
                self._face_values.index(face_default))

        # 默认输出格式
        fmt_default = str(s.get("export.default_format", "png") or "png")
        if fmt_default not in ("png", "jpg"):
            fmt_default = "png"
        self.export_format_group.set(fmt_default)

        self.wm_edit.setText(str(s.get("ui.watermark_text", "2") or "2"))
        self.auto_switch.setChecked(
            bool(s.get("ui.auto_select_first_after_batch", True)))

        dpi = int(s.get("ui.default_dpi", 300) or 300)
        self.dpi_combo.setCurrentText(str(dpi) if str(dpi) in ("300", "600") else "300")
        self.kb_spin.setValue(int(s.get("ui.default_kb_limit", 0) or 0))

        pref = s.get("model.preferred_source")
        if pref in self._source_values:
            self.source_combo.setCurrentIndex(self._source_values.index(pref))
        else:
            self.source_combo.setCurrentIndex(0)
        self.timeout_spin.setValue(int(s.get("model.default_timeout_sec", 120) or 120))
        self.remember_switch.setChecked(
            bool(s.get("download.remember_password", False)))

        # 排版默认
        self.layout_crop_switch.setChecked(
            bool(s.get("layout.show_crop_line", True)))
        self.layout_crop_full_switch.setChecked(
            bool(s.get("layout.crop_line_full_page", True)))
        self.layout_bleed_spin.setValue(
            int(s.get("layout.default_bleed", 50) or 50))
        self.layout_gap_h_spin.setValue(
            int(s.get("layout.default_gap_h", 1) or 1))
        self.layout_gap_v_spin.setValue(
            int(s.get("layout.default_gap_v", 1) or 1))

        # 打印：直接打印开关
        self.print_skip_switch.setChecked(
            bool(s.get("print.skip_dialog", False)))

        # 模型状态 —— 高级卡片展示
        self._build_model_status_cards()

        # 规格预设列表
        self._reload_preset_list()

        # 自定义纸张列表
        self._reload_paper_list()

    # ----------------------------------------------------------
    def _build_model_status_cards(self):
        """构建模型状态卡片列表（每模型一张小卡：名称 + 版本 + 状态指示器）。"""
        # 清空旧卡片
        while self._model_cards_layout.count():
            item = self._model_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from model_downloader import list_missing_models, _default_config
            # 从 controller 获取模型目录（与 app.py / model_download_dialog 一致）
            _model_dir = getattr(self.c, 'model_dir', None) or getattr(self.c, '_model_dir', None)
            if not _model_dir:
                # 回退：尝试从 settings 推断
                import os
                _model_dir = os.path.join(
                    getattr(self.c._settings, 'touch_dir', lambda: '.')(),
                    'models')
            miss = list_missing_models(_model_dir)
            miss_keys = set()
            for m in miss:
                # miss 项可能是 dict 或 string
                if isinstance(m, dict):
                    miss_keys.add(m.get("key", m.get("name", "")))
                else:
                    miss_keys.add(str(m))
            # 从默认配置的 manifest 取模型显示信息
            cfg = _default_config()
            models_info = [
                {"key": k, "display_name": v.get("filename", k), "version": ""}
                for k, v in cfg.get("manifest", {}).items()
            ]
        except Exception:
            # 降级：显示单行文字
            fallback = CaptionLabel("(无法检测模型状态)")
            fallback.setWordWrap(True)
            self._model_cards_layout.addWidget(fallback)
            return

        # 补充 MATTING_MODELS / FACE_MODELS 的显示名（更友好）
        _disp_map = {}
        for val, disp in (MATTING_MODELS or []):
            _disp_map[val] = disp
        for val, disp in (FACE_MODELS or []):
            _disp_map[val] = disp

        for mi in (models_info or []):
            key = mi.get("key", mi.get("name", ""))
            # 优先用 MATTING_MODELS/FACE_MODELS 的友好显示名
            name = _disp_map.get(key, mi.get("display_name", mi.get("name", "未知模型")))
            version = mi.get("version", "")
            is_missing = key in miss_keys

            # 单模型卡：水平排列 —— [状态圆点] 名称 [版本] [状态文字]
            card_row = QWidget()
            card_row.setObjectName(f"mdlCard_{key}")
            cr_lay = QHBoxLayout(card_row)
            cr_lay.setContentsMargins(8, 4, 8, 4)
            cr_lay.setSpacing(6)

            # 状态圆点（绿=就绪，红/灰=缺失）
            dot = QLabel("\u25CF")  # ●
            dot.setFixedSize(10, 10)
            if is_missing:
                dot.setStyleSheet("color:#EF4444;font-size:12px;")
                status_text = "未下载"
            else:
                dot.setStyleSheet("color:#22C55E;font-size:12px;")
                status_text = "已就绪"
            cr_lay.addWidget(dot, 0, Qt.AlignVCenter)

            # 模型名称（加粗）—— 不设固定宽度，让文字完整显示
            name_lbl = StrongBodyLabel(name)
            name_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            cr_lay.addWidget(name_lbl, 0, Qt.AlignVCenter)

            # 版本号
            if version:
                ver_lbl = CaptionLabel(version)
                cr_lay.addWidget(ver_lbl, 0, Qt.AlignVCenter)

            cr_lay.addStretch(1)

            # 状态文字
            st_lbl = CaptionLabel(status_text)
            if is_missing:
                st_lbl.setStyleSheet("color:#EF4444;")
            else:
                st_lbl.setStyleSheet("color:#22C55E;")
            cr_lay.addWidget(st_lbl, 0, Qt.AlignVCenter)

            # 卡片底色（交替或统一浅色）
            card_row.setStyleSheet(
                f"#{card_row.objectName()}"
                f"{{background-color:{theme.pal('surface')};"
                f"border-radius:4px;"
                f"border:1px solid {theme.pal('border')};}}")

            self._model_cards_layout.addWidget(card_row)

    # ----------------------------------------------------------
    #  规格预设管理
    # ----------------------------------------------------------
    BUILTIN_SIZE_NAMES = tuple(name for name, _, _ in PRESET_SIZES)
    BUILTIN_PAPER_NAMES = tuple(p[0] for p in PAPER_PRESETS)

    def _reload_preset_list(self):
        """从 settings 读取自定义规格并刷新列表。"""
        self._preset_list.blockSignals(True)
        self._preset_list.clear()
        s = self.c._settings
        raw = s.get("layout.custom_presets", []) if s else []
        presets = LayoutLeftPane._normalize_presets(raw) if raw else []
        for name, w, h, count in presets:
            self._preset_list.addItem(f"{name}  {w}x{h}  默认{count}张")
        self._preset_list.blockSignals(False)

    def _get_selected_preset(self):
        """返回选中行对应的 (index, [name, w, h, count]) 或 (None, None)。"""
        row = self._preset_list.currentRow()
        if row < 0:
            return None, None
        s = self.c._settings
        raw = s.get("layout.custom_presets", []) if s else []
        presets = LayoutLeftPane._normalize_presets(raw) if raw else []
        if row < len(presets):
            return row, presets[row]
        return None, None

    def _on_add_preset(self):
        dlg = Dialog("新增自定义规格", "设置名称、宽、高和默认数量。", self,
                     yesText="确定", cancelText="取消")
        # 名称
        dlg.textLayout.addWidget(BodyLabel("名称："))
        name_edit = LineEdit(dlg)
        name_edit.setPlaceholderText("例如：社保照")
        name_edit.setClearButtonEnabled(True)
        dlg.textLayout.addWidget(name_edit)
        # 宽高数量
        wh_row = QHBoxLayout()
        wh_row.addWidget(BodyLabel("宽："))
        w_spin = SpinBox(); w_spin.setRange(10, 2000); w_spin.setValue(295)
        wh_row.addWidget(w_spin)
        wh_row.addWidget(BodyLabel("高："))
        h_spin = SpinBox(); h_spin.setRange(10, 2000); h_spin.setValue(413)
        wh_row.addWidget(h_spin)
        wh_row.addWidget(BodyLabel("张数："))
        c_spin = SpinBox(); c_spin.setRange(1, 99); c_spin.setValue(8)
        wh_row.addWidget(c_spin)
        dlg.textLayout.addLayout(wh_row)
        if not dlg.exec():
            return
        name = name_edit.text().strip()
        if not name:
            InfoBar.warning(title="名称为空", content="请输入规格名称。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        if name in self.BUILTIN_SIZE_NAMES:
            InfoBar.warning(title="名称冲突",
                           content=f"「{name}」是内置规格，请换名。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        s = self.c._settings
        raw = list(s.get("layout.custom_presets", []) if s else [])
        if any(p[0] == name for p in raw):
            InfoBar.warning(title="已存在",
                           content=f"自定义规格「{name}」已存在。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        raw.append([name, int(w_spin.value()), int(h_spin.value()), int(c_spin.value())])
        s.set("layout.custom_presets", raw)
        try:
            s.save()
        except Exception:
            pass
        self._reload_preset_list()
        InfoBar.success(title="已添加",
                       content=f"自定义规格「{name}」已保存。",
                       parent=self, position=InfoBarPosition.TOP)

    def _on_edit_preset(self):
        idx, preset = self._get_selected_preset()
        if preset is None:
            InfoBar.warning(title="未选中", content="请先选中一个要修改的规格。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        name, old_w, old_h, old_c = preset
        dlg = Dialog("修改自定义规格", f"修改「{name}」的参数。", self,
                     yesText="确定", cancelText="取消")
        dlg.textLayout.addWidget(BodyLabel("名称："))
        name_edit = LineEdit(dlg)
        name_edit.setText(name)
        name_edit.setClearButtonEnabled(True)
        dlg.textLayout.addWidget(name_edit)
        wh_row = QHBoxLayout()
        wh_row.addWidget(BodyLabel("宽："))
        w_spin = SpinBox(); w_spin.setRange(10, 2000); w_spin.setValue(old_w)
        wh_row.addWidget(w_spin)
        wh_row.addWidget(BodyLabel("高："))
        h_spin = SpinBox(); h_spin.setRange(10, 2000); h_spin.setValue(old_h)
        wh_row.addWidget(h_spin)
        wh_row.addWidget(BodyLabel("张数："))
        c_spin = SpinBox(); c_spin.setRange(1, 99); c_spin.setValue(old_c)
        wh_row.addWidget(c_spin)
        dlg.textLayout.addLayout(wh_row)
        if not dlg.exec():
            return
        new_name = name_edit.text().strip()
        if not new_name:
            InfoBar.warning(title="名称为空", content="请输入规格名称。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        if new_name != name and new_name in self.BUILTIN_SIZE_NAMES:
            InfoBar.warning(title="名称冲突",
                           content=f"「{new_name}」是内置规格，请换名。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        s = self.c._settings
        raw = list(s.get("layout.custom_presets", []) if s else [])
        if idx < len(raw):
            raw[idx] = [new_name, int(w_spin.value()),
                        int(h_spin.value()), int(c_spin.value())]
            s.set("layout.custom_presets", raw)
            try:
                s.save()
            except Exception:
                pass
            self._reload_preset_list()
            InfoBar.success(title="已修改",
                           content=f"规格已更新为「{new_name}」。",
                           parent=self, position=InfoBarPosition.TOP)

    def _on_delete_preset(self):
        idx, preset = self._get_selected_preset()
        if preset is None:
            InfoBar.warning(title="未选中", content="请先选中一个要删除的自定义规格。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        name = preset[0]
        box = MessageBox("删除规格", f"确定删除自定义规格「{name}」吗？", self)
        if not box.exec():
            return
        s = self.c._settings
        raw = list(s.get("layout.custom_presets", []) if s else [])
        if idx < len(raw):
            raw.pop(idx)
            s.set("layout.custom_presets", raw)
            try:
                s.save()
            except Exception:
                pass
            self._reload_preset_list()
            InfoBar.success(title="已删除",
                           content=f"自定义规格「{name}」已删除。",
                           parent=self, position=InfoBarPosition.TOP)

    # ----------------------------------------------------------
    #  自定义纸张规格管理
    # ----------------------------------------------------------
    def _reload_paper_list(self):
        """从 settings 读自定义纸张并刷新列表。"""
        self._paper_list.blockSignals(True)
        self._paper_list.clear()
        s = self.c._settings
        raw = s.get("layout.custom_paper_presets", []) if s else []
        presets = LayoutView._normalize_paper(raw) if raw else []
        for name, w, h in presets:
            self._paper_list.addItem(f"{name}  {w}x{h}mm")
        self._paper_list.blockSignals(False)

    def _on_add_paper(self):
        dlg = Dialog("新增自定义纸张", "尺寸单位：毫米（mm）。名称不能与内置纸张重复。", self,
                     yesText="确定", cancelText="取消")
        dlg.textLayout.addWidget(BodyLabel("名称："))
        name_edit = LineEdit(dlg)
        name_edit.setPlaceholderText("例如：6寸竖版卡纸")
        name_edit.setClearButtonEnabled(True)
        dlg.textLayout.addWidget(name_edit)
        wh_row = QHBoxLayout()
        wh_row.addWidget(BodyLabel("宽（mm）："))
        w_spin = SpinBox(); w_spin.setRange(1, 2000); w_spin.setValue(152)
        wh_row.addWidget(w_spin)
        wh_row.addWidget(BodyLabel("高（mm）："))
        h_spin = SpinBox(); h_spin.setRange(1, 2000); h_spin.setValue(102)
        wh_row.addWidget(h_spin)
        dlg.textLayout.addLayout(wh_row)
        if not dlg.exec():
            return
        name = name_edit.text().strip()
        if not name:
            InfoBar.warning(title="名称为空", content="请输入纸张规格名称。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        if name in self.BUILTIN_PAPER_NAMES:
            InfoBar.warning(title="名称冲突",
                           content=f"「{name}」是内置纸张，请换一个名称。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        s = self.c._settings
        raw = list(s.get("layout.custom_paper_presets", []) if s else [])
        if any(p[0] == name for p in raw):
            InfoBar.warning(title="名称冲突",
                           content=f"自定义纸张「{name}」已存在。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        w = int(w_spin.value())
        h = int(h_spin.value())
        if w <= 0 or h <= 0:
            InfoBar.warning(title="参数错误", content="请输入有效的宽和高。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        raw.append([name, w, h])
        s.set("layout.custom_paper_presets", raw)
        try:
            s.save()
        except Exception:
            pass
        self._reload_paper_list()
        # 实时刷新排版页下拉，使其立即生效
        if hasattr(self.c, "refresh_paper_presets"):
            try:
                self.c.refresh_paper_presets()
            except Exception:
                traceback.print_exc()
        InfoBar.success(title="已添加",
                       content=f"自定义纸张「{name}」{w}x{h}mm 已保存。",
                       parent=self, position=InfoBarPosition.TOP)

    def _on_delete_paper(self):
        row = self._paper_list.currentRow()
        if row < 0:
            InfoBar.warning(title="未选中", content="请先选中一个要删除的自定义纸张。",
                           parent=self, position=InfoBarPosition.TOP)
            return
        s = self.c._settings
        raw = list(s.get("layout.custom_paper_presets", []) if s else [])
        presets = LayoutView._normalize_paper(raw)
        if row >= len(presets):
            return
        name = presets[row][0]
        box = MessageBox("删除纸张规格", f"确定删除自定义纸张「{name}」吗？", self)
        if not box.exec():
            return
        # 按名称匹配删除（避免脏数据与列表索引错位）
        idx = next((i for i, p in enumerate(raw) if p[0] == name), None)
        if idx is None:
            return
        raw.pop(idx)
        s.set("layout.custom_paper_presets", raw)
        try:
            s.save()
        except Exception:
            pass
        self._reload_paper_list()
        if hasattr(self.c, "refresh_paper_presets"):
            try:
                self.c.refresh_paper_presets()
            except Exception:
                traceback.print_exc()
        InfoBar.success(title="已删除",
                       content=f"自定义纸张「{name}」已删除。",
                       parent=self, position=InfoBarPosition.TOP)

    # ----------------------------------------------------------
    def _on_accent_changed(self, color: QColor):
        self._sync_swatch(color.name())

    def _sync_swatch(self, hex_color: str):
        self.accent_swatch.setStyleSheet(
            f"background-color:{hex_color};border:1px solid "
            f"{theme.pal('border')};border-radius:4px;")

    # ----------------------------------------------------------
    #  软件图标：选择 / 恢复默认 / 预览
    # ----------------------------------------------------------
    def _set_icon_preview(self, pm):
        """pm=None 或空图像时回退到默认 logo。"""
        if pm is None or pm.isNull():
            pm = make_logo_pixmap(32)
        self.app_icon_preview.setPixmap(
            pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _on_choose_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择软件图标", "",
            "图标文件 (*.ico *.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)")
        if not path:
            return
        pm = QPixmap(path)
        if pm.isNull():
            theme.info(self, "warning", "提示", "无法读取该图片，请换一个文件。")
            return
        self._icon_path = os.path.abspath(path)
        self._set_icon_preview(pm)

    def _on_reset_icon(self):
        self._icon_path = ""
        self._set_icon_preview(None)

    # ----------------------------------------------------------
    def _open_config_folder(self):
        d = self.c._settings.touch_dir()
        try:
            os.startfile(d)
        except Exception:
            theme.info(self, "warning", "提示", f"配置目录：\n{d}")

    def _open_config_file(self):
        """用系统默认程序打开 settings.json（文件不存在则先落盘一份）。"""
        path = self.c._settings.path      # SettingsStore.path 是 @property
        try:
            if not os.path.exists(path):
                self.c._settings.save()
            os.startfile(path)
        except Exception:
            theme.info(self, "warning", "提示", f"配置文件：\n{path}")

    # ----------------------------------------------------------
    def _on_save(self):
        """保存设置。

        拆成 3 阶段执行，避免与 dialog 关闭事件竞争导致卡死 / 闪退：

          阶段 1（同步）：把所有字段读出来写到 settings 并落盘。这一步不许
                          触碰主题、polish QSS、弹 InfoBar，纯数据 I/O。
          阶段 2（同步）：dialog.accept() —— 关闭模态、触发关闭事件链。
          阶段 3（异步）：挂在主窗口上的 QTimer 延后 80ms 再跑主题刷新 +
                          identity 刷新 + InfoBar。此时 dialog 已被
                          deleteLater 销毁完，刷新范围只剩主窗口自身。

        每一步独立 try/except 包裹，单点失败也不会让整个保存流程中断或闪退。

        历史卡死 / 闪退的两个真根因（2026-09-02）：
          1. theme.apply_theme() 里的 ``QApplication.setStyle("Fusion")``。
             setStyle 销毁旧 QStyle 并对全应用所有 widget 发 StyleChange +
             重新 polish，且会踩到 deleteLater 排队中的 dialog 子控件
             → use-after-free。已改为 ``_apply_neutral_palette()``
             （setPalette，不重建 QStyle、不触发全局 polish）。
          2. ``QTimer.singleShot(0, 局部闭包)``：闭包无强引用可能被 GC；
             且 0ms 会抢在 deleteLater 生效前执行。已改为挂在 ctrl 上的
             QTimer 实例 + 80ms 延迟。
        """
        s = self.c._settings
        ctrl = self.c  # 捕获 controller 防 dialog 提前销毁

        # ---------- 阶段 1：纯数据 I/O ----------
        try:
            # 外观
            s.set("appearance.theme_mode", self.mode_group.get())
            s.set("appearance.accent_color", self.accent_btn.color.name())
            s.set("appearance.app_name",
                  self.app_name_edit.text().strip() or "证件照制作工具")
            s.set("appearance.app_icon", self._icon_path or "")
            # 处理默认
            render_idx = self.render_combo.currentIndex()
            render_val = (RENDER_MODES[render_idx][1]
                          if 0 <= render_idx < len(RENDER_MODES) else 0)
            s.set("ui.last_render_mode", int(render_val))
            # 抠图 / 人脸模型
            matting_idx = self.matting_combo.currentIndex()
            matting_val = (self._matting_values[matting_idx]
                           if 0 <= matting_idx < len(self._matting_values)
                           else "modnet_photographic_portrait_matting")
            s.set("processing.default_matting_model", matting_val)
            face_idx = self.face_combo.currentIndex()
            face_val = (self._face_values[face_idx]
                        if 0 <= face_idx < len(self._face_values)
                        else "retinaface-resnet50")
            s.set("processing.default_face_model", face_val)
            # 输出格式
            s.set("export.default_format", self.export_format_group.get())
            s.set("ui.watermark_text", self.wm_edit.text().strip() or "2")
            s.set("ui.auto_select_first_after_batch", self.auto_switch.isChecked())
            s.set("ui.default_dpi", int(self.dpi_combo.currentText()))
            s.set("ui.default_kb_limit", int(self.kb_spin.value()))
            # 下载
            s.set("model.preferred_source", self._source_values[self.source_combo.currentIndex()])
            s.set("model.default_timeout_sec", int(self.timeout_spin.value()))
            s.set("download.remember_password", self.remember_switch.isChecked())
            # 排版默认
            s.set("layout.show_crop_line", self.layout_crop_switch.isChecked())
            s.set("layout.crop_line_full_page", self.layout_crop_full_switch.isChecked())
            s.set("layout.default_bleed", int(self.layout_bleed_spin.value()))
            s.set("layout.default_gap_h", int(self.layout_gap_h_spin.value()))
            s.set("layout.default_gap_v", int(self.layout_gap_v_spin.value()))
            # 打印
            s.set("print.skip_dialog", self.print_skip_switch.isChecked())
        except Exception as e:
            theme.info(self, "error", "保存失败", f"读取控件值失败: {e}")
            return

        try:
            s.save()
        except Exception as e:
            theme.info(self, "error", "保存失败", f"无法写入设置文件: {e}")
            return

        # ---------- 阶段 2：关闭 dialog（必须发生在主题刷新之前） ----------
        self.accept()

        # ---------- 阶段 3：dialog 销毁之后再做主题刷新 / 身份刷新 / InfoBar ----------
        #
        # 为什么用「挂在主窗口上的 QTimer 实例」而不是 QTimer.singleShot(0, 闭包)：
        #   1. singleShot 的局部闭包没有外部强引用，回调触发前可能被 GC → 崩。
        #      QTimer(ctrl) 归主窗口所有，闭包被 connection 持有，生命周期安全。
        #   2. 0ms 太早。app.py 里是 `dlg.exec()` 之后立刻 `dlg.deleteLater()`，
        #      0ms 的定时器会**抢在 deleteLater 生效之前**跑 apply_theme，
        #      此时 dialog 的几十个子控件还活着但马上要被销毁，刷新它们
        #      纯属浪费，且构成竞争。延后 80ms 等 dialog 真正销毁完再刷。
        def _post_close():
            try:
                theme.apply_theme(s)
            except Exception:
                traceback.print_exc()
            try:
                ctrl._refresh_theme_styles()
            except Exception:
                traceback.print_exc()
            try:
                ctrl._apply_app_identity()
            except Exception:
                traceback.print_exc()
            try:
                theme.info(ctrl, "success", "已保存",
                           "设置已保存，软件将在 2 秒后自动重启以生效全部更改。")
            except Exception:
                traceback.print_exc()
            # 自动重启：延迟 2 秒让用户看到成功提示，然后重启进程
            def _do_restart():
                import os, sys, subprocess
                # 用当前 python 解释器重新启动自身（保留命令行参数）
                # 打包后 sys.argv[0] 已经是 exe 自身路径，再拼一次会变成
                # [exe, exe]，等于把 exe 路径当作要打开的文件参数传进去；
                # 源码运行时 sys.argv[0] 是脚本路径，必须保留。
                _argv = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
                # PDETACH 在 Windows 上让子进程脱离父进程，避免被连带杀掉
                if sys.platform == "win32":
                    creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                    subprocess.Popen(
                        [sys.executable] + _argv,
                        cwd=os.getcwd(),
                        creationflags=creationflags,
                        start_new_session=True,
                    )
                else:
                    os.execv(sys.executable, [sys.executable] + _argv)
                QApplication.instance().quit()

            _restart_timer = QTimer(ctrl)
            _restart_timer.setSingleShot(True)
            _restart_timer.timeout.connect(_do_restart)
            _restart_timer.timeout.connect(_restart_timer.deleteLater)
            _restart_timer.start(2000)

        from PySide6.QtCore import QTimer
        timer = QTimer(ctrl)
        timer.setSingleShot(True)
        # 连接顺序 = 调用顺序：先跑 _post_close，再自我释放，
        # 避免反复开关设置时 QTimer 在主窗口上累积。
        timer.timeout.connect(_post_close)
        timer.timeout.connect(timer.deleteLater)
        timer.start(80)

    # ----------------------------------------------------------
    #  高速下载密码门
    # ----------------------------------------------------------

    def _open_highspeed_gate(self):
        """弹出高速下载门控对话框：提示流量有限 + 密码输入，验证通过后打开 ModelDownloadDialog。

        门禁优先用工具箱生成的配置文件密码（mirror_manager.try_unlock）；
        无加密配置文件时回退到内置访问密码。
        """
        from .highspeed_gate_dialog import HighSpeedGateDialog
        mgr = getattr(self.c, "_mirror_mgr", None)
        dlg = HighSpeedGateDialog(parent=self, mirror_manager=mgr)
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.c.open_model_download_dialog()
        finally:
            dlg.deleteLater()
