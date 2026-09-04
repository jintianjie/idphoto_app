# -*- coding: utf-8 -*-
"""
mirror_manager — 高速下载地址 + 密码（只读解锁）
==============================================

职责边界（与主程序强绑定，请勿再加写能力）
------------------------------------------
  ``tools/mirror_builder/``（独立生成器软件）  → **写**：设密码、填地址、加密、产出配置文件
  本模块                                      → **读**：校验密码、解出地址、交给下载器

也就是说：密码和下载地址只能在「生成器」里写入，主程序不提供任何
「改密码 / 重置密码 / 重新加密」的入口。地址失效时，作者用生成器重新生成一份
``model_download_config.json`` 发给用户即可。

两层配置
--------
1. **公开镜像 (mirrors) — 明文**
   来自 ``model_download_config.json`` 的 ``mirrors`` 字段。
   内含 {name, prefix} 列表，用户可自行增删（这部分不涉及密码，允许写）。
   标准下载只走这里。

2. **加密高速 (highspeed_downloads) — 加密，只读**
   来自 ``model_download_config.json`` 的 ``highspeed_downloads`` 字段。
   该字段值是一个 secure_json wrapper，解密后才是内部 dict：
       ``{"models": [{name, display_name, url}, ...], "updated_at": <str>, "note": <str>}``

解锁逻辑（极简，唯一判定）
--------------------------
密码只充当一件事 —— **secure wrapper 的解密密钥**。

  - 用户输入密码 → ``try_unlock(password)``
  - 用密码派生密钥去解 secure wrapper：
      * MAC 校验通过（密码正确）→ 解锁成功，缓存解密结果
      * MAC 校验失败 / 文件损坏 / 被篡改 → 返回 False，``last_error`` 给出原因
  - 之后下载器从 ``get_highspeed_models()`` 拿直链 URL

不再有「密码双重身份」「管理员密码」「内部 password 字段二次校验」等绕弯逻辑：
密码对 = 能解开 = 能下载。简单、可预期。

字段约定（与主程序 / 生成器兼容）
--------------------------------
  - ``mirrors``: ``[{"name": str, "prefix": str}, ...]``，prefix 拼到 manifest URL 前
  - ``manifest``: ``{<model_name>: {"url": ..., "filename": ...}, ...}``
  - ``default_timeout``: int (秒)
  - ``highspeed_downloads``: secure_json wrapper（或老格式兼容：明文 dict）
  - 历史遗留的 ``highspeed_admin`` 字段：主程序**忽略**，不影响解锁。
"""
import json
import os
from threading import RLock
from typing import Any

from . import secure_json as sj


# ============================================================
#  文件路径
# ============================================================

def get_config_path(root_dir: str) -> str:
    return os.path.join(root_dir, "model_download_config.json")


# ============================================================
#  MirrorManager 单例（只读）
# ============================================================

