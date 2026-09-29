"""Projeler: iteratif doğal dil kurgusu.

Ham video Gemini'ye yalnızca `create` sırasında gider. Sonraki her `edit`
yalnızca mevcut EDL'i (metin) Gemini'ye gönderir, ardından FFmpeg yeni sürümü basar.

POST /api/projects/create               video + ilk istek -> Gemini EDL -> render v1
POST /api/projects/{id}/edit            {"instruction": "..."} -> refine -> render vN+1
POST /api/projects/{id}/manual-edl      elle düzenlenmiş EDL -> doğrudan render vN+1
GET  /api/projects/{id}                 güncel durum + sürüm geçmişi
GET  /api/projects/{id}/video?version=N render edilmiş MP4
"""

from __future__ import annotations

import functools
import logging
import shutil
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

import config
from api.deps import render_locked, save_upload
from models.edl import EditDecisionList
from services import project_store as store
from services.ai_director import AIDirector, AIDirectorError
from services.edl_diff import describe_changes
from services.ffmpeg_tools import FFmpegError, detect_silences, probe

log = logging.getLogger("reels.projects")
router = APIRouter(prefix="/api/projects", tags=["projects"])


class EditRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=500,
                             description="Kurgulanmış videoya göre küçük istek, örn. \"3. saniyedeki whoosh'u kaldır\"")


class ProjectState(BaseModel):
    project_id: str
    version: int
    video_url: str
    duration: float
    edl: EditDecisionList = Field(description="Ham video zamanında güncel EDL")
    changed: bool = True
    changes: list[str] = []
    warnings: list[str] = []
    message: str | None = None


class RevertRequest(BaseModel):
    version: int | None = Field(default=None, ge=1, description="Dönülecek sürüm; boşsa şu anki sürümün bir öncekine (parent) dönülür")


class VersionInfo(BaseModel):
    version: int
    instruction: str | None
    changes: list[str]
    duration: float
    video_url: str
    created_at: str
    parent: int | None = None
    restored_from: int | None = None
    edl: EditDecisionList


class ProjectDetail(ProjectState):
    source_name: str
    source_duration: float
    history: list[VersionInfo]


def _video_url(project_id: str, version: int) -> str:
    return f"/api/projects/{project_id}/video?version={version}"


def _state(project: store.Project, ver: store.ProjectVersion | None = None, **extra) -> ProjectState:
    ver = ver or project.current
    fields = dict(changes=ver.changes, warnings=ver.warnings) | extra
    return ProjectState(project_id=project.id, version=ver.version, video_url=_video_url(project.id, ver.version),
                        duration=ver.duration, edl=ver.edl, **fields)


def _get_project(project_id: str) -> store.Project:
    try:
        return store.load(project_id)
    except KeyError:
        raise HTTPException(404, "Proje bulunamadı.") from None


def _map_errors(fn):
    """Kilit/AI/FFmpeg hatalarını HTTP yanıtına çevirir (ayrıntı günlükte, kullanıcıya kısa mesaj)."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except store.ProjectBusy:
            raise HTTPException(409, "Bu proje üzerinde başka bir işlem sürüyor; bitmesini bekleyin.") from None
        except (AIDirectorError, genai_errors.APIError) as exc:
            log.exception("Gemini hatası")
            raise HTTPException(502, f"Yapay zeka servisi hatası: {str(exc)[:300]}") from exc
        except httpx.TransportError as exc:  # zaman aşımı / bağlantı kopması
            log.exception("Gemini bağlantı hatası")
            raise HTTPException(504, "Yapay zeka servisine ulaşılamadı ya da zaman aşımına uğradı; tekrar deneyin.") from exc
        except FFmpegError as exc:
            log.exception("Render hatası")
            raise HTTPException(500, f"Render başarısız: {str(exc).splitlines()[0][:200]}") from exc
    return wrapper


def _commit_version(project: store.Project, edl: EditDecisionList, instruction: str | None,
                    changes: list[str]) -> store.ProjectVersion:
    """Yeni sürümü render eder; yalnızca render başarılıysa kaydeder (başarısızsa proje bozulmaz)."""
    n = len(project.versions) + 1
    result = render_locked(store.source_path(project), edl, store.render_path(project.id, n))
    ver = store.ProjectVersion(version=n, edl=edl, instruction=instruction, duration=result.duration,
                               warnings=result.warnings, changes=changes, created_at=store.now(),
                               parent=project.current.version if project.versions else None)
    project.versions.append(ver)
    store.save(project)
    return ver


@router.post("/create", status_code=201)
@_map_errors
def create_project(
    video: Annotated[UploadFile, File(description="Ham video (mp4/mov/m4v/webm)")],
    instruction: Annotated[str, Form(max_length=500, description="İlk kurgu isteği (isteğe bağlı)")] = "",
) -> ProjectState:
    ext = Path(video.filename or "").suffix.lower()
    if ext not in config.ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(415, f"Desteklenmeyen uzantı '{ext}'. İzinli: {sorted(config.ALLOWED_VIDEO_EXTENSIONS)}")
    if not config.GEMINI_API_KEY:
        raise HTTPException(503, "GEMINI_API_KEY tanımlı değil (.env).")

    request_text = instruction.strip() or None
    project_id = store.new_id()
    src = config.UPLOADS_DIR / f"{project_id}{ext}"
    try:
        save_upload(video, src)
        try:
            info = probe(src)
        except FFmpegError as exc:
            raise HTTPException(422, "Video okunamadı; dosya bozuk ya da desteklenmiyor.") from exc
        if not info.has_video:
            raise HTTPException(422, "Dosyada video akışı yok.")

        edl = AIDirector().analyze(src, request_text)
        project = store.Project(id=project_id, source_file=src.name, source_name=video.filename or src.name,
                                source_duration=info.duration, created_at=store.now())
        ver = _commit_version(project, edl, request_text, ["İlk kurgu oluşturuldu"])
    except BaseException:
        src.unlink(missing_ok=True)
        shutil.rmtree(config.OUTPUTS_DIR / project_id, ignore_errors=True)
        raise
    return _state(project, ver)


@router.post("/{project_id}/edit")
@_map_errors
def edit_project(project_id: str, body: EditRequest) -> ProjectState:
    """Küçük doğal dil isteği: video yeniden yüklenmez; yalnızca mevcut EDL güncellenir."""
    if not store.is_valid_id(project_id):
        raise HTTPException(404, "Proje bulunamadı.")
    with store.exclusive(project_id):
        project = _get_project(project_id)
        silences = detect_silences(store.source_path(project))  # yerel FFmpeg; video Gemini'ye gitmez
        result = AIDirector().refine(project.current.edl, body.instruction, project.source_duration, silences)
        if not result.changed:
            return _state(project, changed=False, changes=[],
                          message="Bu istekten bir değişiklik çıkmadı (istek anlaşılamadı ya da videonun "
                                  "süresi dışında bir zamanı işaret ediyor). Farklı ifade edip tekrar deneyin.")
        ver = _commit_version(project, result.edl, body.instruction, result.changes)
        return _state(project, ver)


@router.post("/{project_id}/manual-edl")
@_map_errors
def manual_edl(project_id: str, edl: EditDecisionList) -> ProjectState:
    """Elle düzenlenmiş EDL'i doğrudan render eder. Zamanlar HAM video zamanındadır (yanıtlardaki `edl` ile aynı)."""
    if not store.is_valid_id(project_id):
        raise HTTPException(404, "Proje bulunamadı.")
    with store.exclusive(project_id):
        project = _get_project(project_id)
        old = project.current.edl
        if edl.model_dump() == old.model_dump():
            return _state(project, changed=False, changes=[], message="EDL zaten güncel; değişiklik yok.")
        notes = describe_changes(old, edl, project.source_duration, "sn (ham)")  # ham video zamanında
        ver = _commit_version(project, edl, None, ["Elle: " + n for n in notes] or ["EDL elle güncellendi"])
        return _state(project, ver)


