"""数字人 · 确定性文字/logo 叠加兜底（P3：ffmpeg drawtext + overlay）。

契约见 `docs/数字人工作台-实现契约.md` §7：

- 不依赖 Pillow，只用 ffmpeg（drawtext + overlay）；
- `design.graphics_mode == "overlay"` 时由 job 在本段视频落盘前调用，
  本模块按 `row["beats"]` 叠加文字卡片 / logo；
- 时间轴：`beat["trigger"]` 在段台词里的字符位置 ÷ `chars_per_second` = 起点，
  持续到下一 beat 起点（没有下一个就 +2.5s），再夹到 `[0.2, seconds-0.2]`；
- 位置：`beat["anchor"]` → 安全区坐标，字号按视频高度自适应；多行用 textfile 换行；
- 样式：`design.style_system` 的 accent_color / secondary_color / typography / material；
- logo：`design.brand.logo_refs` 里第一张可读图片叠在右上角安全区
  （宽度不超过画面宽 18%，透明度 0.9）；
- 任何一步失败都返回 False，调用方保留原视频。

Windows 中文环境的两个关键写法（都实测过）：

1. filtergraph 里的路径必须是 `'C\\:/Windows/Fonts/msyh.ttc'`：单引号包裹 + 正斜杠 +
   转义冒号。直接写 `C:/...` 会被 ffmpeg 当成 option 分隔符，报
   "No option name near ..."；
2. 文字一律写 UTF-8 临时 `textfile=` 并加 `expansion=none`，中文 / 逗号 / 百分号
   都不用进 filtergraph 转义。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_CPS = 5.0            # 契约 §1.1：项目默认 5 字/秒
DEFAULT_SECONDS = 6.0
MAX_BEAT_SECONDS = 2.5       # 契约 §7：没有下一个 beat 时默认持续 2.5s
MIN_BEAT_SECONDS = 0.2       # 单个卡片的最短可见时长
MIN_EDGE_SECONDS = 0.2       # 时间窗夹在 [0.2, seconds-0.2]
NO_TRIGGER_GAP = 2.2         # 无 trigger 的 beat 用 index*2.2 当起点
MAX_LINES = 4                # 一张卡片最多几行（主行 + 辅行）
LOGO_WIDTH_RATIO = 0.18      # logo 宽度 ≤ 画面宽 18%
LOGO_HEIGHT_RATIO = 0.16     # logo 高度 ≤ 画面高 16%（防止超高图变成大色块）
LOGO_ALPHA = 0.9

FONT_CANDIDATES = (
    # 契约指定：优先微软雅黑，缺失回落黑体；其余为额外兜底
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simsun.ttc",
)

DEFAULT_SAFE_AREA = {"top": 0.08, "bottom": 0.12, "left": 0.06, "right": 0.06}
DEFAULT_ACCENT = "#FFB454"
DEFAULT_SECONDARY = "#8AB4F8"

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

ENCODE_ARGS = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart"]

_SLASH_SPLIT = re.compile(r"\n+|\s*\|\s*|\s*｜\s*|\s+/\s+|\s*·\s*")
_TRIGGER_STRIP = " \t\r\n，。！？!?；;：:、,.…·"


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def available() -> bool:
    """ffmpeg 是否可用（可用才有确定性叠加）。"""
    return _binary("ffmpeg") is not None


def compose_segment(project: dict, row: dict, source_video: Path, out_video: Path,
                    workdir: Path) -> bool:
    """给单段视频叠加确定性文字/logo；无 beats/无 ffmpeg/失败返回 False（调用方保留原视频）。

    - `project`：数字人 project.json（读 design.style_system / safe_area / brand）；
    - `row`：段（读 text / beats / seconds）；
    - `source_video`：H3 生成的段视频；`out_video`：叠加后的输出；
    - `workdir`：可写临时目录（调用方传 `takes/seg_XXXX/_overlay`）。
    """
    try:
        return _compose(project, row, Path(source_video), Path(out_video), Path(workdir))
    except Exception:  # noqa: BLE001 - 叠加是兜底，任何异常都不能影响出片
        return False


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _compose(project: dict, row: dict, source: Path, out: Path, workdir: Path) -> bool:
    if not isinstance(project, dict) or not isinstance(row, dict):
        return False

    design = project.get("design") if isinstance(project.get("design"), dict) else {}
    graphics_mode = str(design.get("graphics_mode") or "").strip().lower()
    # 契约 §7：仅 overlay 模式生效。job 已先判一次，这里兜底，避免生成式项目被重复叠加。
    if graphics_mode and graphics_mode != "overlay":
        return False

    beats = [beat for beat in (row.get("beats") or []) if isinstance(beat, dict)]
    if not beats:
        return False

    ffmpeg = _binary("ffmpeg")
    if not ffmpeg:
        return False
    if not source.is_file():
        return False

    info = _probe(source)
    if not info:
        return False
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if width < 16 or height < 16:
        return False

    seconds = _segment_seconds(row, float(info.get("duration") or 0.0))
    cps = _cps(project)
    windows = _beat_windows(row, project, seconds)
    if not windows:
        return False

    style = design.get("style_system") if isinstance(design.get("style_system"), dict) else {}
    palette = {
        "accent": _color(style.get("accent_color"), _color(DEFAULT_ACCENT, "0xFFB454")),
        "secondary": _color(style.get("secondary_color"),
                            _color(DEFAULT_SECONDARY, "0x8AB4F8")),
    }
    material = str(style.get("material") or "")
    typography = str(style.get("typography") or "")
    safe = _safe_area(design.get("safe_area"))
    font_size = _font_size(height, typography)
    sub_size = max(10, int(round(font_size * 0.62)))
    line_chars = _max_chars(width, safe, font_size)
    font = _font_path()

    logo = _logo_path(project, source, out, workdir)
    logo_size = _logo_size(_probe(logo) if logo else None, width, height)
    if not logo_size:
        logo = None
    if font is None and logo is None:
        return False
    # 右上角有 logo 时，顶部/左上卡片要让出它的宽度，避免文字压到 logo 上
    top_reserve = logo_size["width"] + int(round(font_size * 0.5)) if logo_size else 0
    top_chars = _max_chars(width, safe, font_size, reserve=top_reserve)
    main_style = _card_options(material, font_size)
    pad = int(main_style.get("boxborderw") or 0)

    workdir.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = workdir / f"overlay_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    temp.mkdir(parents=True, exist_ok=True)
    staged = out.with_name(f"{out.stem}.overlay{uuid.uuid4().hex[:6]}{out.suffix or '.mp4'}")
    # 上一次失败留下的半成品不要污染调用方
    if out.exists() and out != source:
        try:
            out.unlink()
        except OSError:
            pass

    try:
        text = str(row.get("text") or "")
        filters: list[str] = []
        for index, (beat, start, end) in enumerate(windows):
            if end - start < 0.1 or font is None:
                continue
            lines_main, lines_sub = _beat_lines(
                beat, text, cps, start, end,
                top_chars if _is_top_anchor(beat.get("anchor")) else line_chars)
            if not lines_main:
                continue
            x_main, y_main, y_sub = _place(
                beat.get("anchor"), width, height, safe, font_size, sub_size,
                len(lines_main), len(lines_sub), pad=pad)

            main_file = temp / f"beat_{index:02d}_main.txt"
            # 必须写裸 \n：Windows 的文本模式会把 \n 写成 \r\n，drawtext 会把每行多撑一倍行距
            main_file.write_bytes("\n".join(lines_main).encode("utf-8"))
            filters.append(_drawtext(
                textfile=main_file, fontfile=font, size=font_size,
                color=palette["accent"], x=x_main, y=y_main,
                spacing=int(round(font_size * 0.18)) if len(lines_main) > 1 else 0,
                style=main_style, start=start, end=end))

            if lines_sub:
                sub_file = temp / f"beat_{index:02d}_sub.txt"
                sub_file.write_bytes("\n".join(lines_sub).encode("utf-8"))
                filters.append(_drawtext(
                    textfile=sub_file, fontfile=font, size=sub_size,
                    color=palette["secondary"], x=x_main, y=y_sub,
                    spacing=int(round(sub_size * 0.22)) if len(lines_sub) > 1 else 0,
                    # 辅行不加底框（避免两个宽度不同的框叠在一起），用描边保证可读
                    style={"borderw": max(1, sub_size // 14), "bordercolor": "black@0.75"},
                    start=start, end=end))

        if not filters and logo is None:
            return False

        graph = _graph(filters, logo, logo_size, width, height, safe)
        input_args = ["-i", str(source)]
        if logo is not None:
            input_args += ["-i", str(logo)]

        if not _encode(ffmpeg, input_args, graph, staged):
            return False
        if not staged.is_file() or staged.stat().st_size < 1024:
            return False

        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and out != source:
            try:
                out.unlink()
            except OSError:
                pass
        try:
            os.replace(staged, out)
        except OSError:
            if out != source:
                try:
                    out.unlink()
                except OSError:
                    pass
            shutil.copyfile(staged, out)
            staged.unlink(missing_ok=True)
        return out.is_file()
    finally:
        shutil.rmtree(temp, ignore_errors=True)
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 时间轴
# ---------------------------------------------------------------------------
def _segment_seconds(row: dict, video_seconds: float) -> float:
    """本段时长：优先 row.seconds，且不超过素材实际时长。"""
    try:
        seconds = float(row.get("seconds") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        seconds = video_seconds if video_seconds > 0 else DEFAULT_SECONDS
    if video_seconds > 0.05:
        seconds = min(seconds, video_seconds)
    return max(seconds, 0.4)


def _cps(project: dict) -> float:
    try:
        cps = float(project.get("chars_per_second") or DEFAULT_CPS)
    except (TypeError, ValueError):
        cps = DEFAULT_CPS
    return max(0.5, cps)


def _beat_windows(row: dict, project: dict, seconds: float) -> list[tuple[dict, float, float]]:
    """[(beat, start, end)]：trigger 字符位置 ÷ 语速 → 起点，下一 beat 起点 → 终点。"""
    beats = [beat for beat in (row.get("beats") or []) if isinstance(beat, dict)]
    if not beats:
        return []
    cps = _cps(project)
    text = str(row.get("text") or "")

    starts: list[float] = []
    for index, beat in enumerate(beats):
        trigger = str(beat.get("trigger") or "").strip()
        position = text.find(trigger) if trigger else -1
        starts.append(position / cps if position >= 0 else index * NO_TRIGGER_GAP)

    low = MIN_EDGE_SECONDS
    high = max(low + 0.1, seconds - MIN_EDGE_SECONDS)
    windows: list[tuple[dict, float, float]] = []
    for index, beat in enumerate(beats):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else start + MAX_BEAT_SECONDS
        if end <= start:
            end = start + MAX_BEAT_SECONDS
        start = min(max(start, low), high)
        end = min(max(end, start + MIN_BEAT_SECONDS),
                  max(start + MIN_BEAT_SECONDS, high))
        windows.append((beat, round(start, 3), round(end, 3)))
    return windows


# ---------------------------------------------------------------------------
# 文案 → 行
# ---------------------------------------------------------------------------
def _beat_lines(beat: dict, text: str, cps: float, start: float, end: float,
                line_chars: int) -> tuple[list[str], list[str]]:
    """主行（accent）+ 辅行（secondary）。

    优先 `visible_copy`（可用 `/`、`|`、`·`、换行分条），其次 `trigger`，
    最后回落到该时间窗内的台词切片——保证"有 beat 就一定有字可叠"。
    """
    copy = _clean(beat.get("visible_copy"))
    if not copy:
        copy = _clean(beat.get("trigger"))
    if not copy:
        copy = _text_slice(text, cps, start, end)

    lines: list[list[str]] = []
    total = 0
    for piece in _SLASH_SPLIT.split(copy):
        piece = _clean(piece)
        if not piece:
            continue
        wrapped = _wrap(piece, line_chars)
        if not wrapped:
            continue
        lines.append(wrapped)
        total += len(wrapped)
        if total >= MAX_LINES:
            break
    if not lines:
        return [], []
    # 第一块（最多 2 行）是主卡片（accent 色 + 底框），其余块是辅行（secondary 色 + 描边）
    main_lines = lines[0][:2]
    sub_lines = [line for block in lines[1:] for line in block][:MAX_LINES - len(main_lines)]
    return main_lines, sub_lines


def _text_slice(text: str, cps: float, start: float, end: float) -> str:
    """按 beat 时间窗从台词里切一段当兜底文案（trigger 缺失时的最后一级回落）。"""
    source = _clean(text)
    if not source:
        return ""
    begin = max(0, int(round(start * max(cps, 0.5))))
    finish = min(len(source), max(begin + 2, int(round(end * max(cps, 0.5))) + 2))
    return _clean(source[begin:finish])[:24]


def _clean(value: Any) -> str:
    return str(value or "").strip().strip(_TRIGGER_STRIP).strip()


def _wrap(line: str, limit: int) -> list[str]:
    """按字符数换行；优先在标点处断，避免中文被拦腰切断。"""
    line = line.strip()
    if not line:
        return []
    if limit <= 0 or len(line) <= limit:
        return [line]
    pieces: list[str] = []
    rest = line
    punctuations = ("，", "。", "、", "；", "：", "！", "？", ",", ";", " ", "    ")
    while len(rest) > limit:
        head = rest[:limit]
        cut = max((head.rfind(mark) for mark in punctuations), default=-1)
        if cut >= max(2, limit // 2):
            pieces.append(rest[:cut + 1].strip())
            rest = rest[cut + 1:].lstrip()
        else:
            pieces.append(head.strip())
            rest = rest[limit:].lstrip()
    if rest:
        pieces.append(rest.strip())
    return [piece for piece in pieces if piece]


# ---------------------------------------------------------------------------
# 版式 / 样式
# ---------------------------------------------------------------------------
def _safe_area(value: Any) -> dict[str, float]:
    source = value if isinstance(value, dict) else {}
    return {key: _fraction(source.get(key), DEFAULT_SAFE_AREA[key])
            for key in ("top", "bottom", "left", "right")}


def _fraction(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(0.35, number))


def _font_size(height: int, typography: str) -> int:
    """字号按画面高度自适应（契约：约 height/22），typography 里的字重/标题档做微调。"""
    scale = 1.0
    hint = str(typography or "").lower()
    if any(word in hint for word in ("粗", "黑体", "bold", "black", "heavy")):
        scale *= 1.08
    if any(word in hint for word in ("细", "light", "thin")):
        scale *= 0.92
    if any(word in hint for word in ("标题", "大号", "headline", "display", "big")):
        scale *= 1.2
    size = int(round(height / 22.0 * scale))
    return max(12, min(120, size))


def _max_chars(width: int, safe: dict[str, float], font_size: int,
               reserve: int = 0) -> int:
    available = max(40.0, width * (1.0 - safe["left"] - safe["right"]) - max(0, reserve))
    return max(4, min(24, int(available / max(font_size, 1) / 1.02)))


def _is_top_anchor(anchor: Any) -> bool:
    """顶部（含 anchor 空/未知，_place 默认落到 top_left）要给右上角 logo 让位。"""
    key = str(anchor or "").strip().lower()
    return key in ("", "top", "top_left", "upper_left", "upper")


def _place(anchor: Any, width: int, height: int, safe: dict[str, float],
           font_size: int, sub_size: int, main_count: int, sub_count: int,
           pad: int = 0) -> tuple[str, int, int]:
    """anchor → (x, 主行 y, 辅行 y)；x 用 drawtext 表达式，y 是像素整数。"""
    left = int(round(width * safe["left"]))
    right = int(round(width * safe["right"]))
    top = int(round(height * safe["top"]))
    bottom = int(round(height * safe["bottom"]))
    pad = max(0, int(pad))
    line_height = 1.18
    main_height = int(round(main_count * font_size * line_height))
    gap = int(round(font_size * 0.28)) if sub_count else 0
    sub_height = int(round(sub_count * sub_size * 1.3)) if sub_count else 0
    block_height = main_height + gap + sub_height

    key = str(anchor or "").strip().lower()
    x: Any = left + pad
    if key in ("right_side", "right", "screen_right", "hud"):
        x = f"w-text_w-{right + pad}"
        y = int(round(height * 0.40))
    elif key in ("world_space", "surface", "world", "screen"):
        x = f"w-text_w-{right + pad}"
        y = int(round(height * 0.54))
    elif key in ("lower_third", "lower", "bottom"):
        y = height - bottom - block_height - pad
    elif key in ("beside_speaker", "beside", "side", "speaker"):
        y = int(round(height * 0.34))
    else:  # top_left / 未知 anchor
        y = top + pad
    y = max(top + pad, min(y, max(top + pad, height - bottom - block_height - pad)))
    y_sub = y + main_height + gap
    return str(x), int(y), int(y_sub)


def _card_options(material: str, font_size: int) -> dict[str, Any]:
    """material → 底框/描边（契约：box=1:boxcolor=... 或描边）。"""
    key = str(material or "").lower()
    presets = (
        (("毛玻璃", "玻璃", "glass", "frost", "磨砂", "半透明"), {"box": 1, "boxcolor": "black@0.30"}),
        (("白底", "浅色", "light", "white"), {"box": 1, "boxcolor": "white@0.72"}),
        (("纯色", "实底", "色块", "卡片", "solid", "card", "paper", "纸质"), {"box": 1, "boxcolor": "black@0.62"}),
        (("描边", "线框", "霓虹", "outline", "stroke", "neon", "line"), {"border": 1}),
    )
    chosen: dict[str, Any] = {"box": 1, "boxcolor": "black@0.45"}
    for words, options in presets:
        if any(word in key for word in words):
            chosen = options
            break
    if chosen.get("border"):
        return {"borderw": max(2, font_size // 18), "bordercolor": "black@0.85"}
    return {"box": 1, "boxcolor": chosen["boxcolor"],
            "boxborderw": max(4, int(round(font_size * 0.35)))}


def _color(value: Any, default: str) -> str:
    """#RRGGBB / RRGGBB / #RGB / 0xRRGGBB / 颜色名 → ffmpeg 认的颜色。"""
    raw = str(value or "").strip()
    if not raw:
        return default
    lowered = raw.lower()
    if re.fullmatch(r"#[0-9a-f]{6}", lowered):
        return "0x" + lowered[1:].upper()
    if re.fullmatch(r"#[0-9a-f]{3}", lowered):
        return "0x" + "".join(char * 2 for char in lowered[1:]).upper()
    if re.fullmatch(r"0x[0-9a-f]{6,8}", lowered):
        return lowered
    if re.fullmatch(r"[0-9a-f]{6}", lowered):
        return "0x" + lowered.upper()
    if re.fullmatch(r"[0-9a-f]{3}", lowered):
        return "0x" + "".join(char * 2 for char in lowered).upper()
    if re.fullmatch(r"[a-z][a-z0-9_]*", lowered) or lowered.startswith("0x"):
        return lowered
    return default


