# -*- coding: utf-8 -*-
"""排版打印页 — 仅右侧参数面板 (成品/排版已拆到 LayoutLeftPane)
============================================================
构建: 纸张与打印设置 + 选项 + 导出。所有取参 getter 供控制器调用,
用户操作通过 self.c 回调交给控制器。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
)
from qfluentwidgets import (
    ComboBox, FluentIcon, BodyLabel,
)

from . import theme, widgets
from styles import PAPER_PRESETS, PRINT_DPI

# 导出区大按钮样式（调用 theme 的主题感知函数，跟随浅/深模式与强调色）


class LayoutView(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.layout_pages = []
        self.current_page_idx = 0
        self._legend_swatches = []  # 配色图例色块，供主题切换时重染描边
        self._custom_paper_presets = []  # [[名称, 宽mm, 高mm], ...]
        self._build()
        self._load_custom_paper_presets()
        # 注册主题感知：浅/深切换时重染自定义色块描边（弱引用，无需手动反注册）
        theme.register_theme_widget(self)

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        v.addWidget(self._build_paper_card(), 0)
        v.addWidget(self._build_options_card(), 0)
        v.addWidget(self._build_export_card(), 0)

        # 所有提示统一放在底部居中（不嵌在任何卡片内）
        self._tip_label = theme.tip_label(
            "提示:0 表示照片紧挨,只显示一条公共裁切线。")
        self._tip_label.setAlignment(Qt.AlignCenter)
        self._tip_label.setWordWrap(True)
        v.addWidget(self._tip_label)
        v.addStretch(1)

    # ----------------------------------------------------------
    #  纸张与打印设置
    # ----------------------------------------------------------
    def _build_paper_card(self):
        card = theme.SectionCard("纸张与打印设置")
        # 行 1：纸张规格 + 自定义增删（＋ 保存自定义纸张，－ 删除选中的自定义纸张）
        paper_row = QHBoxLayout()
        paper_row.setSpacing(4)
        paper_row.addWidget(theme.small_label("纸张规格:"))
        self.paper_combo = ComboBox()
        self.paper_combo.currentTextChanged.connect(self._on_paper_changed)
        paper_row.addWidget(self.paper_combo, 1)
        card.addLayout(paper_row)

        self.orientation_group = widgets.RadioGroup(
            [("portrait", "纵向"), ("landscape", "横向")],
            default="portrait",
            on_change=lambda _: self._on_paper_changed())
        card.addWidget(self.orientation_group)

        # 打印机行：标签 + Combo + 属性按钮。Combo 拉满 stretch，属性按钮紧凑
        printer_row = QHBoxLayout()
        printer_row.setSpacing(4)
        printer_row.addWidget(theme.small_label("打印机:"))
        self.printer_combo = ComboBox()
        self.printer_combo.addItems(["系统默认"])
        printer_row.addWidget(self.printer_combo, 1)
        printer_row.addWidget(theme.compact_secondary_button(
            "属性", None, lambda: self.c.on_printer_properties()))
        card.addLayout(printer_row)
        return card

    # ----------------------------------------------------------
    #  选项
    # ----------------------------------------------------------
    def _build_options_card(self):
        card = theme.SectionCard("选项")
        s = self.c._settings if hasattr(self.c, "_settings") else None

        crop_default = bool(s.get("layout.show_crop_line", True)) if s else True
        self.crop_line_switch = self._switch_row(card, "显示裁剪线", crop_default)
        self.crop_line_switch.checkedChanged.connect(self._on_layout_param_changed)
        crop_full_default = bool(s.get("layout.crop_line_full_page", True)) if s else True
        self.crop_line_full_switch = self._switch_row(card, "空白区也绘制切割线", crop_full_default)
        self.crop_line_full_switch.checkedChanged.connect(self._on_layout_param_changed)

        legend_row = QHBoxLayout()
        legend_row.setSpacing(4)
        legend_row.addWidget(theme.small_label("配色:"))
        self.crop_legend_inner = QHBoxLayout()
        self.crop_legend_inner.setSpacing(4)
        legend_row.addLayout(self.crop_legend_inner)
        legend_row.addStretch(1)
        card.addLayout(legend_row)

        # 出血 + 间距：用更小字号的小标签，压缩行高；默认值从 settings 读
        bleed_default = int(s.get("layout.default_bleed", 50) or 50) if s else 50
        bleed_row = QHBoxLayout()
        bleed_row.setSpacing(4)
        bleed_row.addWidget(theme.tip_label("出血线:"))
        self.bleed_edit = theme.themed_line_edit()
        self.bleed_edit.setText(str(bleed_default))
        self.bleed_edit.setFixedWidth(48)
        bleed_row.addWidget(self.bleed_edit)
        bleed_row.addWidget(theme.tip_label("px"))
        bleed_row.addStretch(1)
        card.addLayout(bleed_row)

        gap_h_default = int(s.get("layout.default_gap_h", 1) or 1) if s else 1
        gap_v_default = int(s.get("layout.default_gap_v", 1) or 1) if s else 1
        gap_row = QHBoxLayout()
        gap_row.setSpacing(4)
        gap_row.addWidget(theme.tip_label("横距:"))
        self.gap_h_edit = theme.themed_line_edit()
        self.gap_h_edit.setText(str(gap_h_default))
        self.gap_h_edit.setFixedWidth(48)
        gap_row.addWidget(self.gap_h_edit)
        gap_row.addSpacing(6)
        gap_row.addWidget(theme.tip_label("纵距:"))
        self.gap_v_edit = theme.themed_line_edit()
        self.gap_v_edit.setText(str(gap_v_default))
        self.gap_v_edit.setFixedWidth(48)
        gap_row.addWidget(self.gap_v_edit)
        gap_row.addStretch(1)
        card.addLayout(gap_row)

        # 数值变化时刷新预览（让位置标记/小圆点跟随更新）
        self.bleed_edit.textChanged.connect(self._on_layout_param_changed)
        self.gap_h_edit.textChanged.connect(self._on_layout_param_changed)
        self.gap_v_edit.textChanged.connect(self._on_layout_param_changed)
        return card

    @staticmethod
    def _switch_row(card, label, default):
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(theme.small_label(label))
        # 用 theme.themed_switch() 替代原始 SwitchButton：
        # OFF 状态的轨道/把手颜色改成跟随调色板（border/text2），
        # 不再像灰色进度条；ON 仍用强调色。
        sw = theme.themed_switch()
        sw.setChecked(default)
        row.addWidget(sw)
        row.addStretch(1)
        card.addLayout(row)
        return sw

    # ----------------------------------------------------------
    #  导出
    # ----------------------------------------------------------
    def _build_export_card(self):
        card = theme.SectionCard("导出")
        # 默认输出格式：先看 settings.export.default_format，否则 png
        s = self.c._settings if hasattr(self.c, "_settings") else None
        fmt_default = (s.get("export.default_format", "png")
                       if s is not None else "png")
        if fmt_default not in ("png", "jpg"):
            fmt_default = "png"
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(4)
        fmt_row.addWidget(theme.small_label("输出格式:"))
        self.export_format_group = widgets.RadioGroup(
            [("png", "PNG"), ("jpg", "JPEG")], default=fmt_default)
        fmt_row.addWidget(self.export_format_group)
        fmt_row.addStretch(1)
        card.addLayout(fmt_row)

        btn_grid_top = QHBoxLayout()
        btn_grid_top.setSpacing(10)          # 列间距（"中间"）
        btn_grid_bot = QHBoxLayout()
        btn_grid_bot.setSpacing(10)
        export_buttons = [
            ("保存标准证件照", "standard", True),
            ("保存高清证件照", "hd", True),
            ("保存透明底抠图", "matting", False),
            ("保存排版图", "layout", True),
        ]
        # 上排：standard | hd ；下排：matting | layout
        # 用 setStretch 让两列等宽
        for i, (label, key, primary) in enumerate(export_buttons):
            btn = theme.block_button(
                label, primary, None, lambda k=key: self.c.on_export(k))
            (btn_grid_top if i < 2 else btn_grid_bot).addWidget(btn, 1)
        # 上下两行都拉满整行宽度，并加上更大的行间距
        card.addLayout(btn_grid_top)
        card.addSpacing(8)
        card.addLayout(btn_grid_bot)

        # 打印：把当前排版页直接送打印机（系统打印对话框，带入已选打印机）
        print_row = QHBoxLayout()
        print_row.setSpacing(10)
        print_btn = theme.block_button(
            "打印排版图", True, FluentIcon.PRINT,
            lambda: self.c.on_print())
        print_row.addWidget(print_btn, 1)
        card.addSpacing(8)
        card.addLayout(print_row)
        return card

    # ----------------------------------------------------------
    #  内部处理
    # ----------------------------------------------------------
    # ---- 自定义纸张规格 ----
    def _settings(self):
        """控制器上的设置存储（无则 None，全部调用点都要容错）。"""
        return getattr(self.c, "_settings", None)

    def _load_custom_paper_presets(self):
        """从 settings 读自定义纸张并重建下拉（默认选中 A4）。"""
        raw = None
        s = self._settings()
        if s is not None:
            try:
                raw = s.get("layout.custom_paper_presets", [])
            except Exception:
                raw = None
        self._custom_paper_presets = self._normalize_paper(raw)
        self._reload_paper_combo(select="A4")

    @staticmethod
    def _normalize_paper(raw):
        """把 settings 原始值洗成 [[名称, 宽mm, 高mm], ...]，脏数据过滤。"""
        out = []
        if not isinstance(raw, (list, tuple)):
            return out
        for row in raw:
            try:
                name, w, h = row[0], int(row[1]), int(row[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            if w <= 0 or h <= 0:
                continue
            out.append([name.strip(), w, h])
        return out

    def _reload_paper_combo(self, select=None):
        """重建纸张下拉：内置规格 + 自定义规格，尽量保持原选中项。"""
        prev = select if select is not None else \
            self.paper_combo.currentText()
        self.paper_combo.blockSignals(True)
        self.paper_combo.clear()
        self.paper_combo.addItems([p[0] for p in PAPER_PRESETS])
        for name, _, _ in self._custom_paper_presets:
            self.paper_combo.addItem(name)
        if prev and self.paper_combo.findText(prev) >= 0:
            self.paper_combo.setCurrentText(prev)
        self.paper_combo.blockSignals(False)

    def _save_custom_paper_presets(self):
        s = self._settings()
        if s is None:
            return
        s.set("layout.custom_paper_presets",
              [list(p) for p in self._custom_paper_presets])
        try:
            s.save()
        except Exception:
            pass

    def _on_paper_changed(self):
        self.c.layout_pages.clear()
        self.c._refresh_right_previews()

    def _on_layout_param_changed(self):
        """出血线/横距/纵距数值变化时刷新排版预览。"""
        self.c._refresh_right_previews()

    def _update_crop_legend(self, size_color_labels):
        while self.crop_legend_inner.count():
            item = self.crop_legend_inner.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._legend_swatches = []
        if not size_color_labels:
            return
        for label, (b, g, r) in size_color_labels:
            sw = QLabel()
            sw.setFixedSize(14, 14)
            sw.setStyleSheet(
                f"background-color:rgb({r},{g},{b});"
                f"border:1px solid {theme.pal('border')};border-radius:3px;")
            self._legend_swatches.append((sw, (r, g, b)))
            self.crop_legend_inner.addWidget(sw)
            self.crop_legend_inner.addWidget(BodyLabel(label))

    def refresh_theme(self):
        """浅/深切换时重染配色图例色块描边（色块背景不变，仅描边跟随主题）。"""
        for sw, (r, g, b) in self._legend_swatches:
            sw.setStyleSheet(
                f"background-color:rgb({r},{g},{b});"
                f"border:1px solid {theme.pal('border')};border-radius:3px;")

    # ----------------------------------------------------------
    #  控制器调用 getter
    # ----------------------------------------------------------
    def get_paper_px(self):
        selected = self.paper_combo.currentText()
        orient = self.orientation_group.get()
        # 内置规格优先，再查自定义规格；都按 横向时宽高互换 处理
        for pool in (PAPER_PRESETS, self._custom_paper_presets):
            for name, w_mm, h_mm in pool:
                if name == selected:
                    if orient == "landscape":
                        w_mm, h_mm = h_mm, w_mm
                    pw = round(w_mm * PRINT_DPI / 25.4)
                    ph = round(h_mm * PRINT_DPI / 25.4)
                    return pw, ph
        return 1795, 1205

    def get_crop_line(self):
        return self.crop_line_switch.isChecked()

    def get_crop_line_full_page(self):
        return self.crop_line_full_switch.isChecked()

    def get_photo_interval(self):
        try:
            h = max(0, int(self.gap_h_edit.text() or "0"))
        except (ValueError, TypeError):
            h = 0
        try:
            v = max(0, int(self.gap_v_edit.text() or "0"))
        except (ValueError, TypeError):
            v = 0
        return h, v

    def get_bleed_margin(self):
        try:
            return max(0, int(self.bleed_edit.text() or "0"))
        except (ValueError, TypeError):
            return 50

    def get_format(self):
        return self.export_format_group.get()

    def get_printer_name(self):
        name = self.printer_combo.currentText()
        return None if name in ("系统默认", "") else name

    def set_printer_list(self, printers):
        self.printer_combo.clear()
        self.printer_combo.addItems(printers)
        # 记住上次选择的打印机：settings 中有记录且仍在列表里则自动选中，
        # 这样「打印」时会直接带入该打印机，无需每次重新选择。
        last = None
        s = self._settings()
        if s is not None:
            try:
                last = s.get("print.last_printer", None)
            except Exception:
                last = None
        if last and last in printers:
            self.printer_combo.setCurrentText(last)
        elif self.printer_combo.currentText() not in printers:
            self.printer_combo.setCurrentText(printers[0] if printers else "系统默认")

    def set_layout_pages(self, pages, size_color_labels=None):
        self.layout_pages = pages
        self.current_page_idx = 0
        if size_color_labels is not None:
            self._update_crop_legend(size_color_labels)
        self.c._refresh_layout_thumbnails(pages)
        self.c._refresh_right_previews()