@router.post("/{project_id}/revert")
@_map_errors
def revert_project(project_id: str, body: RevertRequest | None = None) -> ProjectState:
    """Bir önceki (ya da `version` ile seçilen) sürüme geri döner. Geçmiş silinmez: hedef sürüm yeni bir sürüm olarak
    kopyalanır (yeniden render gerekmez); böylece 'geri al'ı geri almak da mümkündür."""
    if not store.is_valid_id(project_id):
        raise HTTPException(404, "Proje bulunamadı.")
    with store.exclusive(project_id):
        project = _get_project(project_id)
        cur = project.current
        target_no = body.version if body and body.version else cur.parent
        if target_no is None:
            raise HTTPException(409, "Geri dönülecek önceki sürüm yok.")
        if not 1 <= target_no <= len(project.versions):
            raise HTTPException(404, f"v{target_no} sürümü yok.")
        if target_no == cur.version:
            return _state(project, changed=False, changes=[], message=f"Zaten v{target_no} sürümündesiniz.")

        target = project.versions[target_no - 1]
        n = len(project.versions) + 1
        src, dst = store.render_path(project.id, target_no), store.render_path(project.id, n)
        if src.exists():
            shutil.copy2(src, dst)
        else:  # dosya kaybolmuşsa aynı EDL'den yeniden üret
            render_locked(store.source_path(project), target.edl, dst)
        ver = store.ProjectVersion(
            version=n, edl=target.edl, instruction=None, duration=target.duration, warnings=target.warnings,
            changes=[f"v{target_no} sürümüne dönüldü"], created_at=store.now(), parent=cur.version,
            restored_from=target_no)
        project.versions.append(ver)
        store.save(project)
        return _state(project, ver)


@router.get("/{project_id}")
def get_project(project_id: str) -> ProjectDetail:
    project = _get_project(project_id)
    history = [VersionInfo(version=v.version, instruction=v.instruction, changes=v.changes, duration=v.duration,
                           video_url=_video_url(project.id, v.version), created_at=v.created_at,
                           parent=v.parent, restored_from=v.restored_from, edl=v.edl)
               for v in project.versions]
    return ProjectDetail(**_state(project).model_dump(), source_name=project.source_name,
                         source_duration=project.source_duration, history=history)


@router.get("/{project_id}/video")
def get_project_video(project_id: str, version: Annotated[int | None, Query(ge=1)] = None) -> FileResponse:
    project = _get_project(project_id)
    n = version or project.current.version
    path = store.render_path(project.id, n)
    if n > len(project.versions) or not path.exists():
        raise HTTPException(404, "Sürüm bulunamadı.")
    return FileResponse(path, media_type="video/mp4", filename=f"reels_{project.id}_v{n}.mp4")


@router.get("/{project_id}/source")
def get_project_source(project_id: str) -> FileResponse:
    """Yüklenen orijinal ham video (arayüzdeki 'Orijinal' sekmesi için)."""
    project = _get_project(project_id)
    path = store.source_path(project)
    if not path.exists():
        raise HTTPException(404, "Ham video bulunamadı.")
    return FileResponse(path, filename=project.source_name, content_disposition_type="inline")