# ---------------------------------------------------------------------------
# ffmpeg 调用 / 滤镜
# ---------------------------------------------------------------------------
def _drawtext(*, textfile: Path, fontfile: str, size: int, color: str, x: Any, y: int,
              spacing: int, style: dict[str, Any], start: float, end: float) -> str:
    parts = [
        f"fontfile={_filter_path(fontfile)}",
        f"textfile={_filter_path(textfile)}",
        "expansion=none",           # 关掉 %{} 展开，中文/百分号原样输出
        f"fontsize={int(size)}",
        f"fontcolor={color}",
        f"x={x}",
        f"y={int(y)}",
    ]
    if spacing > 0:
        parts.append(f"line_spacing={int(spacing)}")
    for key in ("box", "boxcolor", "boxborderw", "borderw", "bordercolor"):
        if key in style:
            parts.append(f"{key}={style[key]}")
    parts.append(f"enable='between(t,{start:.3f},{end:.3f})'")
    return "drawtext=" + ":".join(str(part) for part in parts)


def _filter_path(value: Any) -> str:
    """filtergraph 里的路径：单引号包裹 + 正斜杠 + 转义冒号（Windows 盘符）。"""
    text = str(value).replace("\\", "/").replace(":", "\\:")
    text = text.replace("'", "\\'")
    return "'" + text + "'"


