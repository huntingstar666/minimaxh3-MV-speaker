"""ComfyUI HTTP / WebSocket 客户端：上传、排队、进度、历史、中断。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from . import settings


class ComfyError(RuntimeError):
    pass


def _base() -> str:
    """规范化 ComfyUI 地址：容忍只填 127.0.0.1:8188（缺 http://）。"""
    base = str(settings.load().get("comfy_url") or "http://127.0.0.1:8188").strip().rstrip("/")
    if "://" not in base:
        base = "http://" + base
    return base


def _url(path: str) -> str:
    return _base() + path


def _ws_url(path: str) -> str:
    parts = urlsplit(_base())
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}{path}"


_client_id = uuid.uuid4().hex


def client_id() -> str:
    return _client_id


def http(timeout: float = 30) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def system_stats() -> dict[str, Any]:
    with http(10) as client:
        response = client.get(_url("/system_stats"))
        response.raise_for_status()
        return response.json()


def ping() -> dict[str, Any]:
    started = time.time()
    try:
        stats = system_stats()
        devices = stats.get("devices") or []
        return {
            "ok": True,
            "elapsed": round(time.time() - started, 3),
            "version": (stats.get("system") or {}).get("comfyui_version"),
            "device": (devices[0].get("name") if devices else ""),
            "vram_free": (devices[0].get("vram_free") if devices else None),
        }
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)}


def system_stats() -> dict[str, Any]:
    with http(10) as client:
        response = client.get(_url("/system_stats"))
        response.raise_for_status()
        return response.json()


def object_info(node: str) -> dict[str, Any] | None:
    with http(20) as client:
        response = client.get(_url(f"/object_info/{node}"))
        if response.status_code >= 400:
            return None
        data = response.json()
    return data.get(node)


def combo_values(node: str, field: str) -> list[str]:
    """读取 COMBO 可选值，兼容旧版 [[...]] 与新版 ["COMBO", {"options": [...]}]。"""
    info = object_info(node)
    if not info:
        return []
    for section in ("required", "optional"):
        value = ((info.get("input") or {}).get(section) or {}).get(field)
        items = _unwrap_combo(value)
        if items:
            return items
    return []


