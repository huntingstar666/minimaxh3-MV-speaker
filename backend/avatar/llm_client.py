"""数字人专用 LLM 调用。

三腔系统提示词本身约 1.3 万字符，且模型（尤其本地 9B）会把大量 token 花在推理上，
所以这里统一：
- max_tokens 至少 16384；
- timeout 至少 900 秒；
- 本地 Ollama（base_url 含 11434）额外带 options.num_ctx=32768 / num_predict，避免
  长系统提示词被默认上下文截断；端点不认这些字段时自动回退普通调用。
"""
from __future__ import annotations

import json
import threading
from typing import Any

import httpx

from .. import llm as base_llm

MIN_MAX_TOKENS = 16384
MIN_TIMEOUT = 900.0
OLLAMA_NUM_CTX = 32768


class CancelledError(Exception):
    """用户取消任务时中断等待中的 LLM 请求。"""


def prepare(config: dict[str, Any] | None) -> dict[str, Any]:
    value = dict(config or {})
    try:
        value["max_tokens"] = max(int(value.get("max_tokens") or 8192), MIN_MAX_TOKENS)
    except (TypeError, ValueError):
        value["max_tokens"] = MIN_MAX_TOKENS
    try:
        value["timeout"] = max(float(value.get("timeout") or 180), MIN_TIMEOUT)
    except (TypeError, ValueError):
        value["timeout"] = MIN_TIMEOUT
    return value


def chat(config: dict[str, Any] | None, messages: list[dict[str, Any]],
         images: list[Any] | None = None,
         cancel: threading.Event | None = None) -> str:
    """同步 LLM 调用；传 cancel 时在等待期间可被其他线程中断。

    httpx 的同步请求无法从外部 cancel，因此把请求放到 daemon 线程里等，
    主线程只轮询取消标记。取消后遗留的 daemon 线程会自行结束，不会阻塞退出。
    """
    if cancel is not None and cancel.is_set():
        raise CancelledError("任务已取消。")
    if cancel is None:
        return _chat_sync(config, messages, images)
    box: dict[str, Any] = {}
    thread = threading.Thread(target=_chat_worker, args=(box, config, messages, images),
                              name="avatar-llm", daemon=True)
    thread.start()
    while thread.is_alive():
        if cancel.wait(0.2):
            raise CancelledError("任务已取消。")
    if "error" in box:
        raise box["error"]
    return str(box.get("value") or "")


def _chat_worker(box: dict[str, Any], config: dict[str, Any] | None,
                 messages: list[dict[str, Any]], images: list[Any] | None) -> None:
    try:
        box["value"] = _chat_sync(config, messages, images)
    except BaseException as error:  # noqa: BLE001
        box["error"] = error


def _chat_sync(config: dict[str, Any] | None, messages: list[dict[str, Any]],
               images: list[Any] | None = None) -> str:
    value = prepare(config)
    if "11434" in str(value.get("base_url") or ""):
        try:
            return _ollama_chat(value, messages, images)
        except Exception:  # noqa: BLE001  端点不支持 options 时走标准调用
            pass
    return base_llm.chat(value, messages, images)


def _ollama_chat(config: dict[str, Any], messages: list[dict[str, Any]],
                 images: list[Any] | None) -> str:
    base = base_llm.normalize_base(str(config.get("base_url") or ""))
    model = str(config.get("model") or "").strip()
    if not model:
        raise ValueError("未配置 LLM 模型。")
    max_tokens = int(config.get("max_tokens") or MIN_MAX_TOKENS)
    payload: dict[str, Any] = {
        "model": model,
        "messages": base_llm.build_messages(messages, images,
                                            bool(config.get("vision", True))),
        "temperature": float(config.get("temperature", 0.4)),
        "max_tokens": max_tokens,
        "stream": False,
        "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": max_tokens},
    }
    headers = {"Content-Type": "application/json"}
    key = str(config.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = base + "/chat/completions"
    timeout = float(config.get("timeout") or MIN_TIMEOUT)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            payload.pop("options", None)
            response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    message = (data.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = str(content or "").strip()
    if not text:
        raise RuntimeError("LLM 返回为空。")
    return base_llm.strip_fences(text)


def parse_json(text: str) -> Any:
    """宽松解析：允许模型在 JSON 前后带一句解释或 ``` 围栏。"""
    value = base_llm.strip_fences(str(text or "").strip())
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = value.find(opener)
        end = value.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(value[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"无法从模型输出解析 JSON：{value[:200]}")
