# -*- coding: utf-8 -*-
"""
程序入口（带启动页）
============================================================
run.bat 启动本文件而不是 app.py。流程：

    1. 只 import PySide6（毫秒级），立刻把启动页画到屏幕上；
    2. 分步导入重型依赖 / 构造主窗口，每步刷新启动页的文字与进度；
    3. 主窗口就绪后收起启动页，进入事件循环。

这样「双击 run.bat → 看到界面」之间的几秒空白变成有反馈的加载过程。
直接 `python app.py` 依然可用（只是没有启动页），两条入口都保留。

为什么每步之间要手动 processEvents：
    启动阶段 app.exec() 还没跑，Qt 事件循环不存在，
    QTimer.singleShot / QPropertyAnimation / update() 都不会执行。
    只有显式调用 processEvents() + repaint() 才能让启动页真正刷新。
"""
import os
import sys
import time
import traceback

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE_DIR)


def _log_path() -> str:
    """启动失败日志的落盘位置。

    源码运行时就在项目根（run_bat.log），与改动前一致。
    打包运行时 _BASE_DIR 指向 _internal，装到 Program Files 时不可写，
    会静默丢掉排错最关键的异常堆栈，因此改到 %LOCALAPPDATA%。
    """
    name = "run_bat.log"
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "证件照制作工具")
        try:
            os.makedirs(d, exist_ok=True)
            return os.path.join(d, name)
        except OSError:
            return os.path.join(_BASE_DIR, name)
    return os.path.join(_BASE_DIR, name)

from PySide6.QtWidgets import QApplication, QMessageBox
from ui.splash import StartupSplash

# 启动页最短展示时长（毫秒）：依赖已缓存时加载可能只要 200ms，
# 不给下限的话启动页会一闪而过，反而像个 bug。
MIN_SPLASH_MS = 700


def _pump(app, ms):
    """在事件循环启动前"手动跑"一小段事件处理，让启动页保持响应与刷新。"""
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.008)


# ============================================================
#  分步任务：每步只做一件事，便于在启动页上显示有意义的进度
# ============================================================
def _import_ui():
    """Fluent 组件 + 主题。必须在任何控件构造之前完成。"""
    from ui.theme import apply_theme
    apply_theme()


def _import_imaging():
    """图像/数值依赖。app.py 顶层就要用，提前导入只为把耗时摊到进度条上。"""
    import numpy      # noqa: F401
    import cv2        # noqa: F401
    import PIL        # noqa: F401


def _import_app_module():
    """业务模块：inference / photo_processor / layout_engine 等都在这里进入。"""
    import app
    return app


def main():
    app = QApplication(sys.argv)
    splash = StartupSplash()
    splash.show()
    app.processEvents()

    started = time.perf_counter()
    holder = {}

    steps = [
        ("正在加载界面组件…", 0.15, _import_ui),
        ("正在加载图像引擎…", 0.40, _import_imaging),
        ("正在加载业务模块…", 0.65, lambda: holder.setdefault("app", _import_app_module())),
        ("正在初始化主窗口…", 0.85,
         lambda: holder.setdefault("win", holder["app"].IDPhotoApp())),
    ]

    try:
        for text, progress, task in steps:
            splash.set_status(text, progress)
            _pump(app, 16)          # 先让"这一步要开始了"的文字画出来
            task()
    except Exception:
        splash.close()
        detail = traceback.format_exc()
        try:
            with open(_log_path(), "a",
                      encoding="utf-8") as f:
                f.write("\n[LAUNCH FAILED]\n" + detail + "\n")
        except Exception:
            pass
        QMessageBox.critical(
            None, "启动失败",
            "程序初始化时出错，详情已写入 run_bat.log：\n\n" + detail.strip().splitlines()[-1]
        )
        return 1

    splash.set_status("正在进入…", 1.0)
    # 补足最短展示时长
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms < MIN_SPLASH_MS:
        _pump(app, MIN_SPLASH_MS - elapsed_ms)

    splash.finish(holder["win"])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
