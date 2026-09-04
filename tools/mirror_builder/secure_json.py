# -*- coding: utf-8 -*-
"""
secure_json — 加密 JSON 原语
============================

零第三方依赖、纯标准库实现。给本地 JSON 加一层密码保护：
  - 密码错或文件被篡改 → 整体返回 None（让上层识别为"解锁失败"）
  - 正确密码 → 返回原始 dict，可正常使用

算法选择（设计要点）
--------------------
1. **KDF（密钥派生）**: PBKDF2-HMAC-SHA256，200k 次迭代。每次加密随机 16 字节 salt。
   生成 64 字节：前 32 用于流密码，后 32 用于 HMAC。
2. **流密码（SHA256-CTR）**: 自实现 SHA256-CTR，无需第三方 AES/Cipher 库。
   块 = ``SHA256(enc_key || nonce || counter_be8)``，每 32 字节一个 block，XOR 明文。
   自实现的合理性：纯哈希流密码 + 长 MAC 防篡改已足够作为对称加密方案（不试图替代
   经过审计的 AES-GCM，但对本地 JSON 防"被 360/QQ 翻看"已足够）。
3. **完整性（MAC）**: HMAC-SHA256(mac_key, nonce || ciphertext)，加密时先计算明文
   HMAC，解密时先验证 HMAC 后再解流密码（避免"padding oracle"风格攻击）。
4. **格式**: 输出 dict 含 ``v`` 版本号 + ``kdf``/``cipher``/``mac``/``data`` 字段。
   整个 dict 以 JSON 写盘；解码失败/版本不匹配/字段缺失一律视作无效。

约束
----
- 没有第三方依赖，PySide6 基础 venv 直接可用
- 解密失败返回 None，调用方无需 try/except
- 同一密文 + 同一 salt/iter 下多次解密结果是确定性的
- 每次加密 salt/nonce 都重新生成（不重复使用流密码）

API 摘要
--------
``encrypt(obj, password)``           -> 加密包装 dict
``decrypt(wrapper, password)``       -> dict 或 None
``encrypt_to_file(obj, password, path)``
``decrypt_from_file(password, path)`` -> dict 或 None（文件不存在/损坏均视为 None）
``is_encrypted_file(path)``          -> bool，是否加密文件（含失败 JSON 不抛异常）
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any

_VERSION = 1
_PBKDF2_ITERS = 200_000          # 200k 次 → 单次派生约 200-300ms（足以拖慢暴力）
_SALT_LEN = 16                   # 128 bit
_NONCE_LEN = 12                  # 96 bit（与 GCM 同宽，习惯值）
_ENC_KEY_LEN = 32                # 256 bit 流密码
_MAC_KEY_LEN = 32                # 256 bit HMAC
_BLOCK_LEN = 32                  # SHA256 输出 32 字节
_COUNTER_LEN = 8                 # uint64 BE

_VALID_ALGOS = {"pbkdf2-sha256", "sha256ctr", "hmac-sha256"}


# ============================================================
#  低层：密钥派生 / 流密码 / MAC
# ============================================================

def _derive_keys(password: str, salt: bytes, iters: int) -> tuple:
    """派生 (enc_key, mac_key)。密码错误时返回的 key 与正确密码完全不同（PBKDF2）。"""
    if not isinstance(password, str):
        raise TypeError("password 必须为 str")
    pw_bytes = password.encode("utf-8")
    full = hashlib.pbkdf2_hmac("sha256", pw_bytes, salt, iters, _ENC_KEY_LEN + _MAC_KEY_LEN)
    return full[:_ENC_KEY_LEN], full[_ENC_KEY_LEN:]


def _keystream_block(enc_key: bytes, nonce: bytes, counter: int) -> bytes:
    """SHA256(enc_key || nonce || counter_be8) — 单个 32 字节 keystream 块。"""
    return hashlib.sha256(
        enc_key + nonce + counter.to_bytes(_COUNTER_LEN, "big")
    ).digest()


def _ctr_xor(enc_key: bytes, nonce: bytes, data: bytes) -> bytes:
    """SHA256-CTR XOR。data 可以是 plaintext 或 ciphertext，函数对称。"""
    out = bytearray(len(data))
    counter = 0
    for off in range(0, len(data), _BLOCK_LEN):
        ks = _keystream_block(enc_key, nonce, counter)
        chunk = data[off:off + _BLOCK_LEN]
        for j in range(len(chunk)):
            out[off + j] = chunk[j] ^ ks[j]
        counter += 1
    return bytes(out)


def _mac(mac_key: bytes, nonce: bytes, ct: bytes) -> bytes:
    return hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()


# ============================================================
#  高层：加密 / 解密
# ============================================================

def encrypt(obj: dict, password: str) -> dict:
    """加密 dict → 输出可 JSON 序列化的 wrapper dict。

    每次调用都生成新 salt + nonce（即便内容/密码相同），保证语义安全。
    """
    salt = secrets.token_bytes(_SALT_LEN)
    nonce = secrets.token_bytes(_NONCE_LEN)
    enc_key, mac_key = _derive_keys(password, salt, _PBKDF2_ITERS)
    pt = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ct = _ctr_xor(enc_key, nonce, pt)
    tag = _mac(mac_key, nonce, ct)
    return {
        "v": _VERSION,
        "kdf": {
            "algo": "pbkdf2-sha256",
            "iters": _PBKDF2_ITERS,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {
            "algo": "sha256ctr",
            "nonce": base64.b64encode(nonce).decode("ascii"),
        },
        "mac": {
            "algo": "hmac-sha256",
            "tag": base64.b64encode(tag).decode("ascii"),
        },
        "data": base64.b64encode(ct).decode("ascii"),
    }


def decrypt(wrapper: dict, password: str) -> Any:
    """解密 wrapper dict。

    返回：解密后的对象（通常为 dict）。
    返回 None：密码错、文件被篡改、格式非法 — 三种情况都让调用方走同一个分支。
    """
    try:
        if not isinstance(wrapper, dict):
            return None
        if int(wrapper.get("v", -1)) != _VERSION:
            return None
        kdf = wrapper.get("kdf") or {}
        cipher = wrapper.get("cipher") or {}
        mac = wrapper.get("mac") or {}
        if kdf.get("algo") not in _VALID_ALGOS:
            return None
        if cipher.get("algo") not in _VALID_ALGOS:
            return None
        if mac.get("algo") not in _VALID_ALGOS:
            return None
        salt = base64.b64decode(kdf["salt"])
        iters = int(kdf["iters"])
        nonce = base64.b64decode(cipher["nonce"])
        tag_in = base64.b64decode(mac["tag"])
        ct = base64.b64decode(wrapper["data"])

        enc_key, mac_key = _derive_keys(password, salt, iters)
        tag_actual = _mac(mac_key, nonce, ct)
        # **先验证 MAC 再解密**：避免把错误密码下的"乱码"作为有效数据返回
        if not hmac.compare_digest(tag_in, tag_actual):
            return None
        pt = _ctr_xor(enc_key, nonce, ct)
        return json.loads(pt.decode("utf-8"))
    except Exception:
        return None


# ============================================================
#  文件 IO（原子写 + 自动识别明文/密文）
# ============================================================

def encrypt_to_file(obj: dict, password: str, path: str) -> None:
    """加密并原子写盘（tmp + os.replace），防止崩溃产生半截文件。"""
    wrapper = encrypt(obj, password)
    payload = json.dumps(wrapper, ensure_ascii=False, indent=2)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload)
    os.replace(tmp, path)


def decrypt_from_file(password: str, path: str) -> Any:
    """从文件解密。文件不存在/损坏/密码错 → 一律返回 None。

    兼容明文 JSON：若顶层 ``v`` 字段缺失且 JSON 可解析，返回原文（便于兼容未加密老文件）。
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None
    try:
        wrapper = json.loads(text)
    except Exception:
        return None
    # 兼容：如果文件不是 secure_json 格式（即无 v/kdf/cipher/mac/data 字段），
    # 直接把原文当明文 JSON 返回。这样新代码可以无缝读取老的明文文件。
    if not _looks_like_secure_wrapper(wrapper):
        return wrapper
    return decrypt(wrapper, password)


