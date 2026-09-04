# -*- coding: utf-8 -*-
"""处理队列面板 (左侧列)
============================================================
从 ProcessView 中抽出: 队列缩略图 + 添加/移除/清空 + 进度条。
只暴露控制器需要的方法, 通过 controller (self.c) 回连。

UI 规范（本项目统一约定）
------------------------------------------------------------
· 可视控件全部使用 qfluentwidgets（PrimaryPushButton / PushButton /
  StrongBodyLabel / BodyLabel / CaptionLabel / ProgressBar / CardWidget）。
· 内容用 CardWidget 包裹，卡片内边距 (20,20,20,20)、间距 10。
· 文字颜色 / 卡片底色全部由 Fluent 主题系统托管，本文件无任何手写配色。
"""
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout,
)
from qfluentwidgets import (
    CardWidget, PrimaryPushButton, PushButton, FluentIcon,
    StrongBodyLabel, BodyLabel, CaptionLabel, ProgressBar,
    InfoBar, InfoBarPosition,
)

from . import widgets


class ProcessQueuePane(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        # ---- 卡片容器：CardWidget 自绘底色，随明暗自动变化（无需手写 QSS）----
        self.card = CardWidget()
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(14, 14, 14, 14)
        card_lay.setSpacing(10)

        self.title_label = StrongBodyLabel("处理队列")
        card_lay.addWidget(self.title_label)
        card_lay.addWidget(CaptionLabel("支持拖拽图片到软件任意位置快速导入"))

        # 上下排列（默认竖向流）——处理队列是竖列面板，不做横向滚动
        self.thumb_list = widgets.ThumbnailList(
            thumb_size=80, name_max_len=24)
        self.thumb_list.itemSelected.connect(
            lambda i: self.c.on_select_queue_image(i))
        self.thumb_list.itemDoubleClickedSig.connect(
            lambda i: self.c.on_queue_zoom(i))
        # stretch=1：列表占满卡片剩余高度（队列本就该撑满，故根布局不再加 stretch）
        card_lay.addWidget(self.thumb_list, 1)

        # 按钮三行竖排（每行占满卡片宽度）：面板只有 ~220px 宽，
        # 三按钮挤一行会被压成图标+残字（2026-09-03 截图反馈），竖排最稳。
        btn_col = QVBoxLayout()
        btn_col.setSpacing(8)
        self.add_btn = PrimaryPushButton(FluentIcon.ADD, "添加")
        self.add_btn.clicked.connect(lambda: self.c.on_add_images())
        self.remove_btn = PushButton(FluentIcon.REMOVE, "移除")
        self.remove_btn.clicked.connect(self._on_remove_queue)
        self.clear_btn = PushButton(FluentIcon.DELETE, "清空")
        self.clear_btn.clicked.connect(lambda: self.c.on_clear_all_images())
        btn_col.addWidget(self.add_btn)
        btn_col.addWidget(self.remove_btn)
        btn_col.addWidget(self.clear_btn)
        card_lay.addLayout(btn_col)

        card_lay.addWidget(BodyLabel("处理进度"))
        self.progress = ProgressBar()
        self.progress.setValue(0)
        card_lay.addWidget(self.progress)
        self.progress_label = CaptionLabel("就绪")
        card_lay.addWidget(self.progress_label)

        v.addWidget(self.card)

    def _on_remove_queue(self):
        idx = self.thumb_list.currentRow()
        if idx < 0:
            InfoBar.warning(
                title="提示", content="请先在队列中选中一张图片。",
                position=InfoBarPosition.TOP, duration=2500, parent=self,
            )
            return
        self.c.on_remove_queue_image(idx)

    # ============================================================
    #  控制器调用的方法（对外契约）
    # ============================================================
    def update_queue(self, items):
        self.thumb_list.set_items(items)
        for idx in self.c.processed_results:
            self.thumb_list.set_status(idx, "✓")

    def append_queue_items(self, items):
        self.thumb_list.append_items(items)

    def select_queue_index(self, index):
        self.thumb_list.select_index(index)

    def set_queue_status(self, idx, text):
        self.thumb_list.set_status(idx, text)

    def clear_queue_status(self):
        for i in range(len(self.thumb_list.item_widgets)):
            self.thumb_list.reset_item_progress(i)

    def set_item_active(self, idx):
        self.thumb_list.set_item_active(idx)

    def set_item_progress(self, idx, pct):
        """单项处理进度（0~100），显示在该条目名称下面的进度条上。"""
        self.thumb_list.set_status(idx, str(int(pct)))

    def set_item_progress_done(self, idx):
        self.thumb_list.set_item_progress_done(idx)

    def reset_item_progress(self, idx):
        self.thumb_list.reset_item_progress(idx)

    def scroll_to_item(self, idx):
        self.thumb_list.scroll_to_item(idx)

    def set_progress(self, value, text=""):
        self.progress.setValue(int(value))
        if text:
            self.progress_label.setText(text)

    def set_processing_state(self, is_processing):
        self.progress_label.setText("处理中..." if is_processing else "就绪")
