"""Uçtan uca render doğrulaması (Gemini gerektirmez).

1. Eksik test asset'lerini üretir.
2. 10 sn'lik yatay (1920x1080) test videosu üretir: uploads/test_input.mp4
3. samples/sample_edl.json ile render eder: outputs/test_output.mp4
4. Çıktıyı ffprobe + tam decode ile doğrular.

    python scripts/smoke_test.py
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from models.edl import EditDecisionList  # noqa: E402
from scripts import generate_assets  # noqa: E402
from services.ffmpeg_tools import run_ffmpeg  # noqa: E402
from services.video_renderer import render  # noqa: E402

TEST_INPUT = config.UPLOADS_DIR / "test_input.mp4"
TEST_OUTPUT = config.OUTPUTS_DIR / "test_output.mp4"
SAMPLE_EDL = config.BASE_DIR / "samples" / "sample_edl.json"


def make_test_video(path: Path, seconds: int = 10) -> None:
    run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc=size=1920x1080:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}",
        "-filter:a", "volume=0.3",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ])


def ffprobe_json(path: Path) -> dict:
    out = subprocess.run(
        [config.FFPROBE_BIN, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.argv = sys.argv[:1]
    generate_assets.main()

    make_test_video(TEST_INPUT)
    print(f"Test videosu: {TEST_INPUT}")

    edl = EditDecisionList.model_validate_json(SAMPLE_EDL.read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    result = render(TEST_INPUT, edl, TEST_OUTPUT)
    print(f"Render {time.perf_counter() - t0:.1f} sn -> {result.output_path}")

    info = ffprobe_json(TEST_OUTPUT)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])

    # Tam decode: bozuk kare/paket varsa stderr'e yazılır
    dec = subprocess.run([config.FFMPEG_BIN, "-v", "error", "-i", str(TEST_OUTPUT), "-f", "null", "-"],
                         capture_output=True, text=True)

    checks = {
        "dosya var ve boş değil": TEST_OUTPUT.exists() and TEST_OUTPUT.stat().st_size > 0,
        "1080x1920": (v["width"], v["height"]) == (1080, 1920),
        "h264 + aac": (v["codec_name"], a["codec_name"]) == ("h264", "aac"),
        f"süre ~ {result.duration:.2f}s (ölçülen {dur:.3f}s)": abs(dur - result.duration) < 0.1,
        "decode hatasız": dec.returncode == 0 and not dec.stderr.strip(),
        "EDL uyarısı yok": not result.warnings,
    }
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    if dec.stderr.strip():
        print(dec.stderr)
    for w in result.warnings:
        print(f"  uyarı: {w}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
