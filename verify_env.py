# -*- coding: utf-8 -*-
"""环境与模型验证（自适应双模式）：

- 已安装 numpy/opencv/onnxruntime/Pillow 时：实测加载 5 个 ONNX 模型 + 业务模块 import 冒烟。
- 缺少 onnxruntime 时（如沙箱无外网）：降级为离线文件完整性检查（文件大小 + ONNX 结构特征扫描）。

真机用法：
    pip install -r requirements.txt
    python verify_env.py
"""
import os
import re
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(_BASE, "model")
MODELS = [
    "modnet_photographic_portrait_matting.onnx",
    "hivision_modnet.onnx",
    "rmbg-1.4.onnx",
    "birefnet-v1-lite.onnx",
    "retinaface-resnet50.onnx",
]


def _load_runtime():
    try:
        import numpy as np
        import cv2
        import onnxruntime as ort
        from PIL import Image
        return np, cv2, ort, Image, True
    except Exception:
        return None, None, None, None, False


def _offline_check():
    print("   模式: 离线文件完整性检查（未安装 onnxruntime，沙箱无外网）")
    common = {
        "input", "output", "Conv", "Relu", "Add", "Mul", "Concat", "Resize",
        "Transpose", "MatMul", "Gemm", "Sigmoid", "Slice", "Pad", "BatchNorm",
        "GlobalAveragePool", "MaxPool", "AveragePool", "Clip", "LeakyRelu",
        "Unsqueeze", "Gather", "Constant", "ReduceMean", "InstanceNormalization",
        "UpSample", "Reshape", "Squeeze", "Cast", "Div", "Sub", "Erf", "Pow",
        "Exp", "Tanh", "HardSwish", "Softmax", "Flatten", "Shape", "PRelu", "Elu",
    }
    tok = re.compile(rb"[\x20-\x7e]{4,}")
    all_ok = True
    for name in MODELS:
        p = os.path.join(MODEL_DIR, name)
        if not os.path.isfile(p):
            print(f"   [MISSING] {name}")
            all_ok = False
            continue
        sz = os.path.getsize(p)
        found = set()
        with open(p, "rb") as f:
            while True:
                chunk = f.read(16 * 1024 * 1024)
                if not chunk:
                    break
                for s in tok.findall(chunk):
                    found.add(s.decode("ascii", "ignore"))
        hits = common & found
        # 文件体积正常(>1MB) 且 含 >=2 个 ONNX 典型算子字符串 => 高度可信为有效模型
        # （输入/输出张量名常为 input.1/image 等，不以裸 input/output 出现，故不强制）
        ok = (sz > 1 * 1024 * 1024) and (len(hits) >= 2)
        if not ok:
            all_ok = False
        print(f"   [{'OK' if ok else 'SUSPECT'}] {name}  ({sz/1024/1024:.1f} MB)  命中算子数={len(hits)}")
    return all_ok


def _full_check(np, cv2, ort, Image):
    print("   模式: 完整运行时验证")
    print(f"   Python {sys.version.split()[0]} | numpy {np.__version__} | "
          f"cv2 {cv2.__version__} | ort {ort.__version__} | PIL {Image.__version__}")
    import inference          # noqa
    import image_utils        # noqa
    import photo_processor    # noqa
    import layout_engine      # noqa
    import styles             # noqa
    print("   业务模块 import 冒烟 (inference/image_utils/photo_processor/layout_engine/styles) -> OK")
    all_ok = True
    for name in MODELS:
        p = os.path.join(MODEL_DIR, name)
        if not os.path.isfile(p):
            print(f"   [MISSING] {name}")
            all_ok = False
            continue
        sz = os.path.getsize(p) / 1024 / 1024
        try:
            sess = ort.InferenceSession(p, providers=["CPUExecutionProvider"])
            inp = [i.name for i in sess.get_inputs()]
            print(f"   [OK] {name} ({sz:.1f} MB) inputs={inp}")
        except Exception as e:
            print(f"   [FAIL] {name}: {e}")
            all_ok = False
    return all_ok


print("=" * 60)
print("  环境与模型验证")
print("=" * 60)
np, cv2, ort, Image, ok = _load_runtime()
if ok:
    res = _full_check(np, cv2, ort, Image)
else:
    print("[提示] 依赖缺失，已降级离线检查。真机执行 `pip install -r requirements.txt` 后可得完整验证。")
    res = _offline_check()
print("=" * 60)
print("结果:", "通过 ✅" if res else "存在问题 ❌")
print("=" * 60)
sys.exit(0 if res else 1)
