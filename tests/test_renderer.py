"""FFmpeg ile gerçek render testleri: kesim doğruluğu ve SFX zamanlaması ölçülür."""

import re
import shutil
import subprocess

import pytest

import config
from models.edl import EditDecisionList
from services.ffmpeg_tools import probe, run_ffmpeg
from services.video_renderer import render

pytestmark = pytest.mark.skipif(shutil.which(config.FFMPEG_BIN) is None, reason="ffmpeg yok")


@pytest.fixture(scope="module")
def luma_clock(tmp_path_factory):
    """Sessiz (ses akışı YOK) 10 sn video; parlaklık her saniye artar => kare, kaynak saniyesini kodlar."""
    path = tmp_path_factory.mktemp("src") / "clock.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=10,format=yuv444p,"
                "geq=lum='20+floor(T)*20':cb=128:cr=128",
                "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", str(path)])
    return path


def gray_at(path, t: float) -> int:
    out = subprocess.run(
        [config.FFMPEG_BIN, "-v", "error", "-i", str(path), "-ss", f"{t:.3f}", "-frames:v", "1",
         "-vf", "scale=1:1,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    return out[0]


def first_sound(path) -> float:
    """silencedetect ile sesin başladığı ilk an (sn); hiç ses yoksa inf."""
    err = subprocess.run(
        [config.FFMPEG_BIN, "-hide_banner", "-i", str(path), "-af", "silencedetect=n=-50dB:d=0.05",
         "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    start = re.search(r"silence_start: ([\d.]+)", err)
    if start is None or float(start.group(1)) > 0.01:
        return 0.0  # baştaki sessizlik yok: ses hemen başlıyor
    end = re.search(r"silence_end: ([\d.]+)", err)
    return float(end.group(1)) if end else float("inf")


def edl(**kw) -> EditDecisionList:
    base = {"summary": "t", "keep_segments": [], "zoom_effects": [], "sfx_events": [],
            "bgm": {"track": "none", "volume": 0, "fade_out_last_seconds": 0}}
    base.update(kw)
    return EditDecisionList.model_validate(base)


def test_cuts_land_on_correct_source_frames(luma_clock, tmp_path):
    out = tmp_path / "cut.mp4"
    res = render(luma_clock, edl(keep_segments=[{"start": 0, "end": 2.5}, {"start": 4, "end": 7}],
                                 zoom_effects=[{"start": 4.2, "end": 5.0, "scale": 1.4}]), out)
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio  # kaynakta ses yoktu; sessiz parça eklenmiş olmalı
    assert info.duration == pytest.approx(5.5, abs=0.05)
    assert res.duration == pytest.approx(5.5)
    # çıktı zamanı -> beklenen kaynak zamanı
    for t_out, t_src in [(1.2, 1.2), (3.0, 4.5), (5.2, 6.7)]:
        assert abs(gray_at(out, t_out) - gray_at(luma_clock, t_src)) <= 3, (t_out, t_src)


def test_sfx_is_delayed_to_remapped_time(luma_clock, tmp_path):
    out = tmp_path / "sfx.mp4"
    # kaynak 4.0 sn'deki SFX, [0-2.5] + [4-7] kesiminden sonra çıktıda 2.5 sn'de duyulmalı
    render(luma_clock, edl(keep_segments=[{"start": 0, "end": 2.5}, {"start": 4, "end": 7}],
                           sfx_events=[{"timestamp": 4.0, "type": "pop", "volume": 1.0}]), out)
    assert first_sound(out) == pytest.approx(2.5, abs=0.05)


def test_bgm_spans_output_and_fades(luma_clock, tmp_path):
    out = tmp_path / "bgm.mp4"
    render(luma_clock, edl(keep_segments=[{"start": 1, "end": 5}],
                           bgm={"track": "chill", "volume": 0.5, "fade_out_last_seconds": 1.0}), out)
    assert probe(out).duration == pytest.approx(4.0, abs=0.05)
    assert first_sound(out) < 0.3  # müzik baştan (kısa fade-in ile) başlar