def _logo_size(info: dict[str, Any] | None, width: int, height: int) -> dict[str, int] | None:
    """logo 目标尺寸：宽 ≤ 画面 18%、高 ≤ 画面 16%，小图不放大。"""
    if not info:
        return None
    logo_width = int(info.get("width") or 0)
    logo_height = int(info.get("height") or 0)
    if logo_width < 4 or logo_height < 4:
        return None
    max_width = max(8, int(round(width * LOGO_WIDTH_RATIO)))
    max_height = max(8, int(round(height * LOGO_HEIGHT_RATIO)))
    scale = min(1.0, max_width / logo_width, max_height / logo_height)
    target_width = max(2, int(round(logo_width * scale)))
    target_height = max(2, int(round(logo_height * scale)))
    if target_width % 2:
        target_width += 1
    if target_height % 2:
        target_height += 1
    return {"width": target_width, "height": target_height}


def _graph(filters: list[str], logo: Path | None, logo_size: dict[str, int] | None,
           width: int, height: int, safe: dict[str, float]) -> str:
    chain = ",".join(filters) if filters else "null"
    if logo is None or not logo_size:
        return f"[0:v]{chain}[outv]"

    target_width = int(logo_size["width"])
    target_height = int(logo_size["height"])
    right = int(round(width * safe["right"]))
    top = int(round(height * safe["top"]))
    x = max(0, width - target_width - right)
    y = top
    logo_chain = (f"[1:v]scale={target_width}:{target_height},format=rgba,"
                  f"colorchannelmixer=aa={LOGO_ALPHA:.2f}[logo]")
    return (f"[0:v]{chain}[base];{logo_chain};"
            f"[base][logo]overlay={x}:{y}:format=auto[outv]")


