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


# --- model yedekleme (429 kota / 503 yoğunluk) ------------------------------
from google.genai import errors as genai_errors  # noqa: E402

import config  # noqa: E402


def quota_error():
    return genai_errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})


def busy_error():
    return genai_errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})


class FlakyModels(FakeModels):
    """`failing` içindeki modeller hata verir; diğerleri normal yanıt döndürür."""

    def __init__(self, failing: dict, parsed=None):
        super().__init__(parsed=parsed)
        self.failing, self.tried = failing, []

    def generate_content(self, *, model, contents, config):
        self.tried.append((model, config.http_options.retry_options.attempts))
        if model in self.failing:
            raise self.failing[model]
        return super().generate_content(model=model, contents=contents, config=config)


@pytest.fixture
def fallback_cfg(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", ["lite-model"])


def test_analyze_falls_back_on_quota_error(video, fallback_cfg):
    files = FakeFiles([types.FileState.ACTIVE])
    models = FlakyModels({"primary": quota_error()}, parsed=EDL_JSON)
    director = AIDirector(client=SimpleNamespace(files=files, models=models), model="primary")
    edl = director.analyze(video)
    assert edl.sfx_events[0].type == "pop"
    assert [m for m, _ in models.tried] == ["primary", "lite-model"]
    assert director.last_model == "lite-model"
    assert files.deleted == ["files/abc"]            # dosya bir kez yüklendi, yedek de aynı dosyayı kullandı
    assert models.tried[0][1] == 1                   # yedek varken SDK aynı modeli tekrar denemez
    assert models.tried[1][1] == config.GEMINI_RETRY_ATTEMPTS


def test_refine_falls_back_on_busy_error(fallback_cfg):
    from tests.test_refine import CURRENT
    models = FlakyModels({"primary": busy_error()}, parsed=None)
    models.generate_content = lambda **kw: (_ for _ in ()).throw(busy_error()) if kw["model"] == "primary" else \
        SimpleNamespace(parsed=kw["config"].response_schema.model_validate(
            {**CURRENT.model_dump(), "bgm": {**CURRENT.model_dump()["bgm"], "volume": 0.1}}), text=None, candidates=[])
    director = AIDirector(client=SimpleNamespace(models=models, files=None), model="primary")
    result = director.refine(CURRENT, "müziği kıs", 10.0)
    assert result.changed and director.last_model == "lite-model"


def test_all_models_failing_raises_last_error(video, fallback_cfg):
    files = FakeFiles([types.FileState.ACTIVE])
    models = FlakyModels({"primary": quota_error(), "lite-model": busy_error()}, parsed=EDL_JSON)
    director = AIDirector(client=SimpleNamespace(files=files, models=models), model="primary")
    with pytest.raises(genai_errors.ServerError):
        director.analyze(video)
    assert [m for m, _ in models.tried] == ["primary", "lite-model"]
    assert files.deleted == ["files/abc"]            # hata olsa da yüklenen dosya silinir


def test_other_errors_do_not_fall_back(video, fallback_cfg):
    bad = genai_errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    files = FakeFiles([types.FileState.ACTIVE])
    models = FlakyModels({"primary": bad}, parsed=EDL_JSON)
    director = AIDirector(client=SimpleNamespace(files=files, models=models), model="primary")
    with pytest.raises(genai_errors.ClientError):
        director.analyze(video)
    assert [m for m, _ in models.tried] == ["primary"]


def test_fallback_disabled_or_same_model(video, monkeypatch):
    for setting in ([], ["primary"]):
        monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", setting)
        files = FakeFiles([types.FileState.ACTIVE])
        models = FlakyModels({"primary": quota_error()}, parsed=EDL_JSON)
        director = AIDirector(client=SimpleNamespace(files=files, models=models), model="primary")
        with pytest.raises(genai_errors.ClientError):
            director.analyze(video)
        assert [m for m, _ in models.tried] == ["primary"]
        assert models.tried[0][1] == config.GEMINI_RETRY_ATTEMPTS   # yedek yoksa normal yeniden deneme
