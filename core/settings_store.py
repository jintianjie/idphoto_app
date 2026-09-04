# -*- coding: utf-8 -*-
"""
settings_store — 用户设置 JSON 持久化
======================================

设计目标
--------
让"用户可手动编辑的简单参数"以 JSON 形式持久化到 ``settings.json``：
  - 启动时读一次（不存在则用默认值）
  - 关闭时落盘一次
  - 字段缺失/类型错误时自动回退到默认值
  - 与原 configparser 风格完全脱离

API
---
``SettingsStore(path, defaults)``
  - ``path``: 默认 ``<项目根>/settings.json``
  - ``defaults``: dict，缺失字段的默认值
  - ``.data``: 当前 dict（可直接读写）
  - ``.save()``: 落盘（原子写）
  - ``.reload()``: 重新读盘（实现"手动改文件后立即生效"只需 watcher，watcher 不做）
"""
import json
import os
from threading import RLock
from typing import Any


class SettingsStore:
    def __init__(self, path: str, defaults: dict):
        self._path = path
        self._defaults = defaults
        self._lock = RLock()
        self.data: dict = {}
        self.reload()

    # ----------------------------------------------------------
    def reload(self) -> None:
        """从磁盘加载，失败/缺字段自动补默认值。"""
        with self._lock:
            disk = {}
            if os.path.exists(self._path):
                try:
                    with open(self._path, "r", encoding="utf-8") as f:
                        disk = json.load(f)
                except Exception:
                    disk = {}
            if not isinstance(disk, dict):
                disk = {}
            merged = dict(self._defaults)
            for k, v in disk.items():
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            self.data = merged

    # ----------------------------------------------------------
    def save(self) -> None:
        """原子写：tmp + os.replace。"""
        with self._lock:
            tmp = self._path + ".tmp"
            payload = json.dumps(self.data, ensure_ascii=False, indent=2)
            # 确保目录存在
            d = os.path.dirname(self._path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(payload)
            os.replace(tmp, self._path)

    # ----------------------------------------------------------
    @property
    def path(self) -> str:
        return self._path

    # ----------------------------------------------------------
    # 嵌套 key 工具（用 "." 分隔，如 "ui.watermark_text"）
    # ----------------------------------------------------------
    def _split_key(self, key: str) -> list:
        return [k for k in key.split(".") if k]

    def _dig(self, root: dict, parts: list):
        cur = root
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return None, False
            cur = cur[p]
        return cur, True

    def _place(self, root: dict, parts: list, value) -> None:
        cur = root
        for i, p in enumerate(parts[:-1]):
            if not isinstance(cur.get(p), dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value

    def get(self, key: str, default: Any = None) -> Any:
        if "." in key:
            parts = self._split_key(key)
            v, ok = self._dig(self.data, parts)
            return v if ok else default
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if "." in key:
            parts = self._split_key(key)
            if parts:
                self._place(self.data, parts, value)
            return
        self.data[key] = value

    def touch_dir(self) -> None:
        """暴露磁盘目录（让 UI 弹「在文件管理器中打开」）。"""
        d = os.path.dirname(self._path)
        os.makedirs(d, exist_ok=True)
        return d


# ============================================================
#  项目级默认配置（仅简单参数；不存路径/凭据）
# ============================================================

DEFAULT_SETTINGS = {
    "appearance": {
        "theme_mode": "light",               # light 浅色（默认） / dark 深色
        "accent_color": "#F97316",          # 强调色（主题色），可自定义
        "app_name": "证件照制作工具",         # 软件名称（窗口标题 + 顶栏名称）
        "app_icon": "",                      # 自定义图标路径(绝对)；空=默认 logo
    },
    "ui": {
        # 全局界面字号缩放（0.8~1.2，1.0 = Fluent 原厂字号）。
        # 改小 = 界面所有文字整体缩小一档（窄栏放不下文字时调 0.85~0.9）。
        # 重启生效 —— 字号在控件构造时确定，运行中改不会回溯。
        "font_scale": 0.9,
        "last_process_quality": 95,        # 默认 JPEG 质量
        "last_render_mode": 0,              # 0 纯色 / 1 上下渐变 / 2 中心渐变
        "watermark_text": "2",              # 水印默认文字
        "auto_select_first_after_batch": True,
        "default_dpi": 300,                 # 默认导出 DPI
        "default_kb_limit": 0,              # 默认 KB 限制(0=不限)
    },
    "processing": {
        # 默认抠图 / 人脸模型：键名与 model_downloader BUILTIN_MANIFEST 一致
        "default_matting_model": "modnet_photographic_portrait_matting",
        "default_face_model": "retinaface-resnet50",
    },
    "export": {
        "default_format": "png",            # png / jpg
    },
    "layout": {
        # 自定义排版规格列表（增删存这里）。每项: (name, w, h, default_count, bg_hex)
        # 前 11 项（"一寸"等）由 styles.PRESET_SIZES 提供；本字段只存用户新增的。
        "custom_presets": [],
        # 自定义纸张规格。每项: (name, 宽mm, 高mm)；内置纸张由 styles.PAPER_PRESETS 提供。
        "custom_paper_presets": [],
    },
    "model": {
        "preferred_source": "mirror",
        "default_timeout_sec": 120,
    },
    "download": {
        "remember_password": False,         # 是否记住高速下载密码（仅会话内）
    },
}


def get_default_settings_path(root_dir: str) -> str:
    return os.path.join(root_dir, "settings.json")
