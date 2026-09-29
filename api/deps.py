"""API katmanı için ortak yardımcılar: render kilidi ve yükleme kaydı."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import HTTPException, UploadFile

from services.video_renderer import render

MAX_UPLOAD_BYTES = 500 * 1024 * 1024

# CPU-yoğun render'lar (iş kuyruğu + proje uçları) aynı anda tek tek çalışır.
_render_lock = threading.Lock()


def render_locked(*args, **kwargs):
    with _render_lock:
        return render(*args, **kwargs)


def save_upload(upload: UploadFile, dest: Path) -> None:
    written = 0
    with dest.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"Dosya {MAX_UPLOAD_BYTES // 2**20} MB sınırını aşıyor.")
            out.write(chunk)
