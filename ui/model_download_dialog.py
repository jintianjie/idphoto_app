# -*- coding: utf-8 -*-
"""
模型下载对话框（PyQt-Fluent 重写）
============================================================
合并原 tkinter 的两个弹窗（标准下载 / 高速下载）为一个对话框：
  * 列出缺失模型
  * 「开始下载」走 model_downloader.ensure_models（多镜像）
  * 「高速下载」走 model_downloader.download_highspeed（直链）
后台线程下载，进度 / 日志通过 QTimer 回到主线程刷新 UI。
"""
import os
import threading

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QComboBox, QSpinBox, QFrame, QLineEdit,
)
from PySide6.QtCore import Qt, Signal

from qfluentwidgets import (
    PushButton, BodyLabel, SubtitleLabel, FluentIcon, InfoBar, InfoBarPosition,
)

from . import theme

from model_downloader import (
    BUILTIN_MANIFEST, ensure_models, list_missing_models,
    load_config, get_config_path, download_highspeed,
)


class ModelDownloadDialog(QDialog):
    # 跨线程信号：后台下载线程 → 主线程 UI 更新（Qt 自动排队，可靠）
    _progress_sig = Signal(dict)
    _done_sig = Signal(bool, str)

    def __init__(self, model_dir, parent=None, mirror_manager=None, on_finished=None):
        super().__init__(parent)
        self._progress_sig.connect(self._apply_progress)
        self._done_sig.connect(self._apply_done)
        self.model_dir = model_dir
        self.on_finished = on_finished
        self.mirror_manager = mirror_manager  # 可选：传入后切到加密镜像模式
        self.cancel_event = threading.Event()
        self.worker_started = False

        self.setWindowTitle("模型下载")
        self.resize(560, 540)
        if parent is not None:
            self.setWindowModality(Qt.WindowModal)

        self._build()
        self._apply_dialog_theme()
        self._refresh_missing()

    # ----------------------------------------------------------
    def _apply_dialog_theme(self):
        """对话框底色跟随当前主题（避免在系统深色下继承深色 QDialog 背景）。"""
        bg = theme.pal("bg")
        self.setStyleSheet(f"QDialog{{background-color:{bg};}}")
        try:
            self.miss_box.setStyleSheet(
                f"QPlainTextEdit{{background-color:{theme.pal('surface2')};"
                f"color:{theme.pal('text')};border:1px solid {theme.pal('border')};"
                f"border-radius:4px;}}")
        except Exception:
            pass

    # ----------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        root.addWidget(SubtitleLabel("模型下载"))
        root.addWidget(BodyLabel(f"模型目录：{self.model_dir}"))
        root.addWidget(BodyLabel(f"配置文件：{get_config_path()}"))

        self.status_label = BodyLabel("")
        root.addWidget(self.status_label)

        self.miss_box = QPlainTextEdit()
        self.miss_box.setReadOnly(True)
        self.miss_box.setMaximumHeight(80)
        root.addWidget(self.miss_box)

        # 下载源 + 超时
        opt_row = QHBoxLayout()
        opt_row.addWidget(BodyLabel("下载源："))
        self.source_combo = QComboBox()
        self.source_combo.addItems([
            "加速地址（hf-mirror，国内直连，推荐）",
            "原地址（GitHub/HF，通常需特殊网络）",
        ])
        opt_row.addWidget(self.source_combo, 1)
        opt_row.addWidget(BodyLabel("超时(秒)："))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 600)
        self.timeout_spin.setSingleStep(10)
        if self.mirror_manager is not None:
            self.timeout_spin.setValue(self.mirror_manager.get_default_timeout())
        else:
            cfg = load_config()
            self.timeout_spin.setValue(int(cfg.get("default_timeout", 120)))
        opt_row.addWidget(self.timeout_spin)
        root.addLayout(opt_row)

        # 应用持久化设置（覆盖上面取的默认值）
        settings = getattr(self.parent(), "_settings", None)
        if settings is not None:
            pref = settings.get("model.preferred_source")
            if pref == "original":
                self.source_combo.setCurrentIndex(1)
            elif pref == "mirror":
                self.source_combo.setCurrentIndex(0)
            to = settings.get("model.default_timeout_sec")
            if to:
                try:
                    self.timeout_spin.setValue(int(to))
                except Exception:
                    pass

        # 高速下载密码与模型来源：mirror_manager 时是加密的，否则明文
        self.expected_pwd = ""
        self.hs_models = []
        if self.mirror_manager is not None:
            if not self.mirror_manager.has_highspeed_config():
                self.hs_models = []
            elif self.mirror_manager.is_highspeed_locked():
                # 已加密、未解锁 → 提示用户去「镜像源管理」解锁
                warn_row = QLabel("⚠ 高速镜像已加密。请点击「镜像源管理…」按钮，输入密码解锁。")
                warn_row.setStyleSheet(f"color:{theme.THEME_COLOR};")
                warn_row.setWordWrap(True)
                root.addWidget(warn_row)
                # 不构建密码输入框（解锁由镜像管理弹窗负责）
                self.expected_pwd = ""  # 跳过对话框内校验
            else:
                self.expected_pwd = ""  # 解锁后不再二次校验，统一走已解密内容
                self.hs_models = self.mirror_manager.get_highspeed_models()
        else:
            # 老路径：从 load_config 拉
            cfg = load_config()
            hs = cfg.get("highspeed_downloads", {}) or {}
            if isinstance(hs, dict) and "password" in hs:
                self.expected_pwd = str(hs.get("password", "")).strip()
                self.hs_models = hs.get("models", []) or []
            else:
                # 加密 wrapper 或空
                self.expected_pwd = ""
                self.hs_models = []
            if self.expected_pwd:
                pw_row = QHBoxLayout()
                pw_row.addWidget(BodyLabel("下载密码："))
                self.pwd_edit = theme.themed_line_edit()
                self.pwd_edit.setEchoMode(QLineEdit.Password)
                pw_row.addWidget(self.pwd_edit, 1)
                self.pwd_status = BodyLabel("请输入密码")
                pw_row.addWidget(self.pwd_status)
                root.addLayout(pw_row)

        # 进度
        self.progress = QProgressBar()
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.info_label = BodyLabel("就绪 — 点击「开始下载」")
        root.addWidget(self.info_label)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(120)
        root.addWidget(self.log_box, 1)

        # 按钮
        btn_row = QHBoxLayout()
        self.download_btn = PushButton("开始下载")
        self.download_btn.setIcon(FluentIcon.DOWNLOAD.qicon())
        self.download_btn.clicked.connect(self._start_standard)
        self.hs_btn = PushButton("高速下载")
        self.hs_btn.setIcon(FluentIcon.SPEED_HIGH.qicon())
        self.hs_btn.clicked.connect(self._start_highspeed)
        self.cancel_btn = PushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        self.close_btn = PushButton("关闭")
        self.close_btn.clicked.connect(self.reject)
        self.mirror_mgr_btn = PushButton(FluentIcon.SETTING.qicon(), "镜像源管理…")
        self.mirror_mgr_btn.clicked.connect(self._open_mirror_mgr)
        btn_row.addWidget(self.download_btn)
        btn_row.addWidget(self.hs_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.mirror_mgr_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

    # ----------------------------------------------------------
    def _refresh_missing(self):
        miss = list_missing_models(self.model_dir, BUILTIN_MANIFEST)
        if miss:
            self.status_label.setText(f"⚠ 共缺失 {len(miss)} 个模型")
            self.miss_box.setPlainText("\n".join(
                f"• {m['info'].get('filename', m['name'] + '.onnx')}  ← {m['name']}"
                for m in miss))
            self.download_btn.setEnabled(True)
        else:
            self.status_label.setText("✓ 所有模型均已就绪")
            self.miss_box.setPlainText("当前无需下载。")
            self.download_btn.setEnabled(False)
        # 高速下载可用性
        if not self.hs_models:
            self.hs_btn.setEnabled(False)
            self.hs_btn.setToolTip("配置文件中未设置高速下载模型列表")

    # ----------------------------------------------------------
    def _pick_source(self):
        return "original" if self.source_combo.currentIndex() == 1 else "mirror"

    def _append_log(self, text):
        self.log_box.appendPlainText(text)

    def _apply_progress(self, state):
        try:
            self.progress.setValue(int(state.get("percent", 0.0)))
            fn = state.get("current_filename", "") or ""
            self.info_label.setText(
                f"[{state.get('completed', 0)}/{state.get('total', 0)}] {fn}  "
                f"{state.get('percent', 0.0):.0f}%")
            self._append_log(state.get("log", ""))
        except Exception:
            pass

    def _apply_done(self, success, message):
        self.info_label.setText(("✓ " if success else "✗ ") + message)
        self._append_log(("✓ " if success else "✗ ") + message)
        self.cancel_btn.setEnabled(False)
        self.download_btn.setEnabled(True)
        self.hs_btn.setEnabled(bool(self.hs_models))
        self._refresh_missing()
        if self.on_finished:
            self.on_finished()

    def _on_progress(self, state):
        # 由后台下载线程调用 → 通过 Signal 安全排队到主线程（QTimer.singleShot 在
        # 无事件循环的工作线程中不会触发，故必须用 Signal 跨线程投递）
        self._progress_sig.emit(dict(state))

    def _on_done(self, success, message):
        self._done_sig.emit(success, message)

    def _start_thread(self, target):
        if self.worker_started:
            return
        self.worker_started = True
        self.cancel_event.clear()
        self.download_btn.setEnabled(False)
        self.hs_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.info_label.setText("正在检查...")
        t = threading.Thread(target=target, daemon=True)
        t.start()

    def _start_standard(self):
        def _worker():
            try:
                ensure_models(
                    model_dir=self.model_dir,
                    manifest=BUILTIN_MANIFEST,
                    source=self._pick_source(),
                    timeout=self.timeout_spin.value(),
                    cancel_event=self.cancel_event,
                    on_progress=self._on_progress,
                    on_done=self._on_done,
                )
            except Exception as e:
                self._on_done(False, f"线程异常：{e}")
        self._start_thread(_worker)

    def _start_highspeed(self):
        if self.mirror_manager is not None:
            # 加密路径：未解锁/无加密配置都拒绝
            if not self.mirror_manager.has_highspeed_config():
                InfoBar.warning(title="无加密镜像", content="config 中尚未配置加密高速镜像。",
                                parent=self, position=InfoBarPosition.TOP)
                return
            if not self.mirror_manager.is_highspeed_unlocked():
                InfoBar.warning(title="请先解锁", content="点击「镜像源管理」→ 输入密码解锁。",
                                parent=self, position=InfoBarPosition.TOP)
                return
            self.hs_models = self.mirror_manager.get_highspeed_models()
        else:
            # 老路径：dialog 内密码校验
            if self.expected_pwd:
                if getattr(self, "pwd_edit", None) is None or \
                        self.pwd_edit.text().strip() != self.expected_pwd:
                    self.pwd_status.setText("密码错误，请重试")
                    return
                self.pwd_status.setText("✓ 密码正确")
        if not self.hs_models:
            self._append_log("配置中无高速下载模型列表")
            return

        def _worker():
            try:
                download_highspeed(
                    model_dir=self.model_dir,
                    models=self.hs_models,
                    timeout=self.timeout_spin.value(),
                    cancel_event=self.cancel_event,
                    on_progress=self._on_progress,
                    on_done=self._on_done,
                )
            except Exception as e:
                self._on_done(False, f"线程异常：{e}")
        self._start_thread(_worker)

    def _open_mirror_mgr(self):
        """直接弹出镜像源管理对话框（外部已有 manager；这里仅打开它）。"""
        if self.mirror_manager is None:
            InfoBar.info(title="提示", content="当前未启用加密镜像，请直接编辑 model_download_config.json",
                         parent=self, position=InfoBarPosition.TOP)
            return
        # 复用一个外层 dialog：MirrorManagerDialog 直接 exec
        from .mirror_dialog import MirrorManagerDialog
        dlg = MirrorManagerDialog(self.mirror_manager, self)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()
        # 对话框关闭后，更新高速模型列表
        if self.mirror_manager.is_highspeed_unlocked():
            self.hs_models = self.mirror_manager.get_highspeed_models()
        else:
            self.hs_models = []
        self._refresh_missing()

    def _cancel(self):
        self.cancel_event.set()
        self.cancel_btn.setEnabled(False)
        self.info_label.setText("已取消")

    def closeEvent(self, event):
        self.cancel_event.set()
        super().closeEvent(event)
