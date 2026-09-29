"""ffmpeg/ffprobe için ince subprocess sarmalayıcıları."""

from __future__ import annotations

import functools
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import config

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0


def run_ffmpeg(args: list[str], timeout: float | None = None) -> None:
    cmd = [config.FFMPEG_BIN, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args]
    log.debug("ffmpeg %s", " ".join(args))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg bulunamadı: {config.FFMPEG_BIN}") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"ffmpeg hata kodu {proc.returncode}:\n{tail}", proc.stderr)


def probe(path: str | Path) -> MediaInfo:
    cmd = [config.FFPROBE_BIN, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffprobe bulunamadı: {config.FFPROBE_BIN}") from exc
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe okuyamadı: {path}\n{proc.stderr.strip()}", proc.stderr)

    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(data.get("format", {}).get("duration") or 0.0)
    if duration <= 0 and video is not None:
        duration = float(video.get("duration") or 0.0)
    if duration <= 0:
        raise FFmpegError(f"Medya süresi okunamadı: {path}")

    return MediaInfo(
        duration=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
    )


_SIL_START = re.compile(r"silence_start: (-?[\d.]+)")
_SIL_END = re.compile(r"silence_end: (-?[\d.]+)")


@functools.lru_cache(maxsize=64)
def _silences_cached(path: str, mtime: float, noise_db: float, min_dur: float) -> tuple[tuple[float, float], ...]:
    info = probe(path)
    if not info.has_audio:
        return ()
    cmd = [config.FFMPEG_BIN, "-hide_banner", "-nostdin", "-vn", "-i", path,
           "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return ()
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in proc.stderr.splitlines():
        if (m := _SIL_START.search(line)):
            start = max(0.0, float(m.group(1)))
        elif (m := _SIL_END.search(line)) and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:  # dosya sonuna kadar süren sessizlik
        out.append((start, info.duration))
    return tuple(out)


def detect_silences(path: str | Path, noise_db: float = -35.0, min_dur: float = 0.35) -> list[tuple[float, float]]:
    """Ham videodaki sessiz aralıklar (ham zaman, sn). Yerel FFmpeg; Gemini'ye video göndermeden konuşma boşluklarını verir."""
    p = Path(path)
    return list(_silences_cached(str(p), p.stat().st_mtime, noise_db, min_dur))
