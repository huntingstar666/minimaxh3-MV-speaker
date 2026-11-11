"""数字人叠加兜底（backend/avatar/overlay.py）测试。

用法：`python tests/avatar_overlay_test.py`

流程：ffmpeg 合成 3s / 320x240 / 带正弦音轨的 mp4 → 构造 project + row（2 个 beat、
中文可见文案）→ 调用 `compose_segment` 做确定性 drawtext + logo 叠加，断言：

1. 返回 True 且输出文件存在；
2. ffprobe 时长与输入相差 < 0.15s；
3. 输出仍有音轨；
4. beats 为空时返回 False 且不产出文件；
5. `available()` 与 PATH 里的 ffmpeg 一致；
6. `brand.logo_refs` 可读时 logo 叠加也成功，且临时 textfile 已清理。

没有 ffmpeg 时打印 SKIP 并退出 0。素材全部写系统临时目录，跑完自清理；
不依赖 ComfyUI / 网络 / FastAPI。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "backend" / "avatar" / "overlay.py"

# 按文件直接加载：overlay.py 只依赖标准库，测试不需要拉起 backend.avatar 的 FastAPI 应用
_spec = importlib.util.spec_from_file_location("h3_avatar_overlay", MODULE_PATH)
overlay = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(overlay)

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def _run(args: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, **kwargs)


# ---------------------------------------------------------------------------
# 临时目录：默认走系统 TEMP；受限沙箱里 mkdtemp 的 0700 目录可能丢掉继承权限，
# 此时回落到系统 TEMP 下的普通目录（仍是系统临时目录，跑完一起删）。
# ---------------------------------------------------------------------------
def _writable(path: Path) -> bool:
    probe = path / f".probe_{uuid.uuid4().hex[:6]}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _temp_root() -> Path:
    makers = (
        lambda: Path(tempfile.mkdtemp(prefix="h3dh-overlay-")),
        lambda: _plain_temp_dir(),
    )
    for maker in makers:
        try:
            candidate = maker()
        except OSError:
            continue
        if _writable(candidate):
            return candidate
        shutil.rmtree(candidate, ignore_errors=True)
    raise RuntimeError("系统临时目录不可写")


def _plain_temp_dir() -> Path:
    candidate = Path(tempfile.gettempdir()) / f"h3dh-overlay-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


# ---------------------------------------------------------------------------
# 素材合成 / 探测
# ---------------------------------------------------------------------------
def _make_source(ffmpeg: str, target: Path, seconds: float = 3.0) -> bool:
    """3 秒、320x240、24fps 的测试画面 + 正弦音轨（模拟 H3 段视频的 AV 结构）。"""
    result = _run([
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=24:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(target),
    ])
    return result.returncode == 0 and target.is_file()


def _make_logo(ffmpeg: str, target: Path) -> bool:
    """生成一张 200x100 的半透明 logo 图。"""
    result = _run([
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=0xFFB454@0.8:s=200x100:d=1",
        "-frames:v", "1", str(target),
    ])
    return result.returncode == 0 and target.is_file()


def _duration(path: Path) -> float:
    info = overlay._probe(path) or {}
    try:
        return float(info.get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _has_audio(path: Path) -> bool:
    """先看 ffprobe，没有 ffprobe 就从 ffmpeg -i 的 stderr 里找 Audio 流。"""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = _run([ffprobe, "-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)])
        if result.returncode == 0:
            return "audio" in (result.stdout or "")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    result = _run([ffmpeg, "-hide_banner", "-i", str(path)], timeout=60)
    return "Audio:" in (result.stderr or "")


# ---------------------------------------------------------------------------
# 用例夹具
# ---------------------------------------------------------------------------
def _fixtures(logo: Path | None = None) -> tuple[dict, dict]:
    project = {
        "id": "overlaytest01",
        "mode": 3,
        "chars_per_second": 5.0,
        "design": {
            "graphics_mode": "overlay",
            "safe_area": {"top": 0.08, "bottom": 0.12, "left": 0.06, "right": 0.06},
            "style_system": {
                "typography": "微软雅黑 粗体",
                "material": "毛玻璃",
                "accent_color": "#FFB454",
                "secondary_color": "#8AB4F8",
            },
            "brand": {"palette": [], "typography_note": "", "logo_refs": []},
        },
    }
    if logo is not None:
        project["design"]["brand"]["logo_refs"] = [str(logo)]

    row = {
        "index": 0,
        "text": "今天我们聊三个关键步骤：识别、判断、执行。",
        "chars": 20,
        "seconds": 3.0,
        "frames": 72,
        "beats": [
            {"index": 0, "trigger": "三个关键步骤", "job": "steps",
             "primary_effect": "stepper", "anchor": "right_side",
             "layer": "screen", "visible_copy": "01 识别 / 02 判断"},
            {"index": 1, "trigger": "", "job": "cta",
             "primary_effect": "cta_card", "anchor": "lower_third",
             "layer": "screen", "visible_copy": "03 执行 · 落地"},
        ],
    }
    return project, row


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("SKIP: 找不到 ffmpeg，跳过叠加测试")
        return 0

    try:
        root = _temp_root()
    except RuntimeError as error:
        print(f"SKIP: {error}")
        return 0

    print(f"临时目录：{root}")
    try:
        return _checks(root, ffmpeg)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _checks(root: Path, ffmpeg: str) -> int:
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)

    source = root / "source.mp4"
    logo = root / "logo.png"
    check("合成 3s 测试视频（320x240 + 正弦音轨）", _make_source(ffmpeg, source))
    check("合成测试 logo", _make_logo(ffmpeg, logo))
    if not source.is_file():
        return _report()

    project, row = _fixtures()

    # ---- 1) 主用例：文字 + 辅行 ----
    out = work / "overlay_seg0.mp4"
    ok = overlay.compose_segment(project, row, source, out, work)
    check("compose_segment 返回 True 且输出存在", ok and out.is_file(),
          f"rc={ok} size={out.stat().st_size if out.is_file() else 0}")

    if ok and out.is_file():
        src_seconds = _duration(source)
        out_seconds = _duration(out)
        check("输出时长与输入相差 < 0.15s",
              abs(out_seconds - src_seconds) < 0.15,
              f"{src_seconds:.3f}s → {out_seconds:.3f}s")
        check("输出仍有音轨", _has_audio(out))
        check("输出不是空文件", out.stat().st_size > 4096,
              f"{out.stat().st_size} bytes")

    # ---- 2) beats 为空 → False，且不产出文件 ----
    empty_row = dict(row)
    empty_row["beats"] = []
    empty_out = work / "overlay_empty.mp4"
    empty_ok = overlay.compose_segment(project, empty_row, source, empty_out, work)
    check("beats 为空返回 False", empty_ok is False)
    check("beats 为空不产出文件", not empty_out.exists())

    # ---- 3) available() 与 PATH 里的 ffmpeg 一致 ----
    check("available() 与 ffmpeg 一致", overlay.available() == bool(shutil.which("ffmpeg")),
          f"available={overlay.available()}")

    # ---- 4) logo 叠加 ----
    logo_project, _ = _fixtures(logo)
    logo_out = work / "overlay_logo.mp4"
    logo_ok = overlay.compose_segment(logo_project, row, source, logo_out, work)
    check("logo_refs 可读时叠加成功", logo_ok and logo_out.is_file(),
          f"rc={logo_ok} size={logo_out.stat().st_size if logo_out.is_file() else 0}")

    # ---- 5) 临时 textfile / 半成品清理 ----
    leftovers = sorted(
        path.name for path in work.iterdir()
        if (path.name.startswith("overlay_") and path.is_dir())
        or (path.name.endswith(".mp4") and ".overlay" in path.name))
    check("临时 textfile 与半成品已清理", not leftovers,
          "、".join(leftovers[:5]))

    return _report()


def _report() -> int:
    if failures:
        print(f"\nFAILED：{len(failures)} 项")
        for name in failures:
            print(" - " + name)
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