class MirrorManager:
    """统一管理镜像源 + 加密高速下载（读 + 解锁，不提供任何写入口）。"""

    def __init__(self, root_dir: str):
        self._root = root_dir
        self._config_path = get_config_path(root_dir)
        # 公开镜像（启动即明文读出）
        self._public = self._load_public()
        # 加密高速镜像状态
        self._unlocked_obj = None        # 解密后的 highspeed dict
        self._last_error = ""            # 最近一次解锁失败原因
        self._lock = RLock()

    # ----------------------------------------------------------
    #  公开镜像 + manifest（启动即可读）
    # ----------------------------------------------------------
    def _load_public(self) -> dict:
        """读 model_download_config.json 的明文部分。"""
        if not os.path.exists(self._config_path):
            return {"mirrors": [], "manifest": {}, "default_timeout": 60,
                    "highspeed_downloads": None, "_exists": False}
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg.setdefault("mirrors", [])
        cfg.setdefault("manifest", {})
        cfg.setdefault("default_timeout", 60)
        cfg["_exists"] = True
        return cfg

    def get_mirrors(self) -> list:
        """返回 [{name, prefix}, ...]，按文件原序。"""
        return list(self._public.get("mirrors", []))

    def get_manifest(self) -> dict:
        return dict(self._public.get("manifest", {}))

    def get_default_timeout(self) -> int:
        try:
            return int(self._public.get("default_timeout", 60))
        except Exception:
            return 60

    def add_mirror(self, name: str, prefix: str) -> bool:
        """手动添加一个镜像，原子写回磁盘。"""
        if not name or not prefix:
            return False
        with self._lock:
            mirrors = self._public.setdefault("mirrors", [])
            for m in mirrors:
                if m.get("name") == name:
                    return False
            mirrors.append({"name": name, "prefix": prefix})
            self._save_public()
            return True

    def remove_mirror(self, name: str) -> bool:
        with self._lock:
            mirrors = self._public.get("mirrors", [])
            for i, m in enumerate(mirrors):
                if m.get("name") == name:
                    mirrors.pop(i)
                    self._save_public()
                    return True
            return False

    def _save_public(self):
        """仅写回 mirrors + manifest + default_timeout 等明文字段；
        加密高速镜像原样保留（本模块从不改写它）。"""
        payload = {
            "mirrors": self._public.get("mirrors", []),
            "manifest": self._public.get("manifest", {}),
            "default_timeout": self.get_default_timeout(),
            "highspeed_downloads": self._public.get("highspeed_downloads"),
        }
        tmp = self._config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._config_path)

    # ----------------------------------------------------------
    #  加密高速镜像（只读解锁）
    # ----------------------------------------------------------
    def is_highspeed_locked(self) -> bool:
        """存在加密高速镜像 wrapper 且尚未解锁 → True。"""
        with self._lock:
            return (self._public.get("highspeed_downloads") is not None
                    and self._unlocked_obj is None)

    def has_highspeed_config(self) -> bool:
        return self._public.get("highspeed_downloads") is not None

    def is_highspeed_unlocked(self) -> bool:
        with self._lock:
            return self._unlocked_obj is not None

    def try_unlock(self, password: str) -> bool:
        """尝试用密码解锁加密高速镜像。

        极简逻辑：密码就是 secure wrapper 的解密密钥。
          - 解密 + MAC 校验通过  → 解锁成功，缓存解密结果
          - 任何失败（密码错 / 文件损坏 / 被篡改） → False，原因写入 ``last_error``

        老的明文 dict 格式（无加密）也能直接接受：返回其中的 models。
        """
        with self._lock:
            self._last_error = ""
            sec = self._public.get("highspeed_downloads")
            if sec is None:
                # 没有加密 wrapper → 不需要解锁；视为"未锁"
                self._unlocked_obj = None
                return True

            # 已经是明文 dict（老格式兼容）
            if isinstance(sec, dict) and not sj.is_secure_wrapper(sec):
                self._unlocked_obj = sec if isinstance(sec, dict) else {"models": []}
                self._last_error = ""
                return True

            obj = sj.decrypt(sec, password)
            if obj is None or not isinstance(obj, dict):
                self._unlocked_obj = None
                self._last_error = "密码错误，或加密 JSON 已损坏 / 被篡改"
                return False
            self._unlocked_obj = obj
            self._last_error = ""
            return True

    def lock(self) -> None:
        with self._lock:
            self._unlocked_obj = None
            self._last_error = ""

    def get_highspeed_models(self) -> list:
        """返回 [{name, display_name, url}, ...]（仅解锁后可用，否则空）。"""
        with self._lock:
            if not self._unlocked_obj:
                return []
            models = self._unlocked_obj.get("models", [])
            return list(models) if isinstance(models, list) else []

    # ----------------------------------------------------------
    #  对外状态
    # ----------------------------------------------------------
    @property
    def last_error(self) -> str:
        """最近一次解锁失败的原因；成功时为空串。"""
        with self._lock:
            return self._last_error


def try_decrypt_legacy_highspeed(wrapper: Any, password: str) -> Any:
    """兼容老格式：highspeed_downloads 可能是 dict（明文旧版）也可能是 secure wrapper。

    返回解密/明文 dict 或 None。
    """
    if not isinstance(wrapper, dict):
        return None
    if sj.is_secure_wrapper(wrapper):
        return sj.decrypt(wrapper, password)
    return wrapper
