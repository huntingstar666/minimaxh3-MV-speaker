"""可选 ASR：faster-whisper，用于词级时间戳与分句切分。

- 模型按名字缓存，避免每次分析都重新加载；
- 显存/驱动不可用时自动降级到 CPU int8；
- 默认走国内可用的 HF 镜像（可用环境变量 HF_ENDPOINT 覆盖）；
- 音频以 numpy 数组传入，绕开 av 解码器版本差异。
"""
from __future__ import annotations

import os
import threading
from typing import Any

import numpy as np

from .settings import DATA_DIR

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 模型缓存默认放项目内（data/hf-cache），便于整个文件夹拷走后仍可用；
# 若外部已设置 HF_HOME 则以外部为准。
os.environ.setdefault("HF_HOME", str(DATA_DIR / "hf-cache"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# 镜像不支持 hf-xet 的 CAS 协议，必须回退到普通 HTTP 下载
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

MODELS = [
    {"id": "tiny", "label": "tiny · 最快，仅试跑"},
    {"id": "base", "label": "base · 快，准确率一般"},
    {"id": "small", "label": "small · 推荐，中文够用"},
    {"id": "medium", "label": "medium · 更准，更慢"},
    {"id": "large-v3", "label": "large-v3 · 最准，最慢"},
]

_model_cache: dict[str, Any] = {}
_model_lock = threading.Lock()


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _evict_models(keep: str) -> None:
    """只保留最近用过的模型：切换 tiny→large-v3 时旧的会一直占显存。"""
    for name in [key for key in _model_cache if key != keep]:
        _model_cache.pop(name, None)
    if len(_model_cache) <= 1:
        import gc  # noqa: PLC0415
        gc.collect()


def _load(model_name: str):
    with _model_lock:
        cached = _model_cache.get(model_name)
        if cached is not None:
            return cached
        from faster_whisper import WhisperModel  # type: ignore

        errors: list[str] = []
        for device, compute in (("cuda", "float16"), ("cuda", "int8_float16"), ("cpu", "int8")):
            try:
                model = WhisperModel(model_name, device=device, compute_type=compute)
                _evict_models(model_name)
                _model_cache[model_name] = (model, device, compute)
                return _model_cache[model_name]
            except Exception as error:  # noqa: BLE001
                errors.append(f"{device}/{compute}: {error}")
        raise RuntimeError("Whisper 模型加载失败：" + "；".join(errors)[:400])


def _resample(samples: np.ndarray, sr: int, target: int = 16000) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim > 1:  # 多声道先混成单声道
        data = data.mean(axis=1)
    if sr == target:
        return data
    duration = len(data) / float(sr)
    count = int(round(duration * target))
    if count <= 0:
        return np.zeros(0, dtype=np.float32)
    positions = np.linspace(0, len(data) - 1, count)
    return np.interp(positions, np.arange(len(data)), data).astype(np.float32)


def transcribe(samples: np.ndarray, sr: int, model_name: str = "", language: str = "",
               initial_prompt: str = "") -> dict[str, Any]:
    if not available():
        raise RuntimeError("未安装 faster-whisper，请执行 pip install faster-whisper。")
    name = (model_name or "small").strip()
    model, device, compute = _load(name)
    audio_16k = _resample(np.asarray(samples, dtype=np.float32), sr)

    def run(active):
        return active.transcribe(
            audio_16k, language=language or None, initial_prompt=initial_prompt or None,
            word_timestamps=True, vad_filter=True, beam_size=5,
            condition_on_previous_text=False,
        )

    try:
        segments, info = run(model)
        rows_iter = list(segments)
    except Exception as error:  # noqa: BLE001
        # 显存不足或 cuDNN 缺失时，退回 CPU 再试一次
        if device != "cpu":
            model, device, compute = _load_cpu(name)
            segments, info = run(model)
            rows_iter = list(segments)
        else:
            raise RuntimeError(f"语音识别失败：{error}") from error

    rows: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    previous_text = ""
    repeat_count = 0
    for segment in rows_iter:
        text = str(segment.text or "").strip()
        suspicious = False
        if text and text == previous_text:
            repeat_count += 1
            suspicious = repeat_count >= 2
        else:
            repeat_count = 0
        if float(getattr(segment, "no_speech_prob", 0) or 0) > 0.6:
            suspicious = True
        if float(getattr(segment, "avg_logprob", 0) or 0) < -1.2:
            suspicious = True
        previous_text = text
        rows.append({
            "start": round(float(segment.start), 3), "end": round(float(segment.end), 3),
            "text": text, "suspicious": suspicious,
        })
        for word in getattr(segment, "words", []) or []:
            words.append({
                "start": round(float(word.start), 3), "end": round(float(word.end), 3),
                "word": str(word.word or "").strip(),
                "probability": round(float(getattr(word, "probability", 1.0)), 3),
                "suspicious": suspicious,
            })
    return {
        "engine": "faster-whisper",
        "model": name,
        "device": device,
        "compute_type": compute,
        "language": getattr(info, "language", "") or "",
        "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
        "duration": round(float(getattr(info, "duration", 0) or 0), 3),
        "text": "".join(row["text"] for row in rows if row["text"]),
        "segments": rows,
        "words": words,
        "suspicious_segments": [row for row in rows if row["suspicious"]],
    }


def _load_cpu(model_name: str):
    """CPU 降级只对当次调用生效，不写回缓存。

    以前会把 CPU 模型覆盖进缓存，一次 CUDA 失败后该模型在该进程里永久走 CPU。
    """
    with _model_lock:
        from faster_whisper import WhisperModel  # type: ignore

        return WhisperModel(model_name, device="cpu", compute_type="int8"), "cpu", "int8"
