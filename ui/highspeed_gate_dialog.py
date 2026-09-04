# -*- coding: utf-8 -*-
"""
高速下载门控对话框
============================================================
点击「高速下载」后弹出：
  * 提示文字：高速流量有限不开放所有人，请自行下载
  * 密码输入框
  * 验证通过 → accept()（调用方打开 ModelDownloadDialog）
  * 验证失败 / 取消 → reject()
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
)
from PySide6.QtCore import Qt

from qfluentwidgets import (
    BodyLabel, SubtitleLabel, LineEdit, PushButton, PrimaryPushButton,
    FluentIcon, InfoBar, InfoBarPosition,
)

from . import theme


class HighSpeedGateDialog(QDialog):
    """高速下载密码门控。

    密码判定（单一可信来源 = 工具箱生成的配置文件密码）：
      * 若软件随附了加密配置文件（model_download_config.json 含 highspeed_downloads），
        则用该配置文件的解密密码校验（mirror_manager.try_unlock）。
        也就是说「工具箱生成的 JSON 配套密码」就是高速下载密码，门禁与后续解锁共用一个密码。
      * 若没有加密配置文件（作者未分发），则回退到内置访问密码 DEFAULT_PASSWORD。
    """

    # 仅作为「无加密配置文件时的兜底访问密码」；有配置文件时以配置文件密码为准。
    DEFAULT_PASSWORD = "idphoto2026"

    def __init__(self, parent=None, mirror_manager=None):
        super().__init__(parent)
        self.mirror_manager = mirror_manager
        self.setWindowTitle("高速下载")
        self.setFixedSize(420, 300)
        if parent is not None:
            self.setWindowModality(Qt.WindowModal)
        self._build()
        self._apply_theme()
        self._sync_warn_text()

    # ----------------------------------------------------------
    def _apply_theme(self):
        bg = theme.pal("bg")
        self.setStyleSheet(f"QDialog{{background-color:{bg};}}")

    # ----------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        # 标题
        root.addWidget(SubtitleLabel("高速下载"))

        # 警告提示（文案随是否有加密配置文件动态变化，见 _sync_warn_text）
        self.warn_label = BodyLabel("")
        self.warn_label.setWordWrap(True)
        root.addWidget(self.warn_label)

        root.addSpacing(4)

        # 密码输入行
        pwd_row = QHBoxLayout()
        pwd_row.addWidget(BodyLabel("访问密码："))
        self.pwd_edit = LineEdit()
        self.pwd_edit.setPlaceholderText("请输入高速下载密码")
        self.pwd_edit.setEchoMode(LineEdit.Password)
        self.pwd_edit.setFixedWidth(220)
        self.pwd_edit.returnPressed.connect(self._on_confirm)
        pwd_row.addWidget(self.pwd_edit, 1)
        root.addLayout(pwd_row)

        # 状态提示
        self.status_label = BodyLabel("")
        root.addWidget(self.status_label)

        root.addStretch(1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.confirm_btn = PrimaryPushButton(FluentIcon.ACCEPT, "确认进入")
        self.confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self.confirm_btn)
        self.cancel_btn = PushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)
        root.addLayout(btn_row)

    # ----------------------------------------------------------
    def _sync_warn_text(self):
        """根据是否随附加密配置文件，切换提示文案。"""
        has_cfg = (self.mirror_manager is not None
                   and self.mirror_manager.has_highspeed_config())
        if has_cfg:
            self.warn_label.setText(
                "高速下载地址已随软件加密分发。\n"
                "请输入此配置文件（model_download_config.json）对应的解锁密码。\n"
                "普通用户可通过「检查并下载缺失模型」按钮走标准镜像源下载。")
        else:
            self.warn_label.setText(
                "高速流量有限，暂未对所有人开放。\n"
                "如需使用高速直链下载，请输入访问密码。\n"
                "普通用户可通过「检查并下载缺失模型」按钮走标准镜像源下载。")

    # ----------------------------------------------------------
    def _on_confirm(self):
        pwd = self.pwd_edit.text().strip()
        if not pwd:
            self.status_label.setText("请输入密码")
            return
        # 优先用工具箱生成的配置文件密码；无配置文件时回退到内置访问密码
        if self.mirror_manager is not None and self.mirror_manager.has_highspeed_config():
            # 已经解锁过（本会话内）则直接放行，避免误锁
            if self.mirror_manager.is_highspeed_unlocked():
                self.accept()
                return
            if self.mirror_manager.try_unlock(pwd):
                self.accept()
            else:
                self.status_label.setText("密码错误，请重试")
                self.pwd_edit.clear()
                self.pwd_edit.setFocus()
            return
        # 无加密配置文件：回退到内置访问密码
        if pwd != self.DEFAULT_PASSWORD:
            self.status_label.setText("密码错误，请重试")
            self.pwd_edit.clear()
            self.pwd_edit.setFocus()
            return
        self.accept()
