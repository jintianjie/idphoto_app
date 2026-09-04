# -*- coding: utf-8 -*-
"""
综合测试脚本 - 验证证件照应用所有核心功能。
"""
import sys
import os
import glob
import time
import traceback

# 设置工作目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import numpy as np
import cv2

# 测试常量（已相对化：工程随迁即可运行，无需改路径）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(_BASE_DIR, "model")
MATTING_MODEL = os.path.join(MODEL_DIR, "modnet_photographic_portrait_matting.onnx")
FACE_MODEL = os.path.join(MODEL_DIR, "retinaface-resnet50.onnx")
OUTPUT_DIR = os.path.join(_BASE_DIR, "test_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def find_test_image():
    """自动定位一张含人脸的测试原图，查找顺序：
    1) test_images/test.jpg
    2) test_images/ 下任意图片（jpg/jpeg/png/bmp）
    3) 找不到返回 None
    """
    preferred = os.path.join(_BASE_DIR, "test_images", "test.jpg")
    if os.path.isfile(preferred):
        return preferred
    search_dir = os.path.join(_BASE_DIR, "test_images")
    if os.path.isdir(search_dir):
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp"):
            hits = glob.glob(os.path.join(search_dir, ext))
            if hits:
                return sorted(hits)[0]
    return None


# 测试图片：自动定位（找不到时由 __main__ 入口提示并退出）
TEST_IMAGE = find_test_image()


def step(msg):
    print(f"  {msg}")


def ok(msg):
    print(f"  [OK] {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")


def make_engine_manager(model_name="modnet_photographic_portrait_matting"):
    """创建 EngineManager 实例"""
    from inference import EngineManager
    mgr = EngineManager(MODEL_DIR, FACE_MODEL)
    mgr.set_matting_model(model_name)
    return mgr


def run_test(test_func, test_name, test_num, total):
    print(f"\n[{test_num}/{total}] {test_name}...")
    try:
        test_func()
        return True
    except Exception as e:
        fail(f"异常: {e}")
        traceback.print_exc()
        return False


def test_model_loading():
    """测试模型加载"""
    mgr = make_engine_manager()
    step("加载抠图模型...")
    matting = mgr.get_matting_engine()
    ok(f"抠图模型加载成功: {matting.model_path}")

    step("加载人脸检测模型...")
    detector = mgr.get_face_detector()
    ok(f"人脸检测模型加载成功: {detector.model_path}")


def test_face_detection():
    """测试人脸检测"""
    mgr = make_engine_manager()
    detector = mgr.get_face_detector()

    img = cv2.imread(TEST_IMAGE)
    if img is None:
        raise FileNotFoundError(f"无法读取测试图片: {TEST_IMAGE}")
    step(f"测试图片尺寸: {img.shape[1]}x{img.shape[0]}")

    faces = detector.detect(img)
    step(f"检测到 {len(faces)} 张人脸")

    if len(faces) >= 1:
        f = faces[0]
        step(f"人脸框: ({int(f[0])}, {int(f[1])}) -> ({int(f[2])}, {int(f[3])})")
        ok("人脸检测正常")
    else:
        fail("未检测到人脸")


def test_matting():
    """测试人像抠图"""
    from image_utils import resize_image_esp

    mgr = make_engine_manager()
    matting_engine = mgr.get_matting_engine()

    img = cv2.imread(TEST_IMAGE)
    img = resize_image_esp(img, max_side=2000)
    step(f"输入尺寸: {img.shape[1]}x{img.shape[0]}")

    result = matting_engine.process(img)
    step(f"抠图结果: {result.shape} (通道数={result.shape[2]})")

    # 保存抠图结果
    out_path = os.path.join(OUTPUT_DIR, "matting_result.png")
    cv2.imwrite(out_path, result)
    ok(f"抠图结果已保存: {out_path}")


def test_id_photo_processing():
    """测试完整证件照处理流程（含性能）"""
    from photo_processor import PhotoProcessor

    mgr = make_engine_manager()
    processor = PhotoProcessor(mgr)

    img = cv2.imread(TEST_IMAGE)
    step(f"测试图片: {img.shape[1]}x{img.shape[0]}")

    # 一寸照: 295x413
    t0 = time.time()
    result = processor.process_id_photo(img, size=(413, 295))
    elapsed = time.time() - t0
    step(f"证件照处理耗时: {int(elapsed * 1000)}ms")
    step(f"标准照: {result.standard.shape}")
    step(f"高清照: {result.hd.shape}")
    step(f"抠图: {result.matting.shape}")

    # 保存结果
    cv2.imwrite(os.path.join(OUTPUT_DIR, "standard_photo.png"), result.standard)
    cv2.imwrite(os.path.join(OUTPUT_DIR, "hd_photo.png"), result.hd)
    cv2.imwrite(os.path.join(OUTPUT_DIR, "matting_photo.png"), result.matting)

    if result.face_info:
        step(f"人脸信息: {result.face_info}")

    ok("证件照处理流程正常")
    return result


def test_single_size_layout(result):
    """测试单一尺寸排版"""
    from layout_engine import LayoutEngine

    engine = LayoutEngine()

    # 不带裁剪线
    pages = engine.generate_single_size(
        result.standard, 295, 413, crop_line=False
    )
    step(f"排版图: {len(pages)} 页，首页尺寸: {pages[0].shape}")
    cv2.imwrite(os.path.join(OUTPUT_DIR, "layout_single.png"), pages[0])
    ok("单一尺寸排版正常（无裁剪线）")

    # 带裁剪线
    pages_crop = engine.generate_single_size(
        result.standard, 295, 413, crop_line=True
    )
    cv2.imwrite(os.path.join(OUTPUT_DIR, "layout_single_crop.png"), pages_crop[0])
    ok("单一尺寸排版正常（带裁剪线）")


def test_mixed_size_layout():
    """测试混合尺寸排版（尺寸混拼）"""
    from layout_engine import LayoutEngine, LayoutItem
    from photo_processor import PhotoProcessor

    mgr = make_engine_manager()
    processor = PhotoProcessor(mgr)

    img = cv2.imread(TEST_IMAGE)

    # 生成不同尺寸的证件照
    sizes = [
        (413, 295, "一寸"),
        (579, 390, "二寸"),
        (567, 390, "小二寸"),
    ]

    items = []
    for h, w, label in sizes:
        step(f"  生成 {label}: {w}x{h}")
        r = processor.process_id_photo(img, size=(h, w))
        items.append(LayoutItem(r.standard, w, h, label))

    engine = LayoutEngine()
    pages = engine.generate_mixed_size(items, crop_line=True)
    step(f"混拼排版图: {len(pages)} 页，首页尺寸: {pages[0].shape}")
    cv2.imwrite(os.path.join(OUTPUT_DIR, "layout_mixed.png"), pages[0])
    ok("混合尺寸排版正常")


def test_export():
    """测试导出功能"""
    from image_utils import save_image_with_dpi, save_image_to_jpeg_kb

    # 读取之前保存的标准照
    standard = cv2.imread(os.path.join(OUTPUT_DIR, "standard_photo.png"))
    if standard is None:
        step("跳过导出测试（无标准照）")
        return

    # PNG with DPI
    png_path = os.path.join(OUTPUT_DIR, "export_300dpi.png")
    save_image_with_dpi(standard, png_path, dpi=300)
    ok(f"PNG 300DPI 导出成功: {png_path}")

    # JPEG with KB limit
    jpg_path = os.path.join(OUTPUT_DIR, "export_50kb.jpg")
    save_image_to_jpeg_kb(standard, jpg_path, target_kb=50)
    file_size = os.path.getsize(jpg_path) / 1024
    step(f"JPEG 文件大小: {file_size:.1f}KB")
    ok(f"JPEG KB限制导出成功: {jpg_path}")


if __name__ == "__main__":
    if not TEST_IMAGE:
        print("=" * 60)
        print("  [准备] 未找到测试图片")
        print("  请将一张含人脸的 JPG/PNG 放到以下任一位置：")
        print("    1) %s" % os.path.join(_BASE_DIR, "test_images", "test.jpg"))
        print("    2) test_images/ 目录下的任意图片（jpg/jpeg/png/bmp）")
        print("  然后重新运行本脚本。")
        print("=" * 60)
        sys.exit(1)

    print("=" * 60)
    print("  证件照应用 - 综合测试")
    print("=" * 60)

    results = []
    total = 7

    # 前4个测试
    tests = [
        ("模型加载", test_model_loading),
        ("人脸检测", test_face_detection),
        ("人像抠图", test_matting),
        ("证件照处理（性能）", test_id_photo_processing),
    ]

    for i, (name, func) in enumerate(tests, 1):
        success = run_test(func, name, i, total)
        results.append((name, success))

    # 第5步：单一尺寸排版（依赖第4步结果）
    print("\n[5/7] 单一尺寸排版...")
    try:
        from photo_processor import PhotoProcessor
        mgr = make_engine_manager()
        processor = PhotoProcessor(mgr)
        img = cv2.imread(TEST_IMAGE)
        photo_result = processor.process_id_photo(img, size=(413, 295))
        test_single_size_layout(photo_result)
        results.append(("单一尺寸排版", True))
    except Exception as e:
        fail(f"异常: {e}")
        traceback.print_exc()
        results.append(("单一尺寸排版", False))

    # 第6步：混合尺寸排版
    print("\n[6/7] 混合尺寸排版...")
    try:
        test_mixed_size_layout()
        results.append(("混合尺寸排版", True))
    except Exception as e:
        fail(f"异常: {e}")
        traceback.print_exc()
        results.append(("混合尺寸排版", False))

    # 第7步：导出功能
    print("\n[7/7] 导出功能...")
    try:
        test_export()
        results.append(("导出功能", True))
    except Exception as e:
        fail(f"异常: {e}")
        traceback.print_exc()
        results.append(("导出功能", False))

    # 汇总
    print("\n" + "=" * 60)
    print("  测试汇总")
    print("=" * 60)
    passed = sum(1 for _, s in results if s)
    for name, success in results:
        status = "PASS" if success else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  通过: {passed}/{len(results)}")
    print("=" * 60)