def is_encrypted_file(path: str) -> bool:
    """快速判断文件是否加密（读首段不抛异常）。"""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(4096)
    except Exception:
        return False
    try:
        obj = json.loads(head)
    except Exception:
        return False
    return _looks_like_secure_wrapper(obj)


def is_secure_wrapper(obj: Any) -> bool:
    """判断**任意一层 JSON 子树**是否是 secure wrapper（公开 API）。

    与 :func:`is_encrypted_file` 的区别：后者只检查整个文件的顶层，
    而加密内容经常只是配置文件里的一个字段
    （如 ``config["highspeed_downloads"]``），此时必须用本函数。
    """
    return _looks_like_secure_wrapper(obj)


def _looks_like_secure_wrapper(obj: Any) -> bool:
    """检查 JSON 是否符合 secure_json 输出格式。

    顶层应当有：v (int == _VERSION) + kdf (dict) + cipher (dict) + mac (dict) + data (str base64)。
    如果 data 也是 dict（明文 JSON）则视为非加密。
    """
    if not isinstance(obj, dict):
        return False
    try:
        if int(obj.get("v", -1)) != _VERSION:
            return False
    except Exception:
        return False
    for key in ("kdf", "cipher", "mac"):
        if not isinstance(obj.get(key), dict):
            return False
    # data 必须是非空 base64 字符串
    data = obj.get("data")
    if not isinstance(data, str) or not data:
        return False
    return True
