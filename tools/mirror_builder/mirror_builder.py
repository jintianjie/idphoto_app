# -*- coding: utf-8 -*-
"""
高速下载配置生成器（独立软件，作者专用）
========================================

这是一个**独立软件**，唯一职责：
    写入「软件密码」+「高速下载地址」→ 加密 → 产出 model_download_config.json

主程序（证件照工作室）只负责「读」：用户输入密码 → 解密成功即解锁 → 拿到直链下载。
本软件不提供任何「改密码 / 重置 / 重新加密」入口；地址失效时，作者重新生成这一份
JSON 发给用户即可。

加密说明
--------
密码只充当一件事 —— secure_json wrapper 的**解密密钥**。
主程序用密码去解密；MAC 校验通过（密码正确）即视为解锁成功并下载。
因此本软件只需把地址用密码加密写盘，无需在 JSON 内再存一份密码做「二次校验」。

支持的粘贴格式
--------------
最常用（从网盘/聊天复制出来的原始文本）::

    rmbg-1.4.onnx:
    https://drfs.ctcontents.com/file/.../rmbg-1.4.onnx

也认这些写法::

    rmbg-1.4.onnx: https://...          # 名称与地址同一行
    rmbg-1.4.onnx = https://...         # 等号 / 竖线 / 制表符分隔都行
    https://.../rmbg-1.4.onnx           # 只有地址，名称自动取最后一段
    名称行与地址行之间夹空行            # 允许

运行（PySide6 + qfluentwidgets，与主程序同一套 UI 风格）
--------------------------------------------------------
双击「运行生成器.bat」即可。
命令行（批量 / 无界面）：::

    python mirror_builder.py --cli -i urls.txt -p 密码 -o model_download_config.json
    python mirror_builder.py --cli --dump -p 密码 -i model_download_config.json
"""
import datetime
import json
import os
import re
import sys
import traceback
from urllib.parse import unquote, urlparse

# 与本目录下的 secure_json.py 配套（算法与主程序 core/secure_json.py 完全一致）
from secure_json import encrypt, decrypt, is_secure_wrapper

# GUI 依赖（PySide6 / qfluentwidgets）按需懒加载：
#   这样作者用「裸 Python」跑 CLI（--cli / --dump）时不需要安装 GUI 库。
#   只有双击 bat 进入图形界面时才会真正 import 它们。
_HAS_GUI = False
try:
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox,
        QTableWidget, QTableWidgetItem, QHeaderView, QInputDialog, QApplication,
    )
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QCloseEvent
    from qfluentwidgets import (
        setTheme, Theme, SubtitleLabel, BodyLabel, LineEdit, PushButton,
        PrimaryPushButton, TextEdit, FluentIcon, InfoBar, InfoBarPosition,
    )
    _HAS_GUI = True
except Exception:                                  # pragma: no cover
    # CLI 模式下不需要 GUI；给占位对象，保证模块级 class 定义不报 NameError。
    QWidget = object
    QVBoxLayout = QHBoxLayout = QFileDialog = QMessageBox = QTableWidget = \
        QTableWidgetItem = QHeaderView = QInputDialog = QApplication = object
    Qt = QCloseEvent = object
    setTheme = Theme = SubtitleLabel = BodyLabel = LineEdit = PushButton = \
        PrimaryPushButton = TextEdit = FluentIcon = InfoBar = InfoBarPosition = object
    _HAS_GUI = False

# ============================================================
#  常量
# ============================================================

#: config 中加密块的字段名（与主程序约定，不要随意改）
FIELD_HIGHSPEED = "highspeed_downloads"

#: 内置模板：目标配置文件不存在时，用它兜底生成公开镜像 + 标准下载清单
DEFAULT_TEMPLATE = {
    "mirrors": [
        {"name": "ghproxy.com（推荐）", "prefix": "https://gh-proxy.com/"},
        {"name": "kkgithub.com", "prefix": "https://kkgithub.com/"},
        {"name": "mirror.ghproxy.com", "prefix": "https://mirror.ghproxy.com/"},
        {"name": "ghproxy.net", "prefix": "https://ghproxy.net/"},
    ],
    "manifest": {
        "modnet_photographic_portrait_matting": {
            "url": "https://raw.githubusercontent.com/Zeyi-Lin/HivisionIDPhotos/master/model/modnet_photographic_portrait_matting.onnx",
            "filename": "modnet_photographic_portrait_matting.onnx",
        },
        "hivision_modnet": {
            "url": "https://raw.githubusercontent.com/Zeyi-Lin/HivisionIDPhotos/master/model/hivision_modnet.onnx",
            "filename": "hivision_modnet.onnx",
        },
        "rmbg-1.4": {
            "url": "https://raw.githubusercontent.com/Zeyi-Lin/HivisionIDPhotos/master/model/rmbg-1.4.onnx",
            "filename": "rmbg-1.4.onnx",
        },
        "birefnet-v1-lite": {
            "url": "https://raw.githubusercontent.com/Zeyi-Lin/HivisionIDPhotos/master/model/birefnet-v1-lite.onnx",
            "filename": "birefnet-v1-lite.onnx",
        },
        "retinaface-resnet50": {
            "url": "https://raw.githubusercontent.com/Zeyi-Lin/HivisionIDPhotos/master/model/retinaface-resnet50.onnx",
            "filename": "retinaface-resnet50.onnx",
        },
    },
    "default_timeout": 60,
}