def _unwrap_combo(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return []
    # 旧格式：["euler", "dpmpp_2m", ...]
    if all(isinstance(item, (str, int, float)) for item in value):
        return [str(item) for item in value]
    # 新格式：["COMBO", {"options": [...]}]
    if isinstance(value[0], str) and len(value) > 1 and isinstance(value[1], dict):
        options = value[1].get("options")
        if isinstance(options, list):
            return [str(item) for item in options]
    # 另一种旧格式：[["euler", ...], {...}]
    if isinstance(value[0], list):
        return [str(item) for item in value[0]]
    return []


_options_cache: dict[str, Any] = {"time": 0, "value": None}
_options_lock = threading.Lock()
_OPTIONS_SNAPSHOT = settings.DATA_DIR / "options.json"


def options(force: bool = False) -> dict[str, Any]:
    with _options_lock:
        if not force and _options_cache["value"] and time.time() - _options_cache["time"] < 45:
            return _options_cache["value"]
        seam_info = object_info("MiniMaxH3FiniteSegmentVideoSampler") or {}
        seam_optional = (seam_info.get("input") or {}).get("optional") or {}
        value = {
            "unet": combo_values("UNETLoader", "unet_name"),
            "clip": combo_values("CLIPLoader", "clip_name"),
            "vae": combo_values("VAELoader", "vae_name"),
            "lora": combo_values("LoraLoaderModelOnly", "lora_name"),
            "upscaler": combo_values("SelfLiftAvatarH3Sampler", "upscaler_model"),
            "samplers": combo_values("KSamplerSelect", "sampler_name"),
            "schedulers": combo_values("BasicScheduler", "scheduler"),
            "aspects": combo_values("ResolutionSelector", "aspect_ratio"),
            "attention": combo_values("ModelAttentionBackend", "attention"),
            "upscalers_common": combo_values("UpscaleModelLoader", "model_name"),
            "nodes": {
                "selflift": bool(object_info("SelfLiftAvatarH3Sampler")),
                "sigma_shift": bool(object_info("MiniMaxH3SigmaShift")),
                "sigma_refiner": bool(object_info("H3SigmaRefiner")),
                "reference": bool(object_info("MiniMaxH3ReferenceToVideo")),
                "attention": bool(object_info("ModelAttentionBackend")),
                "ltxv": bool(object_info("LTXVConcatAVLatent")),
                "vhs": bool(object_info("VHS_VideoCombine")),
                "seam": bool(seam_info),
                "seam_params": "transition_step" in seam_optional,
            },
        }
        if not value["attention"]:
            value["attention"] = ["comfy kitchen attention", "pytorch attention"]
        _options_cache.update(time=time.time(), value=value)
        try:
            _OPTIONS_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            _OPTIONS_SNAPSHOT.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return value


def last_options() -> dict[str, Any] | None:
    """上一次成功读取的节点信息；ComfyUI 掉线时用于兜底，避免下拉框变空。"""
    with _options_lock:
        if _options_cache["value"]:
            return _options_cache["value"]
    try:
        return json.loads(_OPTIONS_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def upload(name: str, data: bytes) -> str:
    """上传到 ComfyUI input 目录，返回服务端文件名（可能含子目录）。"""
    with http(120) as client:
        response = client.post(
            _url("/upload/image"),
            files={"image": (name, data, "application/octet-stream")},
            data={"overwrite": "true", "type": "input", "subfolder": "h3wb"},
        )
        response.raise_for_status()
        payload = response.json()
    filename = str(payload.get("name") or name)
    subfolder = str(payload.get("subfolder") or "")
    return f"{subfolder}/{filename}" if subfolder else filename


def upload_path(path: Path, name: str | None = None) -> str:
    return upload(name or path.name, path.read_bytes())


def queue(prompt: dict[str, Any]) -> str:
    with http(30) as client:
        response = client.post(_url("/prompt"),
                               json={"prompt": prompt, "client_id": _client_id})
        response.raise_for_status()
        payload = response.json()
    if payload.get("node_errors"):
        detail = json.dumps(payload["node_errors"], ensure_ascii=False)[:1200]
        raise ComfyError(f"工作流校验失败：{detail}")
    prompt_id = str(payload.get("prompt_id") or "")
    if not prompt_id:
        raise ComfyError("ComfyUI 未返回 prompt_id。")
    return prompt_id


def history(prompt_id: str) -> dict[str, Any]:
    with http(30) as client:
        response = client.get(_url(f"/history/{prompt_id}"))
        response.raise_for_status()
        payload = response.json()
    return payload.get(prompt_id) or {}


def queue_state() -> dict[str, Any]:
    with http(10) as client:
        response = client.get(_url("/queue"))
        response.raise_for_status()
        payload = response.json()
    return {"running": payload.get("queue_running") or [],
            "pending": payload.get("queue_pending") or []}


def free_memory(unload_models: bool = True) -> bool:
    """请求 ComfyUI 卸载模型并释放显存；失败不抛（只是优化项）。"""
    try:
        with http(10) as client:
            client.post(_url("/free"),
                        json={"unload_models": bool(unload_models),
                              "free_memory": True})
        return True
    except Exception:  # noqa: BLE001
        return False


def interrupt() -> None:
    try:
        with http(10) as client:
            client.post(_url("/interrupt"))
    except Exception:  # noqa: BLE001
        pass


def cancel_prompt(prompt_id: str) -> str:
    """只取消「我们自己的」prompt：排队中的删队列，正在跑的才 interrupt。

    ComfyUI 的 /interrupt 打断的是当前正在执行的那一条 prompt，不知道它是谁的；
    以前只要本项目任务 active 就无条件 interrupt，会把别人正在跑的生成打断。
    """
    prompt_id = str(prompt_id or "").strip()
    if not prompt_id:
        return "none"
    try:
        state = queue_state()
    except Exception:  # noqa: BLE001  队列查不到时退回旧行为
        interrupt()
        return "interrupt"
    def _ids(items: list[Any]) -> set[str]:
        ids = set()
        for item in items or []:
            try:
                ids.add(str(item[1]))
            except (IndexError, TypeError):
                continue
        return ids
    if prompt_id in _ids(state["running"]):
        interrupt()
        return "interrupt"
    if prompt_id in _ids(state["pending"]):
        try:
            with http(10) as client:
                client.post(_url("/queue"), json={"delete": [prompt_id]})
        except Exception:  # noqa: BLE001
            pass
        return "dequeue"
    return "none"


def view(filename: str, subfolder: str = "", kind: str = "output") -> bytes:
    with http(180) as client:
        response = client.get(_url("/view"),
                              params={"filename": filename, "subfolder": subfolder, "type": kind})
        response.raise_for_status()
        return response.content


def extract_outputs(payload: dict[str, Any], only_node: str | None = None) -> list[dict[str, str]]:
    outputs = (payload or {}).get("outputs") or {}
    files: list[dict[str, str]] = []
    for node_id, node_outputs in outputs.items():
        if only_node and str(node_id) != str(only_node):
            continue
        if not isinstance(node_outputs, dict):
            continue
        for kind in ("gifs", "videos", "audio", "images"):
            for item in node_outputs.get(kind) or []:
                if not isinstance(item, dict) or not item.get("filename"):
                    continue
                files.append({
                    "filename": item["filename"],
                    "subfolder": item.get("subfolder") or "",
                    "kind": item.get("type") or kind,
                    "node": str(node_id),
                })
    return files


class PromptWatcher:
    """WS 进度 + 轮询历史。WS 不可用时自动退化为轮询，不影响任务完成。"""

    def __init__(self, prompt_id: str, progress: Callable[[dict[str, Any]], None] | None = None):
        self.prompt_id = prompt_id
        self.progress = progress or (lambda _: None)
        self.messages: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._listen, name=f"ws-{self.prompt_id[:8]}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _listen(self) -> None:
        try:
            from websockets.sync.client import connect  # type: ignore
        except Exception:  # noqa: BLE001
            return
        try:
            with connect(_ws_url(f"/ws?clientId={_client_id}"), open_timeout=8,
                         max_size=None) as ws:
                while not self._stop.is_set():
                    try:
                        raw = ws.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    except Exception:  # noqa: BLE001
                        break
                    if isinstance(raw, bytes):
                        continue
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if self._matches(message):
                        self.messages.append(message)
                        self.progress(message)
        except Exception:  # noqa: BLE001
            return

    def _matches(self, message: dict[str, Any]) -> bool:
        data = message.get("data") or {}
        if message.get("type") == "progress":
            return data.get("prompt_id") in {None, self.prompt_id}
        return data.get("prompt_id") == self.prompt_id


def wait(prompt_id: str, progress: Callable[[dict[str, Any]], None] | None = None,
         cancel: threading.Event | None = None, timeout: float = 3600) -> dict[str, Any]:
    watcher = PromptWatcher(prompt_id, progress)
    watcher.start()
    started = time.time()
    last_state = 0.0
    missing = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                cancel_prompt(prompt_id)
                raise ComfyError("任务已取消。")
            entry = history(prompt_id)
            status = (entry.get("status") or {})
            if entry and (status.get("completed") or status.get("status_str") in {"success", "error"}):
                if status.get("status_str") == "error":
                    detail = json.dumps(status.get("messages") or [], ensure_ascii=False)[:1500]
                    raise ComfyError(f"ComfyUI 执行失败：{detail}")
                return entry
            now = time.time()
            if now - started > timeout:
                raise ComfyError("等待 ComfyUI 超时。")
            if now - last_state > 5:
                last_state = now
                state = queue_state()
                position = next((index for index, item in enumerate(state["pending"])
                                 if str(item[1]) == prompt_id), None)
                running = any(str(item[1]) == prompt_id for item in state["running"])
                if progress:
                    progress({"type": "queue", "data": {
                        "prompt_id": prompt_id, "queue_position": position,
                        "queued": position is not None,
                        "running": running,
                    }})
                if not running and not position and not entry:
                    # ComfyUI 重启或队列被清空时 history 也不会有这条任务，
                    # 连续 30 秒找不到就判定丢失，避免一直等到 1 小时超时。
                    missing += 1
                    if missing >= 6:
                        raise ComfyError("ComfyUI 中找不到该任务（可能被重启或队列被清空），请重新生成。")
                else:
                    missing = 0
            time.sleep(1.0)
    finally:
        watcher.stop()
