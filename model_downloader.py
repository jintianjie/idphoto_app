# -*- coding: utf-8 -*-
"""
模型远程下载器（简化版）
------------------------
每个模型在 model_download_config.json 中直接给出两条直链：
  - original: 官方原地址（GitHub Releases / HuggingFace），国内通常需特殊网络
  - mirror:   加速地址（hf-mirror.com 镜像，国内直连，默认优先）
下载时按 source 选择首选源，失败自动回退另一源。
高速下载地址（highspeed_downloads）单独加密，由 MirrorManager 处理，不在此模块。

特性：
  - 后台线程下载，UI 不阻塞；
  - 单文件超时自动取消（不会卡死软件）；
  - 进度回调（百分比/速度/当前 URL）；
  - 可选 sha256 完整性校验（无则跳过）；
  - 配置文件用户可直接编辑（改地址 / 改默认源 / 改超时）。
"""
import hashlib
import json
import os
import socket
import time
import threading
import urllib.error
import urllib.request


# ============================================================
#  本地配置文件路径与读写
# ============================================================

_CONFIG_FILENAME = "model_download_config.json"


def _get_config_file_path():
    """返回本地配置文件的绝对路径（与本模块同目录）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _CONFIG_FILENAME)


def _default_config():
    """返回默认配置字典。

    每个模型直接给出两条可用直链（用户可编辑本 JSON 修改地址）：
      - original: 官方原地址（GitHub Releases / HuggingFace），国内通常需特殊网络
      - mirror:   加速地址（hf-mirror.com 镜像，国内直连，默认优先）
    高速下载地址（highspeed_downloads）单独加密，不在此处。
    """
    return {
        # 保留字段，已不再用于下载（改用 per-model 直链），仅供只读兼容
        "mirrors": [],
        "manifest": {
            "modnet_photographic_portrait_matting": {
                "filename": "modnet_photographic_portrait_matting.onnx",
                "original": "https://github.com/Zeyi-Lin/HivisionIDPhotos/releases/download/pretrained-model/modnet_photographic_portrait_matting.onnx",
                "mirror": "https://hf-mirror.com/DavG25/modnet-pretrained-models/resolve/main/models/modnet_photographic_portrait_matting.onnx",
            },
            "hivision_modnet": {
                "filename": "hivision_modnet.onnx",
                "original": "https://github.com/Zeyi-Lin/HivisionIDPhotos/releases/download/pretrained-model/hivision_modnet.onnx",
                "mirror": "https://hf-mirror.com/xiaofennuonuo/comfyui/resolve/main/hivision_modnet.onnx",
            },
            "rmbg-1.4": {
                "filename": "rmbg-1.4.onnx",
                "original": "https://huggingface.co/briaai/RMBG-1.4/resolve/main/onnx/model.onnx?download=true",
                "mirror": "https://hf-mirror.com/briaai/RMBG-1.4/resolve/main/onnx/model.onnx",
            },
            "birefnet-v1-lite": {
                "filename": "birefnet-v1-lite.onnx",
                "original": "https://github.com/ZhengPeng7/BiRefNet/releases/download/v1/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx",
                "mirror": "https://hf-mirror.com/EmmaJohnson311/TensorRT-ONNX-collect/resolve/main/BiRefNet-v2-onnx/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx",
            },
            "retinaface-resnet50": {
                "filename": "retinaface-resnet50.onnx",
                "original": "https://github.com/Zeyi-Lin/HivisionIDPhotos/releases/download/pretrained-model/retinaface-resnet50.onnx",
                "mirror": "https://hf-mirror.com/TheEeeeLin/HivisionIDPhotos_matting/resolve/main/retinaface-resnet50.onnx",
            },
        },
        "preferred_source": "mirror",
        "default_timeout": 120,
    }


def load_config():
    """加载本地配置文件。

    返回 dict(manifest, preferred_source, default_timeout, highspeed_downloads)。
    文件不存在或格式错误时返回默认配置，同时自动创建默认文件。
    """
    path = _get_config_file_path()
    if not os.path.isfile(path):
        cfg = _default_config()
        save_config(cfg)
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        cfg = _default_config()
        save_config(cfg)
        return cfg
    # 校验必要字段，缺失的用默认补齐
    default = _default_config()
    if "manifest" not in cfg or not isinstance(cfg.get("manifest"), dict):
        cfg["manifest"] = default["manifest"]
    if "preferred_source" not in cfg or cfg.get("preferred_source") not in ("mirror", "original"):
        cfg["preferred_source"] = default["preferred_source"]
    if "default_timeout" not in cfg or not isinstance(cfg.get("default_timeout"), int):
        cfg["default_timeout"] = default["default_timeout"]
    # 保留已有高速下载加密块（如有），不在此处生成或覆盖
    if "highspeed_downloads" not in cfg:
        cfg["highspeed_downloads"] = None
    return cfg


def save_config(cfg):
    """把配置写入本地 JSON 文件（保留 highspeed_downloads 加密块）。"""
    path = _get_config_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "manifest": cfg.get("manifest", _default_config()["manifest"]),
        "preferred_source": cfg.get("preferred_source", "mirror"),
        "default_timeout": cfg.get("default_timeout", 120),
        "highspeed_downloads": cfg.get("highspeed_downloads"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def get_config_path():
    """返回配置文件路径，供 UI 层调用（如"打开配置文件"按钮）。"""
    return _get_config_file_path()


# ============================================================
#  向后兼容：保留 BUILTIN_MANIFEST 供直接引用
# ============================================================

_BUILTINS = _default_config()

BUILTIN_MANIFEST = _BUILTINS["manifest"]


# ============================================================
#  工具
# ============================================================

def load_manifest(source):
    """加载 manifest，支持本地 JSON 文件路径或 HTTP(S) URL。

    JSON 格式：
    {
      "<model_name>": {
        "url": "<原始下载URL>",
        "filename": "<保存到 model/ 目录的文件名>",
        "sha256": "<可选，未提供则跳过校验>"
      },
      ...
    }
    返回 dict；失败抛 RuntimeError。
    """
    if not source:
        raise RuntimeError("manifest 来源为空")
    if source.lower().startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(source, headers={"User-Agent": "idphoto-app/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
        except Exception as e:
            raise RuntimeError(f"下载 manifest 失败: {e}") from e
    else:
        # 本地文件
        if not os.path.isfile(source):
            raise RuntimeError(f"找不到本地 manifest: {source}")
        with open(source, "rb") as f:
            data = f.read()
    try:
        m = json.loads(data.decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"manifest 解析失败: {e}") from e
    if not isinstance(m, dict) or not m:
        raise RuntimeError("manifest 格式无效，应为非空 dict")
    return m


def list_missing_models(model_dir, manifest=None):
    """列出当前缺失的模型文件名清单。"""
    manifest = manifest or BUILTIN_MANIFEST
    miss = []
    for name, info in manifest.items():
        fn = info.get("filename") or (name + ".onnx")
        p = os.path.join(model_dir, fn)
        if not os.path.isfile(p):
            miss.append({"name": name, "info": info, "path": p})
    return miss


# ============================================================
#  单文件下载（带超时 + 进度 + 取消）
# ============================================================

class DownloadCancelled(Exception):
    """用户/超时取消下载。"""


class Progress:
    """下载进度记录。"""

    __slots__ = ("downloaded", "total", "speed", "elapsed", "url", "done", "error")

    def __init__(self, url, total=0):
        self.url = url
        self.total = total or 0
        self.downloaded = 0
        self.speed = 0.0
        self.elapsed = 0.0
        self.done = False
        self.error = None

    @property
    def percent(self):
        if not self.total:
            return 0.0
        return min(100.0, self.downloaded * 100.0 / self.total)


def _download_one(url, target_path, timeout=60, cancel_event=None,
                  progress_cb=None, chunk=64 * 1024):
    """同步下载单个文件，支持 progress_cb(progress_info_dict) 与 cancel_event.set() 取消。

    超时通过 urlopen(timeout=) 实现：它对单次连接设置 socket 级超时，且连接建立后
    该超时仍对后续 read 有效——网络空闲超过 timeout 秒即抛 socket.timeout，
    我们捕获后抛 DownloadCancelled 让上层停止任务。不再使用 socket.setdefaulttimeout
    以避免污染进程级全局 socket 默认超时（影响其他线程的网络操作）。
    """
    prog = Progress(url=url)

    # 创建取消标志（局部）
    if cancel_event is None:
        cancel_event = threading.Event()

    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "idphoto-app/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = resp.headers.get("Content-Length")
            try:
                prog.total = int(total) if total else 0
            except (TypeError, ValueError):
                prog.total = 0

            tmp = target_path + ".part"
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            try:
                f = open(tmp, "wb")
            except OSError as e:
                prog.error = f"无法写入 {tmp}: {e}"
                prog.done = True
                if progress_cb:
                    progress_cb(prog)
                raise

            last_report = start
            last_bytes = 0
            try:
                while True:
                    if cancel_event.is_set():
                        raise DownloadCancelled("用户取消")
                    try:
                        data = resp.read(chunk)
                    except socket.timeout:
                        raise DownloadCancelled(f"下载超时（{timeout}s）")
                    if not data:
                        break
                    f.write(data)
                    prog.downloaded += len(data)
                    now = time.time()
                    # 限频：每 0.2 秒报一次进度
                    if progress_cb and now - last_report >= 0.2:
                        elapsed = now - last_report
                        prog.speed = (prog.downloaded - last_bytes) / elapsed if elapsed > 0 else 0
                        prog.elapsed = now - start
                        progress_cb(prog)
                        last_report = now
                        last_bytes = prog.downloaded
                f.flush()
            finally:
                try:
                    f.close()
                except Exception:
                    pass
            # 最后一次进度
            prog.done = True
            prog.elapsed = time.time() - start
            if progress_cb:
                progress_cb(prog)
            # 改名原子替换
            os.replace(tmp, target_path)
    except DownloadCancelled:
        # 清理半成品
        try:
            os.remove(target_path + ".part")
        except OSError:
            pass
        prog.error = "已取消"
        prog.done = True
        if progress_cb:
            progress_cb(prog)
        raise
    except Exception as e:
        try:
            os.remove(target_path + ".part")
        except OSError:
            pass
        prog.error = f"下载失败: {e}"
        prog.done = True
        if progress_cb:
            progress_cb(prog)
        raise
    return prog


def _try_url_chain(target_urls, target_path, timeout=60, cancel_event=None,
                   progress_cb=None, mirrors_info=None):
    """按 URL 列表依次尝试下载，命中一个即可。

    mirrors_info: 可选，传 (mirror_name, target_url, err) 给 cb 失败日志。
    """
    last_err = None
    for i, url in enumerate(target_urls):
        if cancel_event and cancel_event.is_set():
            raise DownloadCancelled("用户取消")
        try:
            return _download_one(
                url, target_path, timeout=timeout,
                cancel_event=cancel_event, progress_cb=progress_cb,
            )
        except DownloadCancelled:
            raise
        except Exception as e:
            last_err = e
            if mirrors_info is not None:
                mirrors_info((url, str(e)))
            # 继续尝试下一个 URL
            continue
    raise RuntimeError(f"所有下载源均失败: {last_err}")


# ============================================================
#  入口：ensure_models —— 在后台线程跑，逐模型下载
# ============================================================

def ensure_models(model_dir, manifest=None, source="mirror", timeout=120,
                  cancel_event=None, on_progress=None, on_done=None,
                  on_log=None):
    """检查缺失模型并按顺序下载；调用方应在后台线程调用。

    参数：
      model_dir: 模型保存目录
      manifest: dict，默认用 BUILTIN_MANIFEST（每项含 filename/original/mirror）
      source:   "mirror"（默认，加速地址 hf-mirror，国内直连）
                或 "original"（官方原地址，通常需特殊网络）；首选源失败自动回退另一源
      timeout: 单文件单次尝试的超时（秒）
      cancel_event: threading.Event，set() 取消
      on_progress: 主线程回调 fn(state_dict) —— 当前正在下哪个模型、percent、log
      on_done: 主线程回调 fn(success: bool, message: str)

    state_dict 格式：
      {
        "stage": "checking" | "downloading" | "verifying" | "done",
        "current_model": str | None,
        "current_filename": str | None,
        "percent": float,          # 当前模型 0~100
        "speed_bytes_per_s": float,
        "elapsed": float,
        "completed": int,          # 已完成数
        "total": int,              # 总共需要下载数
        "log": str,                # 最新一行日志（含当前 URL 与进度）
      }

    软件本体只需 ensure_models(target_dir, on_progress=lambda s: root.after(0, lambda: ui_update(s)))。
    """
    if cancel_event is None:
        cancel_event = threading.Event()
    if manifest is None:
        manifest = BUILTIN_MANIFEST
    source = source if source in ("mirror", "original") else "mirror"

    state = {
        "stage": "checking",
        "current_model": None,
        "current_filename": None,
        "percent": 0.0,
        "speed_bytes_per_s": 0.0,
        "elapsed": 0.0,
        "completed": 0,
        "total": 0,
        "log": "开始检查模型...",
    }

    def _emit(msg=None):
        if msg is not None:
            state["log"] = msg
        if on_progress:
            on_progress(dict(state))

    def _on_progress_inner(prog):
        state["percent"] = prog.percent
        state["speed_bytes_per_s"] = prog.speed
        state["elapsed"] = prog.elapsed
        fn = state.get("current_filename") or ""
        done = state.get("completed", 0)
        total = state.get("total", 0)
        src = "加速" if state.get("_using_mirror") else "原"
        _emit(f"[{done + 1}/{total}] {fn}（{src}） {prog.percent:.1f}%  "
              f"{prog.speed / 1024:.0f} KB/s\n  {prog.url}")

    def _on_mirror_fail_inner(info):
        url, err = info
        _emit(f"✗ 该源失败，回退另一源: {url[:90]}\n  {err}")

    try:
        miss = list_missing_models(model_dir, manifest)
        state["total"] = len(miss)
        state["completed"] = 0
        if not miss:
            state["stage"] = "done"
            _emit("所有模型已就绪")
            if on_done:
                on_done(True, "全部就绪")
            return

        _emit(f"缺失 {len(miss)} 个模型，开始下载")
        state["stage"] = "downloading"

        for item in miss:
            if cancel_event.is_set():
                raise DownloadCancelled("用户取消")
            name = item["name"]
            info = item["info"]
            target_path = item["path"]
            state["current_model"] = name
            state["current_filename"] = info.get("filename", name + ".onnx")
            state["percent"] = 0.0
            _emit(f"准备下载 {name}")

            # 构造候选 URL：首选 source，失败回退另一源
            raw_url = info.get("original") or info.get("url")
            mirror_url = info.get("mirror")
            if source == "mirror":
                urls = [u for u in (mirror_url, raw_url) if u]
            else:
                urls = [u for u in (raw_url, mirror_url) if u]
            state["_using_mirror"] = (source == "mirror")

            try:
                _try_url_chain(
                    urls, target_path,
                    timeout=timeout,
                    cancel_event=cancel_event,
                    progress_cb=_on_progress_inner,
                    mirrors_info=_on_mirror_fail_inner,
                )
            except DownloadCancelled as e:
                _emit(f"已取消：{e}")
                if on_done:
                    on_done(False, f"已取消：{e}")
                return
            except Exception as e:
                _emit(f"❌ 下载 {name} 失败：{e}")
                if on_done:
                    on_done(False, f"下载 {name} 失败：{e}")
                return

            # 完整性校验（如有 sha256）
            sha = info.get("sha256")
            if sha:
                state["stage"] = "verifying"
                _emit(f"校验 SHA256...")
                h = hashlib.sha256()
                with open(target_path, "rb") as f:
                    while True:
                        b = f.read(64 * 1024)
                        if not b:
                            break
                        h.update(b)
                if h.hexdigest().lower() != str(sha).lower():
                    try:
                        os.remove(target_path)
                    except OSError:
                        pass
                    msg = f"❌ {name} SHA256 不匹配，文件已删除"
                    _emit(msg)
                    if on_done:
                        on_done(False, msg)
                    return

            state["completed"] += 1
            _emit(f"✓ {name} 完成")

        state["stage"] = "done"
        _emit(f"全部完成，共 {state['completed']} 个模型")
        if on_done:
            on_done(True, f"全部完成，共 {state['completed']} 个模型")
    except DownloadCancelled as e:
        _emit(f"已取消：{e}")
        if on_done:
            on_done(False, f"已取消：{e}")
    except Exception as e:
        _emit(f"发生错误：{e}")
        if on_done:
            on_done(False, f"发生错误：{e}")


# ============================================================
#  入口：download_highspeed —— 按配置里的直链列表下载（高速通道）
# ============================================================

def download_highspeed(model_dir, models, timeout=120, cancel_event=None,
                        on_progress=None, on_done=None):
    """按 highspeed_downloads.models 列表，把每个直链模型下载到 model_dir。

    models: list[dict]，每项含：
        name:     模型标识（也用作文件名，若无 filename）
        url:      直链下载地址
        filename: 可选，保存到 model/ 的文件名；缺省用 name
    仅下载本地缺失的模型；已存在则跳过。
    其余参数与 ensure_models 一致（timeout 为单次读取空闲超时，非总时长）。
    """
    if cancel_event is None:
        cancel_event = threading.Event()
    state = {
        "stage": "downloading",
        "current_model": None,
        "current_filename": None,
        "percent": 0.0,
        "speed_bytes_per_s": 0.0,
        "elapsed": 0.0,
        "completed": 0,
        "total": 0,
        "log": "开始高速下载...",
    }

    def _emit(msg=None):
        if msg is not None:
            state["log"] = msg
        if on_progress:
            on_progress(dict(state))

    def _on_progress_inner(prog):
        state["percent"] = prog.percent
        state["speed_bytes_per_s"] = prog.speed
        state["elapsed"] = prog.elapsed
        _emit(f"{prog.percent:.1f}%  {prog.speed / 1024:.0f} KB/s")

    try:
        if not models:
            _emit("没有可下载的模型")
            if on_done:
                on_done(True, "没有可下载的模型")
            return

        todo = []
        for m in models:
            fn = m.get("filename") or m.get("name") or "model.onnx"
            target = os.path.join(model_dir, fn)
            if os.path.isfile(target):
                continue
            todo.append((m, target, fn))

        state["total"] = len(todo)
        if not todo:
            state["completed"] = len(models)
            _emit("所有模型已就绪")
            if on_done:
                on_done(True, "所有模型已就绪")
            return

        _emit(f"缺失 {len(todo)} 个模型，开始高速下载")
        state["stage"] = "downloading"

        for m, target, fn in todo:
            if cancel_event.is_set():
                raise DownloadCancelled("用户取消")
            if not m.get("url"):
                _emit(f"⚠ {fn} 缺少 url，跳过")
                state["completed"] += 1
                continue
            state["current_model"] = m.get("name", fn)
            state["current_filename"] = fn
            state["percent"] = 0.0
            _emit(f"下载 {fn}")
            try:
                _download_one(
                    m["url"], target, timeout=timeout,
                    cancel_event=cancel_event, progress_cb=_on_progress_inner,
                )
            except DownloadCancelled as e:
                _emit(f"已取消：{e}")
                if on_done:
                    on_done(False, f"已取消：{e}")
                return
            except Exception as e:
                _emit(f"❌ {fn} 失败：{e}")
                if on_done:
                    on_done(False, f"{fn} 下载失败：{e}")
                return
            state["completed"] += 1
            _emit(f"✓ {fn} 完成")

        state["stage"] = "done"
        _emit(f"全部完成，共 {state['completed']} 个模型")
        if on_done:
            on_done(True, f"全部完成，共 {state['completed']} 个模型")
    except DownloadCancelled as e:
        _emit(f"已取消：{e}")
        if on_done:
            on_done(False, f"已取消：{e}")
    except Exception as e:
        _emit(f"发生错误：{e}")
        if on_done:
            on_done(False, f"发生错误：{e}")
