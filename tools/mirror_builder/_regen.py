# -*- coding: utf-8 -*-
"""一次性脚本：用用户提供的 5 条地址重新生成干净的 model_download_config.json。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, PROJECT_ROOT)

import mirror_builder as mb
from core import mirror_manager as mm

URLS = """rmbg-1.4.onnx:
https://drfs.ctcontents.com/file/14237530/17569882187584/56a122/date/model/rmbg-1.4.onnx
retinaface-resnet50.onnx:
https://drfs.ctcontents.com/file/14237530/17569882187583/fdf036/date/model/retinaface-resnet50.onnx
modnet_photographic_portrait_matting.onnx:
https://drfs.ctcontents.com/file/14237530/17569882187570/e0da82/date/model/modnet_photographic_portrait_matting.onnx
hivision_modnet.onnx:
https://drfs.ctcontents.com/file/14237530/17569882187566/c4bafd/date/model/hivision_modnet.onnx
birefnet-v1-lite.onnx:
https://drfs.ctcontents.com/file/14237530/17569882187565/10a3e7/date/model/birefnet-v1-lite.onnx
"""

PASSWORD = "000250"
OUT = os.path.join(PROJECT_ROOT, "model_download_config.json")

entries, warns = mb.parse_entries(URLS)
print("parsed entries:", len(entries))
for e in entries:
    print("  -", e["name"], "->", e["url"][:60], "...")
if warns:
    print("warnings:", warns)

plain = mb.build_plain(entries, "drfs 高速镜像（2026-09-01 提供）")
mb.write_config(OUT, plain, PASSWORD)
print("written ->", OUT, "| verify_roundtrip:", mb.verify_roundtrip(OUT, PASSWORD))

# 验证新 MirrorManager 能正确解锁
mgr = mm.MirrorManager(PROJECT_ROOT)
print("has_highspeed_config:", mgr.has_highspeed_config())
print("try_unlock('000250'):", mgr.try_unlock(PASSWORD))
models = mgr.get_highspeed_models()
print("models after unlock:", len(models))
for m in models:
    print("   ", m["name"], m["url"][:50])
print("try_unlock('wrong') ->", mgr.try_unlock("wrongpwd"))
print("is_highspeed_unlocked after wrong:", mgr.is_highspeed_unlocked())
