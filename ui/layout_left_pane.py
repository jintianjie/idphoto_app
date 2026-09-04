# -*- coding: utf-8 -*-
"""排版打印页左列面板
============================================================
从 LayoutView 中抽出: 成品证件照列表 + 排版规格配置。
只暴露控制器需要的方法, 通过 controller (self.c) 回连。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QSizePolicy,
)
from qfluentwidgets import (
    ComboBox, SpinBox, FluentIcon, Dialog, MessageBox, LineEdit,
    TransparentToolButton,
)

from . import theme, widgets
from styles import (PRESET_SIZES, PRESET_COLORS,
                    DEFAULT_COUNT_BY_SIZE, DEFAULT_COUNT_FALLBACK)

LAYOUT_BG_OPTIONS = [("默认（处理时颜色）", "")] + PRESET_COLORS

# 内置规格名（不可删除/重命名）
BUILTIN_SIZE_NAMES = tuple(name for name, _, _ in PRESET_SIZES)


class LayoutLeftPane(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.mixed_items = []
        self._custom_presets = []      # [[名称, 宽, 高, 默认张数], ...]
        self._current_w = 295          # 当前选中规格的宽（内部存储，无 UI 输入框）
        self._current_h = 413          # 当前选中规格的高
        self._build()
        self._load_custom_presets()
        # 注册主题感知：浅/深切换时重染数量数字框（弱引用，无需手动反注册）
        theme.register_theme_widget(self)

    def refresh_theme(self):
        """浅/深切换时重染数量数字框 + 两个裸列表配色（list_qss 跟随调色板）。"""
        self.mixed_count_spin.setStyleSheet(theme.spinbox_qss("QSpinBox"))
        self.ready_listbox.setStyleSheet(theme.list_qss())
        self.mixed_listbox.setStyleSheet(theme.list_qss())

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        v.addWidget(self._build_ready_card(), 5)
        v.addWidget(self._build_mixed_card(), 0)

    # ----------------------------------------------------------
    #  成品证件照
    # ----------------------------------------------------------
    def _build_ready_card(self):
        card = theme.SectionCard("成品证件照")
        self.ready_listbox = QListWidget()
        # 显式配色：Win11 系统深色模式下原生样式给裸列表配深底深字/白底白字，
        # QPalette 修不住，必须 list_qss() 覆盖（浅色=浅底深字，深色=深底浅字）
        self.ready_listbox.setStyleSheet(theme.list_qss())
        self.ready_listbox.setSelectionMode(QListWidget.SingleSelection)
        self.ready_listbox.currentRowChanged.connect(
            lambda _: self._on_ready_select())
        card.addWidget(self.ready_listbox, 1)

        r_ops = QHBoxLayout()
        r_ops.setSpacing(4)
        r_ops.addWidget(theme.compact_primary_button(
            "导入", FluentIcon.FOLDER, lambda: self.c.on_add_ready_photos()))
        r_ops.addWidget(theme.compact_danger_button(
            "移除", FluentIcon.REMOVE, lambda: self._on_remove_ready_selected()))
        r_ops.addWidget(theme.compact_secondary_button(
            "清空", FluentIcon.DELETE, lambda: self.c.on_clear_ready_photos()))
        card.addLayout(r_ops)
        return card

    # ----------------------------------------------------------
    #  排版规格
    # ----------------------------------------------------------
    def _build_mixed_card(self):
        card = theme.SectionCard("排版规格")

        # 行 1：规格 + 背景挤一行（用占位文字代替标签，选中后占位消失）
        size_bg_row = QHBoxLayout()
        size_bg_row.setSpacing(6)
        self.mixed_size_combo = ComboBox()
        self.mixed_size_combo.setMinimumWidth(80)
        self.mixed_size_combo.currentTextChanged.connect(self._on_size_sync)
        self.mixed_size_combo.setPlaceholderText("规格")
        size_bg_row.addWidget(self.mixed_size_combo, 1)
        self.mixed_bg_combo = ComboBox()
        self.mixed_bg_combo.addItems([label for label, _ in LAYOUT_BG_OPTIONS])
        # addItems 会自动选中第 0 项，手动取消选中以显示占位文字「背景」
        self.mixed_bg_combo.setPlaceholderText("背景")
        self.mixed_bg_combo.setCurrentIndex(-1)
        self.mixed_bg_combo.setMinimumWidth(100)
        size_bg_row.addWidget(self.mixed_bg_combo, 1)
        card.addLayout(size_bg_row)

        # 行 2：数量 + 添加按钮
        count_add_row = QHBoxLayout()
        count_add_row.setSpacing(6)
        count_add_row.addWidget(theme.small_label("数量："))
        self.mixed_count_spin = SpinBox()
        self.mixed_count_spin.setRange(1, 99)
        self.mixed_count_spin.setValue(8)
        self.mixed_count_spin.setFixedWidth(72)
        self.mixed_count_spin.setStyleSheet(theme.spinbox_qss("QSpinBox"))
        self.mixed_count_spin.lineEdit().setAlignment(Qt.AlignCenter)
        count_add_row.addWidget(self.mixed_count_spin)
        count_add_row.addStretch(1)
        count_add_row.addWidget(theme.compact_primary_button(
            "添加", None, lambda: self._on_add_mixed_item()))
        card.addLayout(count_add_row)

        # 排版列表 + 操作按钮
        self.mixed_listbox = QListWidget()
        self.mixed_listbox.setMaximumHeight(80)
        self.mixed_listbox.setStyleSheet(theme.list_qss())
        self.mixed_listbox.setSelectionMode(QListWidget.SingleSelection)
        card.addWidget(self.mixed_listbox)

        list_btns = QHBoxLayout()
        list_btns.setSpacing(4)
        list_btns.addWidget(theme.compact_danger_button(
            "删除", None, lambda: self._on_remove_mixed_item()))
        list_btns.addWidget(theme.compact_primary_button(
            "排版", FluentIcon.LAYOUT, lambda: self.c.on_generate_mixed_layout()))
        list_btns.addWidget(theme.compact_secondary_button(
            "清空", None, lambda: self._on_clear_mixed_list()))
        card.addLayout(list_btns)
        return card

    # ----------------------------------------------------------
    #  内部处理
    # ----------------------------------------------------------
    # ---- 自定义规格 ----
    def _settings(self):
        """控制器上的设置存储（无则 None，全部调用点都要容错）。"""
        return getattr(self.c, "_settings", None)

    def _load_custom_presets(self):
        """从 settings 读自定义规格并重建下拉。"""
        raw = None
        s = self._settings()
        if s is not None:
            try:
                raw = s.get("layout.custom_presets", [])
            except Exception:
                raw = None
        self._custom_presets = self._normalize_presets(raw)
        # 初始不预选，让占位文字「规格」显示
        self._reload_size_combo(select=None)

    @staticmethod
    def _normalize_presets(raw):
        """把 settings 里的原始值洗成 [[名称, 宽, 高, 数量], ...]。

        过滤条件：4 元素、名称非空字符串、宽高数量为正整数。
        """
        out = []
        if not isinstance(raw, (list, tuple)):
            return out
        for row in raw:
            try:
                name, w, h, count = row[0], int(row[1]), int(row[2]), int(row[3])
            except (TypeError, ValueError, IndexError):
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            if w <= 0 or h <= 0 or count <= 0:
                continue
            out.append([name.strip(), w, h, count])
        return out

    def _reload_size_combo(self, select=None):
        """重建规格下拉：内置规格 + 自定义规格。

        - select 为具体名称时预选该项（新增/修改自定义规格后定位用）。
        - select 为 None 时不预选，显示占位文字「规格」（选中后占位消失）。
        """
        self.mixed_size_combo.blockSignals(True)
        self.mixed_size_combo.clear()
        for name, _, _ in PRESET_SIZES:
            self.mixed_size_combo.addItem(name)
        for name, _, _, _ in self._custom_presets:
            self.mixed_size_combo.addItem(name)
        if select and self.mixed_size_combo.findText(select) >= 0:
            self.mixed_size_combo.setCurrentText(select)
        else:
            # 不预选：让占位文字「规格」显示，选中后自动隐藏
            self.mixed_size_combo.setPlaceholderText("规格")
            self.mixed_size_combo.setCurrentIndex(-1)
        self.mixed_size_combo.blockSignals(False)

    def _save_custom_presets(self):
        """写回 settings 并落盘。"""
        s = self._settings()
        if s is None:
            return
        s.set("layout.custom_presets", [list(p) for p in self._custom_presets])
        try:
            s.save()
        except Exception:
            pass

    def _read_wh_count(self):
        """读当前 宽/高/数量（宽高从内部存储读取，无 UI 输入框）。"""
        try:
            w = int(self._current_w)
            h = int(self._current_h)
            count = int(self.mixed_count_spin.value())
        except (ValueError, TypeError):
            return None
        if w <= 0 or h <= 0 or count <= 0:
            return None
        return w, h, count

    def _on_add_preset(self):
        """＋ 按钮：把当前规格（内部宽高+数量）保存为自定义规格。"""
        whc = self._read_wh_count()
        if whc is None:
            theme.info(self, "warning", "参数错误",
                       "请先选择有效的规格。")
            return
        w, h, count = whc

        dlg = Dialog("保存为自定义规格", "名称不能与内置规格重复。", self.window(),
                     yesText="确定", cancelText="取消")
        name_edit = LineEdit(dlg)
        name_edit.setPlaceholderText("例如：社保照")
        name_edit.setClearButtonEnabled(True)
        default_name = self.mixed_size_combo.currentText()
        if default_name in BUILTIN_SIZE_NAMES:
            default_name = ""
        name_edit.setText(default_name)
        dlg.textLayout.addWidget(name_edit)
        if not dlg.exec():
            return

        name = name_edit.text().strip()
        if not name:
            theme.info(self, "warning", "名称为空", "请输入规格名称。")
            return
        if name in BUILTIN_SIZE_NAMES:
            theme.info(self, "warning", "名称冲突",
                       f"「{name}」是内置规格，请换一个名称。")
            return
        if any(p[0] == name for p in self._custom_presets):
            theme.info(self, "warning", "名称冲突",
                       f"自定义规格「{name}」已存在。")
            return

        self._custom_presets.append([name, w, h, count])
        self._save_custom_presets()
        self._reload_size_combo(select=name)
        theme.info(self, "success", "已保存",
                   f"自定义规格「{name}」{w}x{h}，默认 {count} 张。")

    def _on_del_preset(self):
        """－ 按钮：删除当前选中的自定义规格（内置规格不允许删）。"""
        name = self.mixed_size_combo.currentText()
        if name in BUILTIN_SIZE_NAMES:
            theme.info(self, "warning", "无法删除",
                       f"「{name}」是内置规格，不能删除。")
            return
        if not any(p[0] == name for p in self._custom_presets):
            theme.info(self, "warning", "提示", "请先选中一个自定义规格。")
            return
        box = MessageBox("删除规格",
                         f"确定删除自定义规格「{name}」吗？", self.window())
        if not box.exec():
            return
        self._custom_presets = [p for p in self._custom_presets
                                if p[0] != name]
        self._save_custom_presets()
        self._reload_size_combo(select="一寸")
        self._on_size_sync()
        theme.info(self, "success", "已删除", f"自定义规格「{name}」已删除。")

    def _on_size_sync(self):
        """选择规格时：内部存储宽/高 + 默认数量回填到控件。

        - 自定义规格：回填其保存时的宽/高/数量
        - 内置规格：宽/高取 PRESET_SIZES，数量按 DEFAULT_COUNT_BY_SIZE
        - 宽高仅内部存储（_current_w / _current_h），不再有 UI 输入框
        """
        selected = self.mixed_size_combo.currentText()
        for name, w, h, count in self._custom_presets:
            if name == selected:
                self._current_w = int(w)
                self._current_h = int(h)
                self.mixed_count_spin.setValue(int(count))
                return
        for name, w, h in PRESET_SIZES:
            if name == selected and w > 0 and h > 0:
                self._current_w = int(w)
                self._current_h = int(h)
                break
        default_count = DEFAULT_COUNT_BY_SIZE.get(
            selected, DEFAULT_COUNT_FALLBACK)
        self.mixed_count_spin.setValue(int(default_count))

    def _bg_hex_for_display(self, display):
        for label, hex_val in LAYOUT_BG_OPTIONS:
            if label == display:
                return hex_val
        return ""

    def _on_add_mixed_item(self):
        try:
            w = int(self._current_w)
            h = int(self._current_h)
            count = int(self.mixed_count_spin.value())
            if w <= 0 or h <= 0 or count <= 0:
                raise ValueError
        except (ValueError, TypeError):
            theme.info(self, "warning", "参数错误", "请先选择有效的规格。")
            return
        name = self.mixed_size_combo.currentText()
        if not name:  # 占位文字「规格」状态下 currentText() 为空，回退默认
            name = "一寸"
        bg_display = self.mixed_bg_combo.currentText()
        bg_hex = self._bg_hex_for_display(bg_display) if bg_display else ""
        bg_label = "默认" if not bg_hex else next(
            (label for label, val in LAYOUT_BG_OPTIONS if val == bg_hex), "自定义")
        display = f"{name}  {w}x{h}  每人x{count}张  {bg_label}"
        self.mixed_items.append((name, w, h, count, bg_hex))
        self.mixed_listbox.addItem(display)

    def _on_remove_mixed_item(self):
        sel = self.mixed_listbox.currentRow()
        if sel >= 0:
            self.mixed_listbox.takeItem(sel)
            self.mixed_items.pop(sel)

    def _on_clear_mixed_list(self):
        self.mixed_listbox.clear()
        self.mixed_items.clear()

    def _on_ready_select(self):
        idx = self.get_selected_ready_idx()
        if idx >= 0:
            self.c.on_select_ready_photo(idx)

    def _on_remove_ready_selected(self):
        idx = self.get_selected_ready_idx()
        if idx < 0:
            theme.info(self, "warning", "提示", "请先在列表中选中一张成品照片。")
            return
        self.c.on_remove_ready_photo(idx)

    # ============================================================
    #  控制器调用
    # ============================================================
    def refresh_ready_list(self):
        lb = self.ready_listbox
        lb.clear()
        for item in self.c.ready_photos:
            h, w = item["image"].shape[:2]
            name = item["name"]
            if len(name) > 18:
                name = name[:18] + "…"
            lb.addItem(f"{name}  {w}x{h}")

    def get_selected_ready_idx(self):
        return self.ready_listbox.currentRow()

    def get_mixed_items(self):
        return list(self.mixed_items)
