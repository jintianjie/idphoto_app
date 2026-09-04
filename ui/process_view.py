# -*- coding: utf-8 -*-
"""证件照处理页 — 仅参数面板 (队列已拆到 ProcessQueuePane)
============================================================
构建: TabStrip(核心/高级/美颜/水印/模型) + 快捷操作按钮。
所有取参 getter 供控制器收集参数, 用户操作通过回调交给控制器。
业务逻辑 (抠图 / 裁剪 / 换底 / 美颜 / 水印) 一律在后端模块。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QColorDialog,
)
from PySide6.QtGui import QColor
from qfluentwidgets import (
    ComboBox, LineEdit, PushButton, PrimaryPushButton, SwitchButton,
    BodyLabel, CaptionLabel, FluentIcon, InfoBar, InfoBarPosition,
)

from . import widgets
from styles import (
    RENDER_MODES, MATTING_MODELS, FACE_MODELS, PRESET_COLORS, PROCESS_MODES,
    DEFAULT_BG_COLOR, DEFAULT_WATERMARK_COLOR,
)
from color_utils import hex_to_rgb_tuple


class ProcessView(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self._build()

    def _build(self):
        # 页面根布局：外边距 24、间距 12（本项目 UI 规范）
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        v.addWidget(self._build_tabs(), 1)

        action = widgets.CardSection("快捷操作")
        self.process_btn = PrimaryPushButton(FluentIcon.PLAY, "开始处理")
        self.process_btn.clicked.connect(lambda: self.c.on_process())
        self.batch_btn = PrimaryPushButton(FluentIcon.SYNC, "批量处理全部")
        self.batch_btn.clicked.connect(lambda: self.c.on_batch_process())
        action.addWidget(self.process_btn)
        action.addWidget(self.batch_btn)
        v.addWidget(action)

    def _build_tabs(self):
        basic = self._tab_basic()
        adv = self._tab_advanced()
        beauty = self._tab_beauty()
        water = self._tab_watermark()
        model = self._tab_model()
        return widgets.TabStrip([
            ("basic", "核心", basic),
            ("adv", "高级", adv),
            ("beauty", "美颜", beauty),
            ("water", "水印", water),
            ("model", "模型", model),
        ])

    # ----------------------------------------------------------
    #  核心参数
    # ----------------------------------------------------------
    def _tab_basic(self):
        w = QWidget()
        # tab 页根布局：外边距交给页面根布局（24），此处 0 避免叠加；间距 12
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        mode_card = widgets.CardSection("处理模式")
        self.process_mode_group = widgets.RadioGroup(
            list(PROCESS_MODES), default="crop_bg")
        mode_card.addWidget(self.process_mode_group)
        v.addWidget(mode_card)

        bg_card = widgets.CardSection("背景设置")
        self.color_group = widgets.ColorSwatchGroup(PRESET_COLORS, DEFAULT_BG_COLOR)
        self.color_group.colorSelected.connect(self._on_color_selected)
        bg_card.addWidget(self.color_group)

        hex_row = QHBoxLayout()
        hex_row.setSpacing(8)
        hex_row.addWidget(BodyLabel("HEX:"))
        self.bg_hex_edit = LineEdit()
        self.bg_hex_edit.setText(DEFAULT_BG_COLOR)
        self.bg_hex_edit.setFixedWidth(86)
        hex_row.addWidget(self.bg_hex_edit)
        self.bg_hex_apply_btn = PushButton("应用")
        self.bg_hex_apply_btn.clicked.connect(self._apply_bg_hex)
        hex_row.addWidget(self.bg_hex_apply_btn)
        hex_row.addStretch(1)
        bg_card.addLayout(hex_row)

        # 渲染模式：纯色独占一行，上下渐变+中心渐变并排一行
        bg_card.addWidget(BodyLabel("渲染模式:"))
        render_default = self.c._settings.get("ui.last_render_mode", 0) or 0
        from PySide6.QtWidgets import QButtonGroup
        from qfluentwidgets import RadioButton
        self.render_group = QButtonGroup(self)
        self.render_buttons = {}

        # 第1行：纯色
        rb_solid = RadioButton("纯色")
        self.render_group.addButton(rb_solid, 0)
        self.render_buttons[0] = rb_solid
        bg_card.addWidget(rb_solid)

        # 第2行：上下渐变 | 中心渐变（横排）
        grad_row = QHBoxLayout()
        grad_row.setSpacing(12)
        rb_vgrad = RadioButton("上下渐变")
        self.render_group.addButton(rb_vgrad, 1)
        self.render_buttons[1] = rb_vgrad
        grad_row.addWidget(rb_vgrad)
        rb_cgrad = RadioButton("中心渐变")
        self.render_group.addButton(rb_cgrad, 2)
        self.render_buttons[2] = rb_cgrad
        grad_row.addWidget(rb_cgrad)
        grad_row.addStretch(1)
        bg_card.addLayout(grad_row)

        # 选中默认值
        default_idx = int(render_default)
        if default_idx in self.render_buttons:
            self.render_buttons[default_idx].setChecked(True)
        v.addWidget(bg_card)

        plugin_card = widgets.CardSection("插件")
        flip_row = QHBoxLayout()
        flip_row.setSpacing(8)
        flip_row.addWidget(BodyLabel("横向翻转:"))
        self.flip_switch = SwitchButton()
        flip_row.addWidget(self.flip_switch)
        flip_row.addStretch(1)
        plugin_card.addLayout(flip_row)

        rotation_row = QHBoxLayout()
        rotation_row.setSpacing(8)
        rotation_row.addWidget(BodyLabel("人脸旋转校正:"))
        self.rotation_switch = SwitchButton()
        rotation_row.addWidget(self.rotation_switch)
        rotation_row.addStretch(1)
        plugin_card.addLayout(rotation_row)
        v.addWidget(plugin_card)
        v.addStretch(1)
        return w

    # ----------------------------------------------------------
    #  高级参数
    # ----------------------------------------------------------
    def _tab_advanced(self):
        w = QWidget()
        # tab 页根布局：外边距交给页面根布局（24），此处 0 避免叠加；间距 12
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        adv_card = widgets.CardSection("人脸裁剪与输出")
        self.head_measure_row = widgets.SliderRow("面比", 0.1, 0.5, 0.01, 0.20, 2)
        self.head_height_row = widgets.SliderRow("距顶", 0.02, 0.5, 0.01, 0.45, 2)
        adv_card.addWidget(self.head_measure_row)
        adv_card.addWidget(self.head_height_row)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.addWidget(BodyLabel("留白:"))
        self.head_top_max_edit = LineEdit(); self.head_top_max_edit.setText("0.12")
        self.head_top_max_edit.setFixedWidth(56)
        top_row.addWidget(self.head_top_max_edit)
        top_row.addWidget(BodyLabel("/"))
        self.head_top_min_edit = LineEdit(); self.head_top_min_edit.setText("0.10")
        self.head_top_min_edit.setFixedWidth(56)
        top_row.addWidget(self.head_top_min_edit)
        top_row.addStretch(1)
        adv_card.addLayout(top_row)

        self.face_conf_row = widgets.SliderRow("置信阈值", 0.1, 0.95, 0.01, 0.80, 2)
        adv_card.addWidget(self.face_conf_row)
        # 「输出设置」原是独立卡，与裁剪参数合进同一张卡省竖向空间
        # KB 限制独占一行
        kb_row = QHBoxLayout()
        kb_row.setSpacing(8)
        kb_row.addWidget(BodyLabel("KB 限制:"))
        kb_val = int(self.c._settings.get("ui.default_kb_limit", 0) or 0)
        self.kb_limit_edit = LineEdit(); self.kb_limit_edit.setText(str(kb_val))
        self.kb_limit_edit.setFixedWidth(56)
        kb_row.addWidget(self.kb_limit_edit)
        kb_row.addWidget(CaptionLabel("(0=不限)"))
        kb_row.addStretch(1)
        adv_card.addLayout(kb_row)

        # DPI 独占一行
        dpi_val = int(self.c._settings.get("ui.default_dpi", 300) or 300)
        dpi_row = QHBoxLayout()
        dpi_row.setSpacing(8)
        self.dpi_combo = ComboBox()
        self.dpi_combo.addItems(["300", "600"])
        self.dpi_combo.setCurrentText(
            str(dpi_val) if str(dpi_val) in ("300", "600") else "300")
        self.dpi_combo.setFixedWidth(72)
        dpi_row.addWidget(BodyLabel("DPI:"))
        dpi_row.addWidget(self.dpi_combo)
        dpi_row.addStretch(1)
        adv_card.addLayout(dpi_row)

        # 恢复默认值按钮独占一行
        self.reset_adv_btn = PushButton("恢复默认值")
        self.reset_adv_btn.clicked.connect(lambda: self._reset_advanced())
        adv_card.addWidget(self.reset_adv_btn)
        v.addWidget(adv_card)

        v.addStretch(1)
        return w

    # ----------------------------------------------------------
    #  美颜
    # ----------------------------------------------------------
    def _tab_beauty(self):
        w = QWidget()
        # tab 页根布局：外边距交给页面根布局（24），此处 0 避免叠加；间距 12
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        beauty_card = widgets.CardSection("美颜参数")
        self.whiten_row = widgets.SliderRow("美白", 0, 15, 1, 0, 0)
        beauty_card.addWidget(self.whiten_row)

        # 上一版亮度/对比度/饱和度三 slider 横排一行,在 300px 右栏内挤切「饱和度」。
        # 拆 3 行与「面部比例 / 头距顶距离」同档,跟其它参数面板视觉一致。
        self.brightness_row = widgets.SliderRow("亮度", -5, 25, 1, 0, 0)
        self.contrast_row = widgets.SliderRow("对比", -10, 50, 1, 0, 0)
        self.saturation_row = widgets.SliderRow("饱和", -10, 50, 1, 0, 0)
        beauty_card.addWidget(self.brightness_row)
        beauty_card.addWidget(self.contrast_row)
        beauty_card.addWidget(self.saturation_row)

        self.sharpen_row = widgets.SliderRow("锐化", 0, 5, 1, 0, 0)
        beauty_card.addWidget(self.sharpen_row)

        self.reset_beauty_btn = PushButton("重置美颜")
        self.reset_beauty_btn.clicked.connect(lambda: self._reset_beauty())
        beauty_card.addWidget(self.reset_beauty_btn)
        v.addWidget(beauty_card)

        v.addStretch(1)
        return w

    # ----------------------------------------------------------
    #  水印
    # ----------------------------------------------------------
    def _tab_watermark(self):
        w = QWidget()
        # tab 页根布局：外边距交给页面根布局（24），此处 0 避免叠加；间距 12
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # 三块内容（开关/文字颜色/样式滑块）合进一张卡，窄栏里省竖向空间
        card = widgets.CardSection("水印")

        en_row = QHBoxLayout()
        en_row.setSpacing(8)
        en_row.addWidget(BodyLabel("添加水印:"))
        self.watermark_enable_switch = SwitchButton()
        en_row.addWidget(self.watermark_enable_switch)
        en_row.addStretch(1)
        card.addLayout(en_row)

        # 文字与颜色挤同一行，进一步压缩高度
        text_row = QHBoxLayout()
        text_row.setSpacing(8)
        text_row.addWidget(BodyLabel("水印文字:"))
        wm_default = self.c._settings.get("ui.watermark_text", "2") or "2"
        self.watermark_text_edit = LineEdit(); self.watermark_text_edit.setText(wm_default)
        text_row.addWidget(self.watermark_text_edit, 1)
        text_row.addWidget(BodyLabel("颜色:"))
        self.watermark_color = DEFAULT_WATERMARK_COLOR
        # 数据色展示按钮：底色=用户选的实际颜色（数据色），描边走系统调色板角色
        self.watermark_color_btn = widgets.ColorSwatchButton(self.watermark_color, 24)
        self.watermark_color_btn.clicked.connect(self._on_watermark_color)
        text_row.addWidget(self.watermark_color_btn)
        card.addLayout(text_row)

        self.watermark_size_row = widgets.SliderRow("字号", 10, 100, 1, 20, 0)
        card.addWidget(self.watermark_size_row)
        self.watermark_alpha_row = widgets.SliderRow("透明", 0, 1, 0.05, 0.15, 2)
        card.addWidget(self.watermark_alpha_row)
        self.watermark_angle_row = widgets.SliderRow("角度", 0, 360, 5, 30, 0)
        card.addWidget(self.watermark_angle_row)
        self.watermark_spacing_row = widgets.SliderRow("间距", 10, 200, 1, 25, 0)
        card.addWidget(self.watermark_spacing_row)
        v.addWidget(card)

        v.addStretch(1)
        return w

    # ----------------------------------------------------------
    #  模型
    # ----------------------------------------------------------
    def _tab_model(self):
        w = QWidget()
        # tab 页根布局：外边距交给页面根布局（24），此处 0 避免叠加；间距 12
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # 先探测缺失模型，确保默认选中“本地已有的模型”；
        # 缺失的模型在下方直接禁用（不可选），无需下载全部也能使用现有模型。
        s = self.c._settings
        miss_keys = self._detect_missing()

        def pick_default(cfg_key, models):
            cfg = s.get(cfg_key, models[0][0] if models else None)
            if cfg and cfg not in miss_keys and any(cfg == k for k, _ in models):
                return cfg
            # 回退到本类别第一个“存在”的模型；都没有则回退配置值（此时该类别全禁用）
            for k, _ in models:
                if k not in miss_keys:
                    return k
            return cfg

        matting_default = pick_default(
            "processing.default_matting_model", MATTING_MODELS)
        face_default = pick_default(
            "processing.default_face_model", FACE_MODELS)

        matting_card = widgets.CardSection("抠图模型")
        # 4 个长名 radio(>16 字符)在 300px 右栏横排会被挤切"修复发丝空洞"等长尾,
        # 改竖排与其它信息一行一项的节奏一致,不被栏宽卡。
        self.matting_group = widgets.RadioGroup(
            list(MATTING_MODELS),
            default=matting_default,
            orientation=Qt.Vertical,
            on_change=lambda v: self.c.on_model_changed(v))
        matting_card.addWidget(self.matting_group)
        v.addWidget(matting_card)

        face_card = widgets.CardSection("人脸检测模型")
        self.face_group = widgets.RadioGroup(
            list(FACE_MODELS),
            default=face_default,
            orientation=Qt.Vertical,
            on_change=lambda v: self.c.on_face_model_changed(v))
        face_card.addWidget(self.face_group)
        v.addWidget(face_card)

        # 缺失模型：禁用且标红（不可选）；存在的模型保持可选 + 默认色
        self._apply_model_states(miss_keys)

        # 模型下载功能已移入「设置」对话框；这里只显示就绪状态：
        # 缺模型时给出灰色按钮，点击跳转设置菜单（在那里下载）。
        self.model_status_inline = CaptionLabel("")
        self.model_status_inline.setWordWrap(True)
        v.addWidget(self.model_status_inline)

        self.model_goto_settings_btn = PushButton("模型未就绪，去设置下载")
        self.model_goto_settings_btn.clicked.connect(
            lambda: self.c.open_settings_dialog())
        self.model_goto_settings_btn.hide()  # 默认隐藏，缺模型时才显示
        v.addWidget(self.model_goto_settings_btn)

        tip = CaptionLabel("提示:切换模型后会重新加载,首次处理会稍慢。")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        return w

    # ----------------------------------------------------------
    #  回调 / 内部
    # ----------------------------------------------------------
    def _detect_missing(self):
        """返回本地缺失模型名的集合（与 MATTING_MODELS / FACE_MODELS 的 value 对齐）。"""
        try:
            from model_downloader import list_missing_models, BUILTIN_MANIFEST
            import os
            model_dir = getattr(self.c, 'model_dir', None) or getattr(self.c, '_model_dir', None)
            if not model_dir:
                model_dir = os.path.join(
                    getattr(self.c._settings, 'touch_dir', lambda: '.')(), 'models')
            miss = list_missing_models(model_dir)
            return {m.get("name", "") if isinstance(m, dict) else str(m) for m in miss}
        except Exception:
            return set()  # 检测失败时视为无缺失，避免误禁选项

    def _apply_model_states(self, miss_keys):
        """根据缺失集合刷新模型可选项：

        · 缺失 → 禁用（不可选）+ 文字标红 + 追加「（缺失）」后缀；
        · 存在 → 可选 + 还原默认文字色与原始文案（下载完成后刷新用）。

        qfluentwidgets.RadioButton 自绘文字（_drawText 用 self.textColor()），
        直接 setStyleSheet("color:...") 不生效，必须通过 lightTextColor /
        darkTextColor 属性（setter: setLightTextColor / setDarkTextColor）。
        """
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            QApplication = None
        # 统一字号：所有模型 RadioButton 复用应用默认字体，保证一致
        uniform_font = QApplication.font() if QApplication is not None else None

        RED = QColor("#EF4444")
        DEFAULT_LIGHT = QColor(0, 0, 0)        # Fluent 浅色主题默认文字
        DEFAULT_DARK = QColor(255, 255, 255)   # Fluent 深色主题默认文字

        def style_group(group, models):
            for val, text in models:
                rb = group.buttons.get(val)
                if rb is None:
                    continue
                if uniform_font is not None:
                    rb.setFont(uniform_font)
                if val in miss_keys:          # 缺失 → 禁用 + 红色 + 后缀
                    rb.setLightTextColor(RED)
                    rb.setDarkTextColor(RED)
                    rb.setText(text + "（缺失）")
                    rb.setEnabled(False)
                else:                          # 正常 → 可选 + 还原默认色 + 原文案
                    rb.setLightTextColor(DEFAULT_LIGHT)
                    rb.setDarkTextColor(DEFAULT_DARK)
                    rb.setText(text)
                    rb.setEnabled(True)
                rb.update()

        style_group(self.matting_group, MATTING_MODELS)
        style_group(self.face_group, FACE_MODELS)

    def refresh_model_states(self):
        """下载完成后重新探测缺失并刷新可选项（之前禁用的模型变为可选）。"""
        self._apply_model_states(self._detect_missing())

    def _on_color_selected(self, hex_val):
        self.bg_hex_edit.setText(hex_val.upper())

    def _apply_bg_hex(self):
        val = self.bg_hex_edit.text().strip().lstrip("#")
        if len(val) != 6 or any(c not in "0123456789ABCDEFabcdef" for c in val):
            self._warn("底色", "请输入 6 位十六进制色值，例如 438EDB")
            return
        self.color_group.select("#" + val.upper())

    def _on_watermark_color(self):
        color = QColorDialog.getColor(QColor(self.watermark_color), self, "选择水印颜色")
        if color.isValid():
            self.watermark_color = color.name().upper()
            self._update_watermark_color_btn()

    def _update_watermark_color_btn(self):
        """刷新水印色块：底色是数据色，描边由 ColorSwatchButton 内部走系统调色板。"""
        self.watermark_color_btn.set_color(self.watermark_color)

    def _warn(self, title, content, duration=2500):
        """统一警告条：直接用 Fluent 原生 InfoBar，不再经过自研主题层。"""
        InfoBar.warning(title, content, position=InfoBarPosition.TOP,
                        duration=duration, parent=self)

    def _reset_advanced(self):
        self.head_measure_row.set_value(0.20)
        self.head_height_row.set_value(0.45)
        self.head_top_max_edit.setText("0.12")
        self.head_top_min_edit.setText("0.10")
        self.face_conf_row.set_value(0.80)
        self.kb_limit_edit.setText("0")
        self.dpi_combo.setCurrentText("300")

    def _reset_beauty(self):
        for r in (self.whiten_row, self.brightness_row, self.contrast_row,
                  self.saturation_row, self.sharpen_row):
            r.set_value(0)

    def set_model_status(self, text, missing=False):
        """更新模型就绪状态。

        missing=False → 只显示状态文字（就绪）；
        missing=True  → 额外显示灰色「去设置下载」按钮，点击跳转设置菜单
                        （模型下载功能已移入设置对话框）。
        """
        self.model_status_inline.setText(text)
        self.model_goto_settings_btn.setVisible(bool(missing))

    # ----------------------------------------------------------
    #  取参 getter (供控制器收集)
    # ----------------------------------------------------------
    def get_background_color_bgr(self):
        rgb = hex_to_rgb_tuple(self.color_group.get_hex())
        return (rgb[2], rgb[1], rgb[0])

    def get_render_mode(self):
        return self.render_group.checkedId()

    def get_process_mode(self):
        return self.process_mode_group.get()

    def get_advanced_params(self):
        try:
            return {
                "head_measure_ratio": float(self.head_measure_row.value()),
                "head_height_ratio": float(self.head_height_row.value()),
                "head_top_range": (float(self.head_top_max_edit.text()),
                                   float(self.head_top_min_edit.text())),
                "face_confidence_threshold": float(self.face_conf_row.value()),
                "dpi": int(self.dpi_combo.currentText()),
                "kb_limit": int(self.kb_limit_edit.text() or "0"),
                "flip": self.flip_switch.isChecked(),
                "face_alignment": self.rotation_switch.isChecked(),
            }
        except (ValueError, TypeError):
            self._warn("参数错误", "请输入有效的高级参数数值。")
            return None

    def get_beauty_params(self):
        try:
            return {
                "whiten": int(self.whiten_row.value()),
                "brightness": int(self.brightness_row.value()),
                "contrast": int(self.contrast_row.value()),
                "saturation": int(self.saturation_row.value()),
                "sharpen": int(self.sharpen_row.value()),
            }
        except (ValueError, TypeError):
            return {"whiten": 0, "brightness": 0, "contrast": 0,
                    "saturation": 0, "sharpen": 0}

    def get_watermark_params(self):
        try:
            return {
                "enable": bool(self.watermark_enable_switch.isChecked()),
                "text": self.watermark_text_edit.text(),
                "color": self.watermark_color,
                "size": int(self.watermark_size_row.value()),
                "alpha": float(self.watermark_alpha_row.value()),
                "angle": int(self.watermark_angle_row.value()),
                "spacing": int(self.watermark_spacing_row.value()),
            }
        except (ValueError, TypeError):
            return {"enable": False}

    def get_matting_model(self):
        return self.matting_group.get()

    def get_face_model(self):
        return self.face_group.get()
