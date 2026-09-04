# -*- coding: utf-8 -*-
"""
MirrorManagerDialog — 高速下载解锁对话框（只读）
================================================

职责（极简）：
  1. 显示配置位置与锁定状态
  2. 用户输入作者提供的密码 → 解锁 → 主程序拿到高速直链
  3. 可随时「锁定」收回密码

本对话框**不提供任何写能力**（改密码 / 重置 / 重新加密一律没有）。
写入密码和地址是「生成器软件」的事，主程序只负责读。

调用方式::

    dlg = MirrorManagerDialog(mirror_manager, parent=self)
    dlg.exec()
"""
import os
import subprocess
from typing import Optional

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QWidget
from PySide6.QtCore import Qt

from PySide6.QtWidgets import QLineEdit
from qfluentwidgets import (
    PushButton, PrimaryPushButton, BodyLabel, SubtitleLabel,
    FluentIcon, InfoBar, InfoBarPosition, MessageBox,
)

from . import theme


class MirrorManagerDialog(QDialog):
    def __init__(self, mirror_manager, parent=None):
        super().__init__(parent)
        self.mgr = mirror_manager
        self.setWindowTitle("高速下载解锁")
        self.resize(560, 320)
        if parent is not None:
            self.setWindowModality(Qt.WindowModal)
        self._build()
        self._apply_dialog_theme()
        self._sync_state()

    # ----------------------------------------------------------
    def _apply_dialog_theme(self):
        """对话框底色跟随当前主题。"""
        bg = theme.pal("bg")
        self.setStyleSheet(f"QDialog{{background-color:{bg};}}")

    # ----------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        root.addWidget(SubtitleLabel("高速下载解锁"))
        root.addWidget(BodyLabel(
            "高速下载地址由作者加密后随软件分发。\n"
            "输入作者提供的密码即可解锁；主程序只读取地址并下载，不做任何改写。"
        ))

        # 状态区
        self.status_box = QWidget()
        sv = QVBoxLayout(self.status_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(4)
        self.file_label = BodyLabel("")
        self.state_label = BodyLabel("")
        sv.addWidget(self.file_label)
        sv.addWidget(self.state_label)
        root.addWidget(self.status_box)

        # 密码输入行
        pwd_row = QHBoxLayout()
        pwd_row.setSpacing(6)
        self.pwd_edit = theme.themed_line_edit()
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        self.pwd_edit.setPlaceholderText("输入高速下载密码")
        self.pwd_edit.returnPressed.connect(self._on_unlock)
        unlock_btn = PrimaryPushButton("解锁")
        unlock_btn.setIcon(FluentIcon.LIBRARY.qicon())
        unlock_btn.clicked.connect(self._on_unlock)
        lock_btn = PushButton("锁定")
        lock_btn.clicked.connect(self._on_lock)
        pwd_row.addWidget(self.pwd_edit, 1)
        pwd_row.addWidget(unlock_btn)
        pwd_row.addWidget(lock_btn)
        root.addLayout(pwd_row)

        self.hint_label = BodyLabel("")
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

        root.addStretch(1)

        # 底部
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        open_btn = PushButton(FluentIcon.FOLDER.qicon(), "打开配置目录")
        open_btn.clicked.connect(self._on_open_dir)
        close_btn = PushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(open_btn)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # ----------------------------------------------------------
    def _on_unlock(self):
        pwd = self.pwd_edit.text()
        if not pwd:
            InfoBar.warning(title="请输入密码", content="向软件作者索取高速下载密码。",
                            parent=self, position=InfoBarPosition.TOP)
            return
        if not self.mgr.has_highspeed_config():
            InfoBar.warning(title="无加密配置", content="config 中没有加密高速镜像。",
                            parent=self, position=InfoBarPosition.TOP)
            return
        if self.mgr.try_unlock(pwd):
            models = self.mgr.get_highspeed_models()
            self._set_hint(True, f"✓ 解锁成功：{len(models)} 个高速地址已可用，现在可以「高速下载」了。")
            InfoBar.success(title="解锁成功", content=f"已加载 {len(models)} 个加密高速模型",
                            parent=self, position=InfoBarPosition.TOP)
        else:
            self._set_hint(False, f"✗ {self.mgr.last_error or '解锁失败'}")
            InfoBar.error(title="解锁失败", content=self.mgr.last_error or "密码错误或加密 JSON 已失效",
                          parent=self, position=InfoBarPosition.TOP)
        self._sync_state()

    def _on_lock(self):
        self.mgr.lock()
        self.pwd_edit.clear()
        self._set_hint(True, "已重新锁定。下次使用高速下载需要重新输入密码。")
        InfoBar.info(title="已锁定", content="下次使用高速下载需重新输入密码",
                     parent=self, position=InfoBarPosition.TOP)
        self._sync_state()

    def _sync_state(self):
        cfg = os.path.join(self.mgr._root, "model_download_config.json")
        self.file_label.setText(f"配置文件：{cfg}")
        if self.mgr.is_highspeed_unlocked():
            models = self.mgr.get_highspeed_models()
            self.state_label.setText(f"状态：已解锁 ✓（{len(models)} 个高速地址可用）")
            self.pwd_edit.setEnabled(False)
        else:
            has = self.mgr.has_highspeed_config()
            self.state_label.setText(
                "状态：已锁定 🔒" + ("" if has else "（无加密高速镜像）"))
            self.pwd_edit.setEnabled(has)

    def _set_hint(self, ok: bool, text: str):
        color = "#22C55E" if ok else "#EF4444"
        self.hint_label.setStyleSheet(f"color:{color};font-weight:600;")
        self.hint_label.setText(text)

    def _on_open_dir(self):
        target = self.mgr._root
        try:
            if hasattr(os, "startfile"):
                os.startfile(target)
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as e:
            InfoBar.error(title="打开失败", content=str(e),
                          parent=self, position=InfoBarPosition.TOP)
