# -*- coding: utf-8 -*-
"""双主题对比度审计（长期保留工具，勿删）
============================================================
目的：根治「深色背景配深色字 / 浅色背景配浅色字」问题。

做法：
  1. 浅色、深色各跑一遍；
  2. 构造代表性控件（ThumbItem 各状态 / 缩略图列表 / 纯文本列表 /
     SectionCard 各档标签 / SliderRow），offscreen 渲染后 grab()；
  3. 在每个「文字区域」内取像素：出现最多的颜色 = 背景，与其差异
     最大的颜色 = 文字，按 WCAG 公式算对比度；
  4. 对比度 < 4.5 判 FAIL 并退出码 1。

约定：以后凡是涉及配色的 UI 改动，验收前跑一遍本脚本：
  QT_QPA_PLATFORM=offscreen python _audit_contrast.py
"""
import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QListWidget, QMainWindow
from PySide6.QtCore import QRect, QTimer, QEventLoop

app = QApplication(sys.argv)
app.setStyle("Fusion")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui import theme
from ui.widgets import ThumbItem, ThumbnailList, SliderRow

GRAY_IMG = None  # 占位图：None 时 ThumbItem 显示 "—"


def rel_lum(rgb):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(c1, c2):
    l1, l2 = sorted((rel_lum(c1), rel_lum(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def region_contrast(img, rect):
    """rect（控件局部坐标）内：最常见颜色视为背景，差异最大颜色视为文字。"""
    counts = Counter()
    x0, y0 = max(0, rect.left()), max(0, rect.top())
    x1 = min(img.width() - 1, rect.right())
    y1 = min(img.height() - 1, rect.bottom())
    if x1 <= x0 or y1 <= y0:
        return None
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            c = img.pixelColor(x, y)
            counts[(c.red(), c.green(), c.blue())] += 1
    bg_rgb, _ = counts.most_common(1)[0]
    fg = max(counts, key=lambda rgb: contrast(bg_rgb, rgb))
    return contrast(bg_rgb, fg)


class _Report:
    rows = []

    @classmethod
    def add(cls, theme_name, widget, region, ratio):
        ok = ratio is not None and ratio >= 4.5
        cls.rows.append((theme_name, widget, region, ratio, ok))
        return ok


def pump(ms=150):
    t = QTimer(); t.setSingleShot(True)
    loop = QEventLoop()
    t.timeout.connect(loop.quit)
    t.start(ms)
    loop.exec()


def audit_thumb_states(theme_name):
    """ThumbItem 四种状态：空闲 / 选中 / 处理中(45%) / 完成。"""
    w = ThumbItem("7e1b799b2cc2ca…", None, 80)
    states = [("空闲", lambda: None),
              ("选中", lambda: w.set_active(True)),
              ("处理中45%", lambda: (w.set_active(True), w.set_status("45"))),
              ("完成", lambda: (w.set_active(True), w.set_status("✓")))]
    all_ok = True
    for name, act in states:
        act()
        w.resize(120, 140)
        w.show()
        app.processEvents(); pump(60)
        img = w.grab().toImage()
        r = region_contrast(img, QRect(0, w.thumb_size + 4,
                                       w.width(), w.height() - w.thumb_size - 4))
        all_ok &= _Report.add(theme_name, f"ThumbItem[{name}]", "名称+状态区", r)
        w.set_active(False)
        w.set_status("")
        w.hide()
    return all_ok


def audit_plain_list(theme_name):
    """纯文本 QListWidget（套 list_qss，模拟成品/混排列表）。"""
    lw = QListWidget()
    lw.setStyleSheet(theme.list_qss())
    lw.addItems(["3d8df6c803bb45…", "7e1b799b2cc2ca…"])
    lw.setCurrentRow(0)
    lw.resize(160, 90); lw.show()
    app.processEvents(); pump(60)
    img = lw.grab().toImage()
    ok = True
    # 第 1 行（选中项）：只看文字所在横带
    vh = lw.visualItemRect(lw.item(0))
    ok &= _Report.add(theme_name, "纯文本列表[选中项]", "文字区", region_contrast(img, vh))
    vh2 = lw.visualItemRect(lw.item(1))
    ok &= _Report.add(theme_name, "纯文本列表[普通项]", "文字区", region_contrast(img, vh2))
    lw.hide()
    return ok


def audit_cards(theme_name):
    """SectionCard 各档标签 + SliderRow（浅/深下均须可读）。"""
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    holder = QWidget()
    lay = QVBoxLayout(holder)
    card = theme.SectionCard("排版与导出")
    card.addWidget(theme.label("title", "标题文字 title"))
    card.addWidget(theme.label("field", "字段文字 field"))
    card.addWidget(theme.label("tip", "提示文字 tip —— 说明性内容"))
    card.addWidget(theme.label("chrome", "状态文字 chrome"))
    lay.addWidget(card)
    card.addWidget(SliderRow("美颜强度", 0, 100, 1, 50))
    lay.addWidget(card)
    holder.resize(300, 260); holder.show()
    app.processEvents(); pump(60)
    img = holder.grab().toImage()
    ok = True
    for lbl in card.findChildren(theme.QLabel if hasattr(theme, "QLabel")
                                 else __import__("PySide6.QtWidgets",
                                                 fromlist=["QLabel"]).QLabel):
        txt = lbl.text()
        if not txt:
            continue
        top = lbl.mapTo(holder, lbl.rect().topLeft())
        r = region_contrast(img, QRect(top, lbl.rect().size()))
        ok &= _Report.add(theme_name, f"标签[{txt[:6]}…]", "文字区", r)
    holder.hide()
    return ok


def run_theme(mode):
    theme.THEME_MODE = mode
    theme.apply_theme()
    pump(100)
    ok = True
    ok &= audit_thumb_states(mode)
    ok &= audit_plain_list(mode)
    ok &= audit_cards(mode)
    return ok


def main():
    all_ok = True
    all_ok &= run_theme("light")
    all_ok &= run_theme("dark")
    print()
    print(f"{'主题':<6}{'控件':<22}{'区域':<10}{'对比度':>8}  结果")
    print("-" * 58)
    for theme_name, widget, region, ratio, ok in _Report.rows:
        s = f"{ratio:.1f}:1" if ratio else "  n/a"
        print(f"{theme_name:<6}{widget:<22}{region:<10}{s:>8}  {'OK' if ok else 'FAIL'}")
    print("-" * 58)
    print("ALL CONTRAST CHECKS PASSED" if all_ok else "CONTRAST FAILURES FOUND")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