#: 已知模型的中文显示名（粘贴时自动补全）
KNOWN_DISPLAY = {
    "rmbg-1.4.onnx": "rmbg-1.4（通用抠图）",
    "retinaface-resnet50.onnx": "retinaface-resnet50（人脸检测）",
    "modnet_photographic_portrait_matting.onnx": "modnet（人像抠图）",
    "hivision_modnet.onnx": "hivision_modnet（证件照抠图）",
    "birefnet-v1-lite.onnx": "birefnet-v1-lite（轻量抠图）",
}

#: 粘贴区占位示例
SAMPLE_TEXT = (
    "rmbg-1.4.onnx:\n"
    "https://drfs.ctcontents.com/file/14237530/17569882187584/56a122/date/model/rmbg-1.4.onnx\n"
    "retinaface-resnet50.onnx:\n"
    "https://drfs.ctcontents.com/file/14237530/17569882187583/fdf036/date/model/retinaface-resnet50.onnx\n"
)

_URL_RE = re.compile(r"https?://[^\s，,、;；\"'）)】\]]+")
#: 名称里的非法字符（避免生成的文件名带路径分隔符等）
_BAD_NAME_CHARS = re.compile(r"[\\/:*?\"<>|\r\n\t]")


# ============================================================
#  解析：粘贴文本 -> [(name, url), ...]
# ============================================================

def _url_filename(url: str) -> str:
    """从 URL 取文件名（去掉 query / 片段，并做 URL 解码）。"""
    try:
        path = urlparse(url).path
    except Exception:
        path = url
    seg = path.rstrip("/").rsplit("/", 1)[-1] if path else ""
    return unquote(seg) or "model.onnx"


def _clean_name(raw: str) -> str:
    """清洗名称：去掉结尾的 : ： = - | → 以及首尾空白 / 引号。"""
    s = (raw or "").strip()
    s = s.strip("：:=＝|-—>》》").strip()
    s = s.strip('"\'“”‘’` ').strip()
    s = _BAD_NAME_CHARS.sub("", s)
    return s


def guess_display_name(name: str) -> str:
    """给文件名补一个中文显示名。"""
    if name in KNOWN_DISPLAY:
        return KNOWN_DISPLAY[name]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem or name


def parse_entries(text: str):
    """解析粘贴文本。

    返回 ``(entries, warnings)``：
      entries  -> [{"name", "url", "display_name"}, ...]（按出现顺序，URL 去重）
      warnings -> [str, ...] 供界面提示的软性问题

    规则（按优先级）：
      1. 一行里出现 http(s) 链接 → 链接前面那截当名称（允许空）
      2. 一行没有链接 → 记为「挂起名称」，等下一行出现链接时套用
      3. 名称为空 → 自动取 URL 最后一段
    """
    entries, warnings = [], []
    seen = set()
    pending = ""

    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue

        m = _URL_RE.search(line)
        if not m:
            pending = _clean_name(line)
            continue

        url = m.group(0).rstrip("。.")
        name = _clean_name(line[:m.start()]) or pending or _url_filename(url)
        pending = ""

        if url in seen:
            warnings.append(f"第 {lineno} 行：地址重复，已跳过")
            continue
        seen.add(url)

        fn = _url_filename(url)
        if name != fn:
            warnings.append(f"第 {lineno} 行：名称「{name}」与地址末段「{fn}」不一致")

        entries.append({
            "name": name,
            "url": url,
            "display_name": guess_display_name(name),
        })

    return entries, warnings


# ============================================================
#  构造 / 读写配置文件
# ============================================================

