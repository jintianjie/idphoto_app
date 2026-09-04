"""
Offline smoke test for the PyQt-Fluent-Widgets UI migration.
Run with: QT_QPA_PLATFORM=offscreen python _smoke_qt.py
Constructs every view + the full controller and reports errors.
"""
import os
import sys
import traceback

from PySide6.QtCore import QEvent

QT_APP = None
ERRORS = []


def section(name):
    print(f"\n=== {name} ===")


def try_step(name, fn):
    try:
        r = fn()
        print(f"[OK]  {name}")
        return r
    except Exception as e:  # noqa
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        ERRORS.append((name, e))
        traceback.print_exc()
        return None


def main():
    global QT_APP
    from PySide6.QtWidgets import QApplication
    QT_APP = QApplication.instance() or QApplication([])

    # 1. import all ui modules
    section("import ui modules")
    ui_modules = [
        "ui.theme",
        "ui.widgets",
        "ui.preview_pane",
        "ui.process_view",
        "ui.layout_view",
        "ui.queue_pane",
        "ui.layout_left_pane",
        "ui.model_download_dialog",
        "ui.highspeed_gate_dialog",
    ]
    for m in ui_modules:
        try_step(f"import {m}", lambda m=m: __import__(m, fromlist=["*"]))

    # 2. import app (pulls backend) — must NOT require torch/models
    section("import app")
    app_mod = try_step("import app", lambda: __import__("app", fromlist=["IDPhotoApp"]))

    # 3. construct standalone views (with a dummy controller-less build)
    section("construct views")
    from PySide6.QtCore import QTimer

    if app_mod:
        IDPhotoApp = getattr(app_mod, "IDPhotoApp")
        win = try_step("construct IDPhotoApp()", lambda: IDPhotoApp())
        if win is not None:
            try_step("show()", lambda: (win.show(), True)[1])
            try_step("resizeEvent path", lambda: (win.resize(1000, 800), True)[1])
            # exercise view getters via the real controller wiring
            pvv = getattr(win, "pages", {}).get("process")
            if pvv is not None:
                try_step("ProcessView getters", lambda: (
                    pvv.get_background_color_bgr(), pvv.get_render_mode(),
                    pvv.get_process_mode(), pvv.get_advanced_params(),
                    pvv.get_beauty_params(), pvv.get_watermark_params(),
                    pvv.get_matting_model(), pvv.get_face_model(), True)[-1])
            lv = getattr(win, "pages", {}).get("layout")
            if lv is not None:
                # 注意: get_mixed_items / refresh_ready_list / get_selected_ready_idx
                # 已拆到 self.layout_pane, LayoutView 只剩纸张/选项/导出 getter
                try_step("LayoutView getters", lambda: (
                    lv.get_paper_px(), lv.get_crop_line(),
                    lv.get_crop_line_full_page(), lv.get_photo_interval(),
                    lv.get_bleed_margin(), lv.get_format(), True)[-1])
            llp = getattr(win, "layout_pane", None)
            if llp is not None:
                try_step("LayoutLeftPane.get_mixed_items", lambda: llp.get_mixed_items())
            qp = getattr(win, "queue_pane", None)
            if qp is not None:
                try_step("ProcessQueuePane.update_queue (empty)", lambda: qp.update_queue([]))
                try_step("ProcessQueuePane.set_progress(20, hi)", lambda: qp.set_progress(20, "hi"))
            pv = getattr(win, "preview_pane", None)
            if pv is not None:
                try_step("PreviewPane.show_image(None)", lambda: (pv.show_image(None), True)[1])

            # 4. runtime round-trip: 合成图像驱动 _on_process_done
            #    这条路径验证了刚修的 set_view -> 回调 -> _refresh_right_previews -> show_image 链路
            def runtime_roundtrip():
                import numpy as np
                fake = np.full((360, 288, 3), (120, 160, 220), dtype=np.uint8)  # BGR 实心图
                win.batch_images = [{"name": "t.png", "path": None, "image": fake}]
                win.current_image_idx = 0
                win.processed_results = {}
                win.ready_photos = []
                win.selected_ready_idx = -1
                win._gallery_selected_idx = None
                win.model_available = True
                # 核心: 走 set_view("idphoto") 的回调链刷新右侧预览
                win._on_process_done(fake, fake, None, idx=0)
                # 直接验证 cv2 -> pixmap 真实图像路径
                win.preview_pane.show_image(fake)
                # 验证视图切换回调能刷新（original 分支读 batch_images）
                win.preview_pane.set_view("original")
                return True

            try_step("runtime round-trip (_on_process_done + set_view)", runtime_roundtrip)

            # 4b. 文件加载主流程集成：合成临时 PNG + 模拟文件对话框，驱动 on_add_images
            def file_load_flow():
                import numpy as np
                import tempfile
                import os as _os
                from PIL import Image
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.close()
                Image.fromarray(
                    np.full((400, 320, 3), (200, 150, 90), dtype=np.uint8), "RGB"
                ).save(tmp.name)
                import app as _appmod
                orig = _appmod.QFileDialog.getOpenFileNames
                _appmod.QFileDialog.getOpenFileNames = staticmethod(
                    lambda *a, **k: ([tmp.name], ""))
                try:
                    win.on_add_images()
                    win.on_select_queue_image(0)
                    win.on_queue_zoom(0)
                    if win.batch_images:
                        win.on_remove_queue_image(0)
                finally:
                    _appmod.QFileDialog.getOpenFileNames = orig
                    _os.unlink(tmp.name)
                return True

            try_step("file-load flow (on_add_images -> select -> zoom -> remove)", file_load_flow)

            # 4b2. 拖拽图片到窗口：模拟 DragEnter + Drop，验证 eventFilter 接受并触发导入
            from PySide6.QtCore import QMimeData, QUrl

            class _FakeDragEvent:
                """只暴露 eventFilter 实际用到的 3 个接口，规避 PySide6
                QDragEnterEvent / QDropEvent 构造签名不一致的坑。"""
                def __init__(self, etype, mime):
                    self._t = etype
                    self._m = mime
                    self.accepted = False
                def type(self):
                    return self._t
                def mimeData(self):
                    return self._m
                def acceptProposedAction(self):
                    self.accepted = True

            def drag_drop_flow():
                import numpy as np, tempfile, os as _os
                from PIL import Image
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.close()
                Image.fromarray(
                    np.full((300, 240, 3), (120, 80, 200), dtype=np.uint8), "RGB"
                ).save(tmp.name)
                md = QMimeData()
                md.setUrls([QUrl.fromLocalFile(tmp.name)])
                before = len(win.batch_images)
                enter = _FakeDragEvent(QEvent.DragEnter, md)
                r1 = win.eventFilter(win, enter)
                drop = _FakeDragEvent(QEvent.Drop, md)
                r2 = win.eventFilter(win, drop)
                after = len(win.batch_images)
                _os.unlink(tmp.name)
                assert r1 is True, f"DragEnter 未被接受: r1={r1}"
                assert r2 is True, f"Drop 未被接受: r2={r2}"
                assert after == before + 1, f"拖拽未导入图片: before={before} after={after}"
                # 清理刚拖入的临时图
                win.on_remove_queue_image(after - 1)
                return True

            try_step("drag-drop image into window (eventFilter accept + add)", drag_drop_flow)

            # 4c. 点击切换链路验证：这是之前 "点击切换按钮没反应" 的真凶
            #     Pivot 的 onClick 只收到 bool，必须用 currentItemChanged(routeKey) 信号。
            from ui.widgets import TabStrip

            def test_tabstrip_switch():
                from PySide6.QtWidgets import QWidget
                a, b, c = QWidget(), QWidget(), QWidget()
                strip = TabStrip([("x", "X", a), ("y", "Y", b), ("z", "Z", c)])
                strip.pivot.setCurrentItem("y")  # = 用户点击 "Y" 触发的内部路径
                assert strip.stack.currentWidget() is b, "TabStrip switch to y failed"
                strip.pivot.setCurrentItem("z")
                assert strip.stack.currentWidget() is c, "TabStrip switch to z failed"
                return True

            try_step("TabStrip click-switch (core fix)", test_tabstrip_switch)

            def test_top_nav_switch():
                win.top_nav.setCurrentItem("layout")
                assert win.page_stack.currentWidget() is win.pages["layout"], \
                    "top nav -> layout page failed"
                # 3 列布局: 切页时左 stack 也要同步
                assert win.left_stack.currentWidget() is win.layout_pane, \
                    "left stack should be layout_pane on layout page"
                assert win.current_page == "layout", "current_page not updated"
                win.top_nav.setCurrentItem("process")
                assert win.page_stack.currentWidget() is win.pages["process"], \
                    "top nav -> process page failed"
                assert win.left_stack.currentWidget() is win.queue_pane, \
                    "left stack should be queue_pane on process page"
                return True

            try_step("Top nav click-switch (core fix)", test_top_nav_switch)

            def test_preview_view_switch():
                win.preview_pane.pivot.setCurrentItem("original")
                assert win.preview_pane.view == "original", \
                    "preview pane view not updated"
                assert win.preview_view == "original", \
                    "controller preview_view not updated via callback"
                win.preview_pane.pivot.setCurrentItem("idphoto")
                assert win.preview_view == "idphoto", \
                    "controller preview_view (idphoto) failed"
                return True

            try_step("Preview pane view click-switch (core fix)", test_preview_view_switch)

            # 4d. 设置对话框构建 + 自定义纸张列表刷新（验证把纸张管理搬进设置菜单后不报错）
            def test_settings_dialog():
                from ui.settings_dialog import SettingsDialog
                dlg = SettingsDialog(win, win)
                # 模拟一条自定义纸张，验证 _reload_paper_list 解析 + 刷新链路
                s = getattr(win, "_settings", None)
                if s is not None:
                    s.set("layout.custom_paper_presets",
                          [["测试卡纸", 152, 102], ["A4", 210, 297]])
                dlg._reload_paper_list()
                # 控制器刷新排版页下拉的联动入口也要可用
                if hasattr(win, "refresh_paper_presets"):
                    win.refresh_paper_presets()
                # 还原，避免污染配置
                if s is not None:
                    s.set("layout.custom_paper_presets", [])
                dlg._reload_paper_list()
                dlg.deleteLater()
                return True

            try_step("SettingsDialog build + paper list reload", test_settings_dialog)

            # 4e. 高速下载门控对话框：构建 + 密码校验 accept/reject 链路
            def test_highspeed_gate():
                from PySide6.QtWidgets import QDialog
                from ui.highspeed_gate_dialog import HighSpeedGateDialog
                gate = HighSpeedGateDialog(parent=win)
                # 错误密码 -> 不应 accept（status_label 被置为错误文案）
                gate.pwd_edit.setText("wrong")
                gate._on_confirm()
                assert gate.result() != QDialog.DialogCode.Accepted, \
                    "gate should reject wrong password"
                assert gate.status_label.text() != "", "gate should show error text"
                # 正确密码 -> accept
                gate.pwd_edit.setText(HighSpeedGateDialog.DEFAULT_PASSWORD)
                gate._on_confirm()
                assert gate.result() == QDialog.DialogCode.Accepted, \
                    "gate should accept correct password"
                gate.deleteLater()
                return True

            try_step("HighSpeedGateDialog build + password gate", test_highspeed_gate)

            # 4e-2. 高速门控：随附加密配置文件时，用工具箱密码解锁（而非写死内置密码）
            def test_highspeed_gate_tb_pwd():
                import json
                import tempfile
                from PySide6.QtWidgets import QDialog
                from core import secure_json as sj
                from core.mirror_manager import MirrorManager
                from ui.highspeed_gate_dialog import HighSpeedGateDialog
                TOOLBOX_PWD = "smoke-tbx-pass"
                d = tempfile.mkdtemp(prefix="hs_smoke_")
                plain = {"models": [{"name": "rmbg-1.4.onnx",
                                     "url": "https://x/y.onnx",
                                     "display_name": "rmbg"}],
                         "updated_at": "2026", "note": "smoke"}
                cfg = {"mirrors": [], "manifest": {}, "default_timeout": 60,
                       "highspeed_downloads": sj.encrypt(plain, TOOLBOX_PWD)}
                with open(os.path.join(d, "model_download_config.json"),
                          "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False)
                mgr = MirrorManager(d)
                assert mgr.has_highspeed_config(), "config must carry highspeed block"
                # 错误密码（含写死内置密码）必须被拒
                g = HighSpeedGateDialog(parent=win, mirror_manager=mgr)
                g.pwd_edit.setText(HighSpeedGateDialog.DEFAULT_PASSWORD)
                g._on_confirm()
                assert g.result() != QDialog.DialogCode.Accepted, \
                    "built-in pwd must NOT unlock config-protected gate"
                # 工具箱密码必须解锁，并同步解开 manager
                g.pwd_edit.setText(TOOLBOX_PWD)
                g._on_confirm()
                assert g.result() == QDialog.DialogCode.Accepted, \
                    "toolbox pwd must unlock gate"
                assert mgr.is_highspeed_unlocked(), "mgr must be unlocked"
                g.deleteLater()
                return True

            try_step("HighSpeedGateDialog: toolbox config password unlocks gate",
                     test_highspeed_gate_tb_pwd)

            # 4f. 缺失模型禁用 / 存在模型可选（核心需求：不必下载全部即可使用）
            def test_model_disable_missing():
                pv = win.pages["process"]
                # 强制把一个人脸模型标记为缺失，验证其 radio 被禁用、其它保持可选
                pv._apply_model_states({"retinaface-resnet50"})
                rb_miss = pv.face_group.buttons["retinaface-resnet50"]
                rb_ok = pv.face_group.buttons["mtcnn"]
                assert not rb_miss.isEnabled(), "missing model radio must be disabled"
                assert rb_ok.isEnabled(), "present model radio must stay enabled"
                # 还原：按真实目录重新计算（可能全部存在，至少不应抛错）
                pv.refresh_model_states()
                return True

            try_step("Missing models disabled / present enabled", test_model_disable_missing)

    # 5. quit cleanly
    QTimer.singleShot(200, QT_APP.quit)
    QT_APP.exec()

    section("RESULT")
    if ERRORS:
        print(f"{len(ERRORS)} ERROR(S):")
        for n, e in ERRORS:
            print(f"  - {n}: {type(e).__name__}: {e}")
        sys.exit(1)
    else:
        print("ALL SMOKE TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
