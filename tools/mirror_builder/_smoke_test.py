# -*- coding: utf-8 -*-
"""offscreen 冒烟 + 逻辑回归：生成器 / MirrorManager / 两个对话框。"""
import os
import sys
import json
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))           # tools/mirror_builder
PROJECT = os.path.dirname(os.path.dirname(HERE))             # idphoto_app
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "tools", "mirror_builder"))

from PySide6.QtWidgets import QApplication, QMessageBox
# offscreen 下模态对话框会无人点击而阻塞，统一打桩
QMessageBox.information = lambda *a, **k: None
QMessageBox.critical = lambda *a, **k: None
QMessageBox.warning = lambda *a, **k: None
app = QApplication(sys.argv)
from qfluentwidgets import setTheme, Theme
setTheme(Theme.LIGHT)

fails = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        fails.append(name)


# ---- 1. 生成器解析 + 写 + 读 ----
import mirror_builder as mb
entries, warns = mb.parse_entries(mb.SAMPLE_TEXT)
check("builder 解析出 2 条", len(entries) == 2)
check("builder 文件名正确", entries[0]["name"] == "rmbg-1.4.onnx")

tmp = tempfile.mktemp(suffix=".json")
mb.write_config(tmp, mb.build_plain(entries), "testpw")
check("write+roundtrip", mb.verify_roundtrip(tmp, "testpw"))
p2, _ = mb.read_config(tmp, "bad")
check("错误密码读不到", p2 is None)

# ---- 2. MirrorManager 只读解锁 ----
from core import mirror_manager as mm
mgr = mm.MirrorManager(PROJECT)
check("has_highspeed_config", mgr.has_highspeed_config())
check("try_unlock 正确密码", mgr.try_unlock("000250") is True)
check("解锁后 5 条模型", len(mgr.get_highspeed_models()) == 5)
check("错误密码解锁失败", mgr.try_unlock("wrongpwd") is False)
check("失败后未解锁", mgr.is_highspeed_unlocked() is False)
check("公开镜像非空", len(mgr.get_mirrors()) > 0)

# ---- 3. 生成器 UI 构造 + 解析预览（写临时文件，不碰真实配置）----
w = mb.BuilderApp()
w.out_edit.setText(tmp)
w._on_parse()
check("生成器 UI 构造且解析预览", w.table.rowCount() == 2)
w.pwd_edit.setText("000250")
w.pwd2_edit.setText("000250")
w._on_generate()
check("生成器生成到临时文件", mb.verify_roundtrip(tmp, "000250"))
w.close()

# ---- 4. 解锁对话框构造 ----
from ui.mirror_dialog import MirrorManagerDialog
mgr2 = mm.MirrorManager(PROJECT)
mgr2.try_unlock("000250")
dlg = MirrorManagerDialog(mgr2)
check("解锁对话框构造", dlg is not None)
dlg.close()

# ---- 5. 下载对话框构造（含高速分支）----
from ui.model_download_dialog import ModelDownloadDialog
dd = ModelDownloadDialog(tempfile.mkdtemp(), mirror_manager=mgr2)
check("下载对话框构造", dd is not None)
check("高速按钮可用", dd.hs_btn.isEnabled())
dd.close()

print("\n结果:", "ALL PASS" if not fails else f"{len(fails)} 项失败: {fails}")
sys.exit(1 if fails else 0)