def build_plain(entries, note: str = "") -> dict:
    """构造加密块明文（不存密码，密码就是解密密钥）。"""
    return {
        "models": [
            {
                "name": e["name"],
                "display_name": e.get("display_name") or guess_display_name(e["name"]),
                "url": e["url"],
            }
            for e in entries
        ],
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": note or "",
    }


def _atomic_write_json(path: str, payload: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_config(path: str, plain: dict, password: str) -> dict:
    """把明文加密后写入完整配置文件。

    - 明文字段（mirrors / manifest / default_timeout）：
      目标文件已存在 → **原样保留**；不存在 → 用内置模板。
    - 加密字段：仅 ``highspeed_downloads``（用密码加密）。
      不再写任何管理员副本 / 内部密码。

    返回写入的 dict（便于测试断言）。
    """
    base = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict):
                base = old
        except Exception:
            base = {}

    payload = {
        "mirrors": base.get("mirrors") or DEFAULT_TEMPLATE["mirrors"],
        "manifest": base.get("manifest") or DEFAULT_TEMPLATE["manifest"],
        "default_timeout": base.get("default_timeout") or DEFAULT_TEMPLATE["default_timeout"],
        FIELD_HIGHSPEED: encrypt(plain, password),
    }
    _atomic_write_json(path, payload)
    return payload


def read_config(path: str, password: str):
    """用密码读取已有配置。

    返回 ``(plain, base)``；密码错 / 文件损坏 → 返回 ``(None, base)``。
    """
    base = {}
    if not os.path.exists(path):
        return None, base
    try:
        with open(path, "r", encoding="utf-8") as f:
            base = json.load(f)
    except Exception:
        return None, {}
    if not isinstance(base, dict):
        return None, {}
    wrapper = base.get(FIELD_HIGHSPEED)
    if isinstance(wrapper, dict) and is_secure_wrapper(wrapper):
        plain = decrypt(wrapper, password)
    elif isinstance(wrapper, dict):
        # 老明文格式
        plain = wrapper
    else:
        plain = None
    return (plain if isinstance(plain, dict) else None), base


def verify_roundtrip(path: str, password: str) -> bool:
    """生成后自检：用软件密码解一次，必须成功且 models 非空。"""
    plain, _ = read_config(path, password)
    if not plain or not plain.get("models"):
        return False
    return True


# ============================================================
#  GUI
# ============================================================

class BuilderApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("高速下载配置生成器（作者专用）")
        self.resize(900, 680)
        self.entries = []

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        self.default_out = os.path.join(project_root, "model_download_config.json")

        self._build()
        self._set_status("就绪：粘贴下载地址 → 填密码 → 生成配置文件")
        self._refresh_table()

    # ----------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        root.addWidget(SubtitleLabel("高速下载配置生成器"))
        root.addWidget(BodyLabel(
            "生成 model_download_config.json —— 写入软件密码 + 高速下载地址，"
            "加密后交给用户；主程序只负责读取并下载。"))

        self._build_file_section(root)
        self._build_pwd_section(root)
        self._build_text_section(root)
        self._build_table_section(root)
        self._build_bottom(root)

    # ---- 区 1：输出文件 ----
    def _build_file_section(self, parent):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.out_edit = LineEdit()
        self.out_edit.setText(self.default_out)
        self.out_edit.setReadOnly(True)
        browse_btn = PushButton("浏览…")
        browse_btn.clicked.connect(self._on_browse)
        load_btn = PushButton("载入已有…")
        load_btn.clicked.connect(self._on_load)
        row.addWidget(self.out_edit, 1)
        row.addWidget(browse_btn)
        row.addWidget(load_btn)
        v.addLayout(row)

        v.addWidget(BodyLabel(
            "已存在的文件会保留公开镜像等明文字段，只刷新加密块。"))
        parent.addWidget(box)

    # ---- 区 2：密码 ----
    def _build_pwd_section(self, parent):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(BodyLabel("密码："), 0)
        self.pwd_edit = LineEdit()
        self.pwd_edit.setEchoMode(LineEdit.Password)
        self.pwd_edit.setPlaceholderText("高速下载密码（用户解锁用）")
        row.addWidget(self.pwd_edit, 1)
        row.addWidget(BodyLabel("确认："), 0)
        self.pwd2_edit = LineEdit()
        self.pwd2_edit.setEchoMode(LineEdit.Password)
        self.pwd2_edit.setPlaceholderText("再次输入密码")
        row.addWidget(self.pwd2_edit, 1)
        row.addWidget(BodyLabel("备注："), 0)
        self.note_edit = LineEdit()
        self.note_edit.setPlaceholderText("可选备注")
        row.addWidget(self.note_edit, 1)
        v.addLayout(row)

        v.addWidget(BodyLabel(
            "密码即解密密钥：用户主程序输入此密码能解开，即可高速下载。请牢记并单独告知用户。"))
        parent.addWidget(box)

    # ---- 区 3：地址粘贴 ----
    def _build_text_section(self, parent):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        v.addWidget(BodyLabel("下载地址（每行一个，格式：名称: 回车 地址）："))
        self.text = TextEdit()
        self.text.setPlainText(SAMPLE_TEXT)
        self.text.setMinimumHeight(120)
        v.addWidget(self.text)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        parse_btn = PushButton("解析预览")
        parse_btn.clicked.connect(self._on_parse)
        sample_btn = PushButton("填入示例")
        sample_btn.clicked.connect(self._on_sample)
        clear_btn = PushButton("清空")
        clear_btn.clicked.connect(self._on_clear)
        bar.addWidget(parse_btn)
        bar.addWidget(sample_btn)
        bar.addWidget(clear_btn)
        bar.addWidget(BodyLabel(
            "支持：名称单独一行 / 名称: 地址 / 只有地址（自动取文件名）"), 1)
        v.addLayout(bar)
        parent.addWidget(box)

    # ---- 区 4：解析结果 ----
    def _build_table_section(self, parent):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        v.addWidget(BodyLabel("解析结果："))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件名", "显示名称", "下载地址"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setMinimumHeight(160)
        v.addWidget(self.table)

        self.count_label = BodyLabel("共 0 条")
        v.addWidget(self.count_label)
        parent.addWidget(box)

    # ---- 区 5：底部 ----
    def _build_bottom(self, parent):
        box = QHBoxLayout()
        box.setSpacing(10)
        self.status_label = BodyLabel("")
        self.status_label.setWordWrap(True)
        box.addWidget(self.status_label, 1)
        gen_btn = PrimaryPushButton("生成配置文件")
        gen_btn.setIcon(FluentIcon.SAVE.qicon())
        gen_btn.clicked.connect(self._on_generate)
        quit_btn = PushButton("退出")
        quit_btn.clicked.connect(self.close)
        box.addWidget(gen_btn)
        box.addWidget(quit_btn)
        parent.addLayout(box)

    # ----------------------------------------------------------
    def _set_status(self, text, ok=True):
        color = "#22C55E" if ok else "#EF4444"
        self.status_label.setStyleSheet(f"color:{color};font-weight:600;")
        self.status_label.setText(text)

    def _on_browse(self):
        p, _ = QFileDialog.getSaveFileName(
            self, "输出配置文件", self.default_out, "JSON 配置 (*.json)")
        if p:
            self.out_edit.setText(p)

    def _on_sample(self):
        self.text.setPlainText(SAMPLE_TEXT)
        self._set_status("已填入示例地址，点击「解析预览」")

    def _on_clear(self):
        self.text.clear()
        self.entries = []
        self._refresh_table()
        self._set_status("已清空")

    def _on_parse(self):
        entries, warns = parse_entries(self.text.toPlainText())
        self.entries = entries
        self._refresh_table()
        if not entries:
            self._set_status("没有解析出任何地址 — 请检查粘贴内容是否包含 http(s) 链接", False)
            return
        msg = f"解析完成：{len(entries)} 条地址"
        if warns:
            msg += "（" + "；".join(warns[:3]) + ("…" if len(warns) > 3 else "") + "）"
        self._set_status(msg, True)

    def _refresh_table(self):
        self.table.setRowCount(len(self.entries))
        for i, e in enumerate(self.entries):
            self.table.setItem(i, 0, QTableWidgetItem(e["name"]))
            self.table.setItem(i, 1, QTableWidgetItem(e.get("display_name", "")))
            self.table.setItem(i, 2, QTableWidgetItem(e["url"]))
        self.count_label.setText(f"共 {len(self.entries)} 条")

    def _on_load(self):
        path = self.out_edit.text().strip()
        if not path or not os.path.exists(path):
            InfoBar.warning(title="文件不存在", content=path or "",
                            parent=self, position=InfoBarPosition.TOP)
            return
        pwd, ok = QInputDialog.getText(self, "载入已有配置", "输入该配置文件的当前密码：")
        if not ok or not pwd:
            return
        plain, _base = read_config(path, pwd)
        if plain is None:
            InfoBar.error(title="载入失败", content="密码错误，或加密块已损坏 / 被篡改。",
                          parent=self, position=InfoBarPosition.TOP)
            return
        models = plain.get("models", []) or []
        self.entries = [
            {"name": m.get("name", ""),
             "display_name": m.get("display_name", ""),
             "url": m.get("url", "")}
            for m in models if m.get("url")
        ]
        self._refresh_table()
        self.pwd_edit.setText(pwd)
        self.pwd2_edit.setText(pwd)
        self.note_edit.setText(plain.get("note", ""))
        self.text.setPlainText("".join(f"{e['name']}:\n{e['url']}\n" for e in self.entries))
        self._set_status(
            f"已载入 {len(self.entries)} 条地址"
            f"（更新时间 {plain.get('updated_at', '未知')}）— 可修改后再生成")

    def _on_generate(self):
        pwd = self.pwd_edit.text()
        pwd2 = self.pwd2_edit.text()
        if not pwd:
            self._set_status("请填写密码", False)
            return
        if len(pwd) < 4:
            self._set_status("密码至少 4 位", False)
            return
        if pwd != pwd2:
            self._set_status("两次输入的密码不一致", False)
            self.pwd2_edit.clear()
            return

        # 表格里有内容就以表格为准，否则重新解析粘贴区
        if self.entries:
            entries = self.entries
        else:
            entries, _ = parse_entries(self.text.toPlainText())
            self.entries = entries
            self._refresh_table()
        if not entries:
            self._set_status("没有可写入的地址 — 请先粘贴并解析", False)
            return

        path = self.out_edit.text().strip()
        if not path:
            self._set_status("请选择输出路径", False)
            return
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            self._set_status(f"目录不存在：{d}", False)
            return

        self._set_status("正在加密写入…（PBKDF2 200k 迭代，约 1 秒）")
        try:
            plain = build_plain(entries, self.note_edit.text().strip())
            write_config(path, plain, pwd)
        except Exception as e:
            self._set_status(f"写入失败：{e}", False)
            QMessageBox.critical(self, "写入失败", str(e))
            return

        if not verify_roundtrip(path, pwd):
            self._set_status("生成完成，但自检未通过 — 请重新生成", False)
            QMessageBox.critical(self, "自检失败", "生成后回读校验失败，请重新生成。")
            return

        self._set_status(f"✓ 已生成：{len(entries)} 条地址 → {path}")
        QMessageBox.information(
            self, "生成成功",
            f"已写入 {len(entries)} 条高速地址。\n\n"
            f"文件：{path}\n\n"
            f"软件密码：{pwd}\n"
            f"把该文件和密码一起发给用户；用户主程序输入密码即可解锁下载。\n"
            f"地址失效时，重新生成并更新这一个文件即可。",
        )


