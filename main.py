"""FastAPI giriş noktası.

    uvicorn main:app --reload

GET  /                                  web arayüzü (AI Reels Kurgu Stüdyosu)
POST /jobs          video (+ isteğe bağlı hazır EDL JSON) yükle -> iş kimliği
GET  /jobs/{id}     durum: queued | analyzing | rendering | done | failed
GET  /jobs/{id}/video  bitmiş MP4
GET  /health        ffmpeg / Gemini anahtarı / asset kütüphanesi kontrolü

İşler tek işçili bir kuyrukta sırayla işlenir (render CPU-yoğundur).
İş durumu bellek içindedir; süreç yeniden başlarsa kaybolur.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

import config
from api.deps import render_locked, save_upload
from api.projects import router as projects_router
from models.edl import EditDecisionList
from services.ai_director import AIDirector
from services.video_renderer import available_bgm, available_sfx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reels")

JobStatus = Literal["queued", "analyzing", "rendering", "done", "failed"]


class Job(BaseModel):
    id: str
    status: JobStatus = "queued"
    source: str
    edl: EditDecisionList | None = None
    warnings: list[str] = []
    duration: float | None = None
    error: str | None = None


app = FastAPI(title="Reels AI Editor - Core Engine")
app.include_router(projects_router)

WEB_DIR = config.BASE_DIR / "web"
_NO_CACHE = {"Cache-Control": "no-cache"}  # geliştirme sırasında eski JS/CSS kalmasın


class _StaticNoCache(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers.update(_NO_CACHE)
        return resp


app.mount("/static", _StaticNoCache(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """AI Reels Kurgu Stüdyosu (web arayüzü)."""
    return FileResponse(WEB_DIR / "index.html", headers=_NO_CACHE)

_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id] = _jobs[job_id].model_copy(update=fields)


def _output_path(job_id: str) -> Path:
    return config.OUTPUTS_DIR / f"{job_id}.mp4"


def _process(job_id: str, video_path: Path, edl: EditDecisionList | None) -> None:
    try:
        if edl is None:
            _update(job_id, status="analyzing")
            edl = AIDirector().analyze(video_path)
            _update(job_id, edl=edl)
        (config.OUTPUTS_DIR / f"{job_id}.edl.json").write_text(
            edl.model_dump_json(indent=2), encoding="utf-8")

        _update(job_id, status="rendering")
        result = render_locked(video_path, edl, _output_path(job_id))
        _update(job_id, status="done", warnings=result.warnings, duration=result.duration)
    except Exception as exc:
        log.exception("İş başarısız: %s", job_id)
        _update(job_id, status="failed", error=str(exc))


@app.get("/health")
def health() -> dict:
    return {
        "ffmpeg": shutil.which(config.FFMPEG_BIN) is not None,
        "ffprobe": shutil.which(config.FFPROBE_BIN) is not None,
        "gemini_key": bool(config.GEMINI_API_KEY),
        "model": config.GEMINI_MODEL,
        "sfx": sorted(available_sfx()),
        "bgm": sorted(available_bgm()),
    }


@app.post("/jobs", status_code=202)
def create_job(
    video: Annotated[UploadFile, File(description="Ham video (mp4/mov/m4v/webm)")],
    edl: Annotated[str | None, Form(description="Hazır EDL JSON; verilirse Gemini atlanır")] = None,
) -> Job:
    ext = Path(video.filename or "").suffix.lower()
    if ext not in config.ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(415, f"Desteklenmeyen uzantı '{ext}'. İzinli: {sorted(config.ALLOWED_VIDEO_EXTENSIONS)}")

    parsed_edl = None
    if edl:
        try:
            parsed_edl = EditDecisionList.model_validate(json.loads(edl))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(422, f"Geçersiz EDL: {exc}") from exc
    elif not config.GEMINI_API_KEY:
        raise HTTPException(503, "GEMINI_API_KEY tanımlı değil; ya anahtar ekleyin ya da 'edl' gönderin.")

    job_id = uuid.uuid4().hex[:12]
    src = config.UPLOADS_DIR / f"{job_id}{ext}"
    save_upload(video, src)

    job = Job(id=job_id, source=src.name, edl=parsed_edl)
    with _lock:
        _jobs[job_id] = job
    _worker.submit(_process, job_id, src, parsed_edl)
    return job


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> Job:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "İş bulunamadı.")
    return job


@app.get("/jobs/{job_id}/video")
def get_video(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if job.status != "done":
        raise HTTPException(409, f"Video hazır değil (durum: {job.status}).")
    return FileResponse(_output_path(job_id), media_type="video/mp4", filename=f"reels_{job_id}.mp4")
