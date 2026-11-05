"""LLM 适配层：OpenAI 兼容云端 + 本地 Ollama，统一走 /chat/completions。"""
from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx


def normalize_base(url: str) -> str:
    value = str(url or "").strip().rstrip("/")
    if not value:
        raise ValueError("LLM 地址为空。")
    if "://" not in value:
        value = "http://" + value
    # 允许用户直接粘贴完整 endpoint（…/v1/chat/completions）：
    # 以前会原样保留再拼一次 /chat/completions，变成双路径必然 404。
    if value.endswith("/chat/completions"):
        value = value[: -len("/chat/completions")].rstrip("/")
    if not value.endswith("/v1") and "/v1/" not in value:
        if "11434" in value and value.endswith("/api"):
            value = value[:-4]
        value += "/v1"
    return value


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def build_messages(messages: list[dict], images: list[Path] | None, vision: bool) -> list[dict]:
    if not images or not vision:
        return messages
    prepared = [dict(message) for message in messages]
    for index in range(len(prepared) - 1, -1, -1):
        if prepared[index].get("role") == "user":
            content = prepared[index].get("content")
            parts = [{"type": "text", "text": content if isinstance(content, str)
                      else json.dumps(content, ensure_ascii=False)}]
            for path in images[:9]:
                if Path(path).is_file():
                    parts.append({"type": "image_url", "image_url": {"url": _image_data_url(Path(path))}})
            prepared[index]["content"] = parts
            break
    return prepared


def chat(config: dict[str, Any], messages: list[dict], images: list[Path] | None = None) -> str:
    base = normalize_base(str(config.get("base_url") or ""))
    model = str(config.get("model") or "").strip()
    if not model:
        raise ValueError("未配置 LLM 模型。")
    payload = {
        "model": model,
        "messages": build_messages(messages, images, bool(config.get("vision", True))),
        "temperature": float(config.get("temperature", 0.4)),
        "max_tokens": int(config.get("max_tokens", 4096)),
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    key = str(config.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = base + "/chat/completions"
    timeout = float(config.get("timeout", 180))
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=payload, headers=headers)
            if response.status_code == 404:
                raise RuntimeError(f"LLM 接口路径不存在（{url}），请检查 Base URL。")
            response.raise_for_status()
            data = response.json()
            message = (data.get("choices") or [{}])[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            text = str(content or "").strip()
            if not text:
                raise RuntimeError("LLM 返回为空。")
            return strip_fences(text)
        except Exception as error:  # noqa: BLE001
            last_error = error
            if attempt == 0:
                time.sleep(1.2)
    raise RuntimeError(f"LLM 调用失败：{last_error}")


def strip_fences(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def test(config: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    reply = chat(config, [{"role": "user", "content": "只回复 ok"}])
    return {"ok": True, "reply": reply[:120], "elapsed": round(time.time() - started, 2),
            "base_url": normalize_base(str(config.get("base_url") or "")),
            "model": config.get("model")}