def _encode(ffmpeg: str, input_args: list[str], graph: str, target: Path) -> bool:
    """一次 ffmpeg 调用：drawtext 链 + logo overlay，视频重编码、音轨优先直拷。"""
    base = [ffmpeg, "-y", "-v", "error"] + input_args + [
        "-filter_complex", graph, "-map", "[outv]", "-map", "0:a:0?", "-map_metadata", "-1",
    ]
    for audio_args in (["-c:a", "copy"], ["-c:a", "aac", "-b:a", "192k"]):
        result = _run(base + ENCODE_ARGS + audio_args + [str(target)], timeout=900)
        if result.returncode == 0:
            return True
        # 音轨直拷失败（例如容器/编码不兼容）才退到重编码；滤镜错误没必要重试
        if "Could not find tag" not in (result.stderr or "") \
                and "Could not write header" not in (result.stderr or ""):
            return False
    return False


def _binary(name: str) -> str | None:
    """找 ffmpeg / ffprobe：环境变量覆盖 → PATH。"""
    override = (os.environ.get(f"H3_OVERLAY_{name.upper()}")
                or os.environ.get(f"H3_{name.upper()}"))
    candidates: list[str] = [override] if override else []
    found = shutil.which(name)
    if found:
        candidates.append(found)
    for candidate in candidates:
        try:
            if candidate and Path(candidate).is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def _font_path() -> str | None:
    """中文字体：契约指定优先 msyh.ttc，缺失回落 simhei.ttf。"""
    override = os.environ.get("H3_OVERLAY_FONT")
    candidates = ([override] if override else []) + list(FONT_CANDIDATES)
    for candidate in candidates:
        try:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate))
        except OSError:
            continue
    return None


