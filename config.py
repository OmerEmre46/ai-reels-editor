"""Merkezi ayarlar: klasör yolları, render sabitleri ve ortam değişkenleri."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

ASSETS_DIR = BASE_DIR / "assets"
SFX_DIR = ASSETS_DIR / "sfx"
BGM_DIR = ASSETS_DIR / "bgm"
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
PROJECTS_DIR = BASE_DIR / "projects"

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
# Yedek model(ler): birincil model 429 (kota) ya da 503 (yoğunluk) verirse sırayla denenir.
# Virgülle birden fazla yazılabilir; boş bırakılırsa yedekleme kapanır.
GEMINI_FALLBACK_MODELS = [
    m.strip() for m in os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite").split(",") if m.strip()
]
GEMINI_FALLBACK_STATUS = (429, 503)
GEMINI_FILE_POLL_SECONDS = 2.0
GEMINI_FILE_TIMEOUT_SECONDS = 300.0
# SDK'nın varsayılan HTTP zaman aşımı yok; takılan bir çağrı isteği sonsuza dek asılı bırakırdı.
GEMINI_ANALYZE_TIMEOUT_SECONDS = float(os.getenv("GEMINI_ANALYZE_TIMEOUT_SECONDS", "150"))
GEMINI_REFINE_TIMEOUT_SECONDS = float(os.getenv("GEMINI_REFINE_TIMEOUT_SECONDS", "45"))
GEMINI_RETRY_ATTEMPTS = 2

# Render hedefi: Instagram Reels 9:16
OUT_WIDTH = 1080
OUT_HEIGHT = 1920
OUT_FPS = 30
AUDIO_RATE = 48000

# ffmpeg / ffprobe çalıştırılabilir yolları (PATH dışındaysa .env ile verilebilir)
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}  # Gemini destekli

for _d in (SFX_DIR, BGM_DIR, UPLOADS_DIR, OUTPUTS_DIR, PROJECTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
