"""AIDirector akışı sahte istemciyle (ağ/API anahtarı gerektirmez)."""

from types import SimpleNamespace

import pytest
from google.genai import types
from pydantic import ValidationError

from models.edl import EditDecisionList
from services import ai_director
from services.ai_director import AIDirector, AIDirectorError, build_response_schema

EDL_JSON = {
    "summary": "test",
    "keep_segments": [{"start": 0, "end": 4}],
    "zoom_effects": [{"start": 1, "end": 2, "scale": 1.2}],
    "sfx_events": [{"timestamp": 1, "type": "pop", "volume": 0.8}],
    "bgm": {"track": "chill", "volume": 0.15, "fade_out_last_seconds": 2},
}


class FakeFiles:
    def __init__(self, states):
        self.states = list(states)
        self.deleted = []
        self.uploaded_with = None

    def _file(self):
        return types.File(name="files/abc", uri="https://x/files/abc", mime_type="video/mp4",
                          state=self.states.pop(0))

    def upload(self, *, file, config):
        self.uploaded_with = (file, config.mime_type)
        return self._file()

    def get(self, *, name):
        return self._file()

    def delete(self, *, name):
        self.deleted.append(name)


class FakeModels:
    def __init__(self, parsed=None, text=None):
        self.parsed, self.text, self.call = parsed, text, None

    def generate_content(self, *, model, contents, config):
        self.call = SimpleNamespace(model=model, contents=contents, config=config)
        parsed = config.response_schema.model_validate(self.parsed) if self.parsed else None
        return SimpleNamespace(parsed=parsed, text=self.text, candidates=[])


@pytest.fixture
def video(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_director, "probe", lambda p: SimpleNamespace(duration=10.0))
    monkeypatch.setattr(ai_director.config, "GEMINI_FILE_POLL_SECONDS", 0)
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\0")
    return p


def make(files, models):
    return AIDirector(client=SimpleNamespace(files=files, models=models), model="gemini-2.5-flash")


def test_analyze_waits_for_active_and_returns_edl(video):
    files = FakeFiles([types.FileState.PROCESSING, types.FileState.PROCESSING, types.FileState.ACTIVE])
    models = FakeModels(parsed=EDL_JSON)
    edl = make(files, models).analyze(video)

    assert isinstance(edl, EditDecisionList) and type(edl) is EditDecisionList
    assert edl.sfx_events[0].type == "pop" and edl.bgm.track == "chill"
    assert files.uploaded_with == (str(video), "video/mp4")
    assert files.deleted == ["files/abc"]
    cfg = models.call.config
    assert cfg.response_mime_type == "application/json"
    assert "10.00 s" in cfg.system_instruction
    assert models.call.contents[0].video_metadata.fps == 5.0  # 150 kare / 10 sn -> 5'e sınırlı


def test_failed_processing_raises_and_cleans_up(video):
    files = FakeFiles([types.FileState.PROCESSING, types.FileState.FAILED])
    with pytest.raises(AIDirectorError):
        make(files, FakeModels(parsed=EDL_JSON)).analyze(video)


def test_falls_back_to_text_json(video):
    import json
    files = FakeFiles([types.FileState.ACTIVE])
    edl = make(files, FakeModels(text=json.dumps(EDL_JSON))).analyze(video)
    assert edl.keep_segments[0].end == 4
    assert files.deleted == ["files/abc"]


def test_schema_enum_rejects_unknown_asset_names():
    schema = build_response_schema(["pop", "whoosh"], ["chill"])
    bad = {**EDL_JSON, "sfx_events": [{"timestamp": 1, "type": "laser", "volume": 1}]}
    with pytest.raises(ValidationError):
        schema.model_validate(bad)
    assert schema.model_validate({**EDL_JSON, "bgm": {**EDL_JSON["bgm"], "track": "none"}})


def test_missing_api_key(monkeypatch):
    monkeypatch.setattr(ai_director.config, "GEMINI_API_KEY", "")
    with pytest.raises(AIDirectorError):
        AIDirector()
