# -*- coding: utf-8 -*-
"""
encrypt_mirrors — 作者侧加密工具（精简版）
==========================================

作者用软件密码把高速下载明文加密成 secure_json wrapper，写进
``model_download_config.json.highspeed_downloads`` 字段。主程序运行时读这部分，
用户输入密码解锁（密码即解密密钥）后下载。

说明
----
密码只是「解密密钥」。本工具**不**在 JSON 内再存一份密码、也**不**写任何管理员副本
或恢复字段 —— 那些是把简单逻辑搞复杂的产物，已全部移除。

用法::

    # 加密并写回（--password 即解密密钥，也是用户解锁要输入的密码）
    python tools/encrypt_mirrors.py encrypt --password myPwd
    python tools/encrypt_mirrors.py encrypt --password myPwd --input custom.json --out /path/to/config.json

    # 检查 / 导出明文
    python tools/encrypt_mirrors.py check --password myPwd
    python tools/encrypt_mirrors.py decrypt --password myPwd --in encrypted.json --out plain.json

纯命令行，不依赖任何 GUI。
"""
import argparse
import json
import os
import sys


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
#  默认高速明文模板（基于旧 model_download_config.json）
# ============================================================

DEFAULT_PLAIN_HIGHSPEED = {
    "models": [
        {"name": "rmbg-1.4.onnx", "display_name": "rmbg-1.4（通用抠图）",
         "url": "https://drfs.ctcontents.com/file/14237530/17569882187584/56a122/date/model/rmbg-1.4.onnx"},
        {"name": "retinaface-resnet50.onnx", "display_name": "retinaface-resnet50（人脸检测）",
         "url": "https://drfs.ctcontents.com/file/14237530/17569882187583/fdf036/date/model/retinaface-resnet50.onnx"},
        {"name": "modnet_photographic_portrait_matting.onnx", "display_name": "modnet（人像抠图）",
         "url": "https://drfs.ctcontents.com/file/14237530/17569882187570/e0da82/date/model/modnet_photographic_portrait_matting.onnx"},
        {"name": "hivision_modnet.onnx", "display_name": "hivision_modnet（证件照抠图）",
         "url": "https://drfs.ctcontents.com/file/14237530/17569882187566/c4bafd/date/model/hivision_modnet.onnx"},
        {"name": "birefnet-v1-lite.onnx", "display_name": "birefnet-v1-lite（轻量抠图）",
         "url": "https://drfs.ctcontents.com/file/14237530/17569882187565/10a3e7/date/model/birefnet-v1-lite.onnx"},
    ],
}


# ============================================================
#  CLI
# ============================================================

def cmd_encrypt(args):
    """加密并写回 model_download_config.json.highspeed_downloads 字段。"""
    sys.path.insert(0, _project_root())
    from core import mirror_manager as mm, secure_json as sj

    root = _project_root()
    cfg_path = args.out or mm.get_config_path(root)

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            plain = json.load(f)
    else:
        plain = dict(DEFAULT_PLAIN_HIGHSPEED)

    if not str(args.password).strip():
        print("错误：密码不能为空", file=sys.stderr)
        sys.exit(2)
    # 确保不含任何遗留的 password 字段（密码只是解密密钥，不存进明文）
    plain.pop("password", None)

    cur = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cur = json.load(f)
        except Exception:
            cur = {}
    if not isinstance(cur, dict):
        cur = {}

    cur["highspeed_downloads"] = sj.encrypt(plain, args.password)

    tmp = cfg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cfg_path)

    print(f"加密完成 -> {cfg_path}")
    print(f"  解锁密码（= 解密密钥）: {args.password}")
    print(f"  高速模型              : {len(plain.get('models', []))} 条")
    print("说明：密码以密文形式存在配置里，文本编辑器打开看不到。")


def cmd_decrypt(args):
    """把 secure_json wrapper 解密成明文 dump 出来（用于作者自检）。"""
    sys.path.insert(0, _project_root())
    from core import secure_json as sj

    if not args.in_file:
        print("错误：需要 --in 参数", file=sys.stderr)
        sys.exit(2)
    with open(args.in_file, encoding="utf-8") as f:
        wrapper = json.load(f)
    plain = sj.decrypt(wrapper, args.password)
    if plain is None:
        print("解密失败：密码错或文件被篡改", file=sys.stderr)
        sys.exit(3)
    out_text = json.dumps(plain, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"明文已写到 {args.out}")
    else:
        sys.stdout.write(out_text + "\n")


def cmd_check(args):
    """检测 model_download_config.json 中 highspeed_downloads 是否有效加密格式。"""
    sys.path.insert(0, _project_root())
    from core import secure_json as sj, mirror_manager as mm

    p = args.in_file or mm.get_config_path(_project_root())
    if not os.path.exists(p):
        print(f"{p} 不存在")
        return
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    sec = cfg.get("highspeed_downloads")
    if not isinstance(sec, dict):
        print("highspeed_downloads: 不存在或非 dict")
        return
    if sj.is_secure_wrapper(sec):
        print(f"highspeed_downloads: 已加密 (PBKDF2 iter={sec.get('kdf', {}).get('iters')})")
        plain = sj.decrypt(sec, args.password) if args.password else None
        if plain is None:
            print("  （未给 --password 或密码错误，无法查看内部内容）")
        else:
            print(f"  高速模型 : {len(plain.get('models', []))} 条")
    else:
        print("highspeed_downloads: 明文（未加密）")


def main():
    ap = argparse.ArgumentParser(description="secure_json 加密 / 解密工具")
    sub = ap.add_subparsers(dest="cmd", required=False)

    enc = sub.add_parser("encrypt", help="加密 highspeed 明文 → 写回 config.json")
    enc.add_argument("--password", required=True)
    enc.add_argument("--input", help="明文 JSON 路径（不填则用内置默认）")
    enc.add_argument("--out", help="输出 config.json 路径（不填写到项目根）")
    enc.set_defaults(func=cmd_encrypt)

    dec = sub.add_parser("decrypt", help="解密 secure wrapper → 明文")
    dec.add_argument("--in", dest="in_file", required=True)
    dec.add_argument("--out", dest="out", help="导出文件路径")
    dec.add_argument("--password", required=True)
    dec.set_defaults(func=cmd_decrypt)

    chk = sub.add_parser("check", help="检查 config 中 highspeed 是否加密")
    chk.add_argument("--in", dest="in_file")
    chk.add_argument("--password")
    chk.set_defaults(func=cmd_check)

    args = ap.parse_args()
    if args.cmd is None:
        args.func = cmd_encrypt
        cmd_encrypt(ap.parse_args(["encrypt", "--password", "000250"]))
        return
    args.func(args)


if __name__ == "__main__":
    main()