# ============================================================
#  CLI（批量 / 无人值守）
# ============================================================

def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="高速下载配置生成器（CLI）")
    ap.add_argument("-i", "--input", required=True,
                    help="地址文本文件，或 --dump 时指配置文件")
    ap.add_argument("-p", "--password", help="软件密码（--dump 时必填）")
    ap.add_argument("-o", "--output", help="输出的配置文件路径")
    ap.add_argument("-n", "--note", default="", help="备注")
    ap.add_argument("--dump", action="store_true",
                    help="解密已有配置，把地址以「名称:\\n地址」打印出来")
    ns = ap.parse_args(argv)

    if ns.dump:
        if not ns.password:
            ap.error("--dump 需要 -p 密码")
        plain, _ = read_config(ns.input, ns.password)
        if plain is None:
            print("读取失败：密码错误或文件损坏")
            return 2
        print(f"# 更新时间 {plain.get('updated_at', '?')}  备注：{plain.get('note', '')}")
        for m in plain.get("models", []):
            print(f"{m.get('name', '')}:\n{m.get('url', '')}")
        return 0

    if not ns.output:
        ap.error("生成模式需要 -o 输出路径")
    if not ns.password:
        ap.error("生成模式需要 -p 密码")
    with open(ns.input, "r", encoding="utf-8") as f:
        text = f.read()
    entries, warns = parse_entries(text)
    for w in warns:
        print("warn:", w)
    if not entries:
        print("没有解析出任何地址")
        return 2
    plain = build_plain(entries, ns.note)
    write_config(ns.output, plain, ns.password)
    ok = verify_roundtrip(ns.output, ns.password)
    print(f"已生成 {len(entries)} 条 → {ns.output}  自检：{'通过' if ok else '失败'}")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if args and args[0] == "--cli":
        sys.exit(_cli(args[1:]))

    app = QApplication(sys.argv)
    setTheme(Theme.LIGHT)
    w = BuilderApp()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builder_error.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        try:
            QMessageBox.critical(None, "出错了", f"已把错误写入：\n{log}")
        except Exception:
            pass
        raise