def _run(args: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(args, **kwargs)
    except (OSError, subprocess.SubprocessError) as error:  # 超时 / 找不到 exe
        return subprocess.CompletedProcess(args, 1, "", str(error))


def _probe(path: Path | None) -> dict[str, Any] | None:
    """取视频/图片的宽高与时长；ffprobe 缺失时用 ffmpeg -i 的输出兜底。"""
    if not path or not Path(path).is_file():
        return None
    ffprobe = _binary("ffprobe")
    if ffprobe:
        result = _run([ffprobe, "-v", "error", "-select_streams", "v:0",
                       "-show_entries", "stream=width,height:format=duration",
                       "-of", "json", str(path)], timeout=60)
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                data = {}
            streams = data.get("streams") or [{}]
            width = int(streams[0].get("width") or 0)
            height = int(streams[0].get("height") or 0)
            duration = 0.0
            try:
                duration = float((data.get("format") or {}).get("duration") or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            if width > 0 and height > 0:
                return {"width": width, "height": height, "duration": duration}

    ffmpeg = _binary("ffmpeg")
    if not ffmpeg:
        return None
    result = _run([ffmpeg, "-hide_banner", "-i", str(path)], timeout=60)
    output = result.stderr or ""
    size = re.search(r"(\d{2,5})x(\d{2,5})", output)
    if not size:
        return None
    duration = 0.0
    stamp = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if stamp:
        duration = int(stamp.group(1)) * 3600 + int(stamp.group(2)) * 60 + float(stamp.group(3))
    return {"width": int(size.group(1)), "height": int(size.group(2)), "duration": duration}


# ---------------------------------------------------------------------------
# logo 定位
# ---------------------------------------------------------------------------
def _logo_path(project: dict, source: Path, out: Path, workdir: Path) -> Path | None:
    """design.brand.logo_refs 里第一张可读的 logo（filename 或绝对路径）。"""
    design = project.get("design") if isinstance(project.get("design"), dict) else {}
    brand = design.get("brand") if isinstance(design.get("brand"), dict) else {}
    refs = brand.get("logo_refs") or []
    if not isinstance(refs, list):
        return None
    roots = _search_roots(source) + _search_roots(out) + _search_roots(workdir)
    for entry in refs:
        name = ""
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            name = str(entry.get("filename") or entry.get("file") or entry.get("path") or "").strip()
        if not name:
            continue
        candidate = Path(name)
        if candidate.is_absolute():
            if candidate.is_file() and _is_image(candidate):
                return candidate
            continue
        for root in roots:
            for path in (root / "refs" / name, root / name):
                if path.is_file() and _is_image(path):
                    return path
    return None


def _search_roots(start: Path, depth: int = 4) -> list[Path]:
    roots: list[Path] = []
    current = start if start.is_dir() else start.parent
    for _ in range(depth):
        roots.append(current)
        if current.parent == current:
            break
        current = current.parent
    return roots


def _is_image(path: Path) -> bool:
    try:
        return path.suffix.lower() in IMAGE_SUFFIXES and path.stat().st_size > 0
    except OSError:
        return False
