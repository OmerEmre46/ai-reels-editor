"""Proje durumu (state) deposu: diskte JSON, sunucu yeniden başlasa da kaybolmaz.

Düzen:
  uploads/{id}{ext}          ham video (bir kez yüklenir, bir daha Gemini'ye gitmez)
  outputs/{id}/v{n}.mp4      her sürümün render'ı
  projects/{id}.json         Project (EDL geçmişi dahil)
"""

from __future__ import annotations

import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

import config
from models.edl import EditDecisionList

_ID_RE = re.compile(r"^[0-9a-f]{12}$")


class ProjectVersion(BaseModel):
    version: int
    edl: EditDecisionList  # ham video zamanında
    instruction: str | None = None
    duration: float
    warnings: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    created_at: str
    parent: int | None = None         # bu sürümün türetildiği sürüm (geri al = parent'a dön)
    restored_from: int | None = None  # revert ile oluştuysa hangi sürümün kopyası


class Project(BaseModel):
    id: str
    source_file: str
    source_name: str
    source_duration: float
    created_at: str
    versions: list[ProjectVersion] = Field(default_factory=list)

    @property
    def current(self) -> ProjectVersion:
        return self.versions[-1]


class ProjectBusy(RuntimeError):
    pass


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def is_valid_id(project_id: str) -> bool:
    return bool(_ID_RE.match(project_id))


def _path(project_id: str) -> Path:
    if not is_valid_id(project_id):
        raise KeyError(project_id)
    return config.PROJECTS_DIR / f"{project_id}.json"


def render_path(project_id: str, version: int) -> Path:
    return config.OUTPUTS_DIR / project_id / f"v{version}.mp4"


def source_path(project: Project) -> Path:
    return config.UPLOADS_DIR / project.source_file


def save(project: Project) -> None:
    path = _path(project.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def load(project_id: str) -> Project:
    """KeyError: proje yok / kimlik geçersiz."""
    path = _path(project_id)
    if not path.exists():
        raise KeyError(project_id)
    return Project.model_validate_json(path.read_text(encoding="utf-8"))


@contextmanager
def exclusive(project_id: str):
    """Aynı projede eşzamanlı düzenlemeyi engeller (beklemez, ProjectBusy fırlatır)."""
    with _locks_guard:
        lock = _locks.setdefault(project_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ProjectBusy(project_id)
    try:
        yield
    finally:
        lock.release()
