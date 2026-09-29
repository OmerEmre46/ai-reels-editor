"""Proje akışı: create -> edit -> manual-edl -> geçmiş. Gemini sahte; FFmpeg gerçek."""

import json
import shutil
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
import main
from api import projects
from models.edl import EditDecisionList
from services import ai_director
from services.ffmpeg_tools import probe, run_ffmpeg
from services.project_store import exclusive

pytestmark = pytest.mark.skipif(shutil.which(config.FFMPEG_BIN) is None, reason="ffmpeg yok")

FIRST_EDL = {
    "summary": "ilk kurgu",
    "keep_segments": [{"start": 0, "end": 2.5}, {"start": 4, "end": 7}],
    "zoom_effects": [],
    "sfx_events": [{"timestamp": 4.0, "type": "whoosh", "volume": 0.8}],
    "bgm": {"track": "chill", "volume": 0.2, "fade_out_last_seconds": 1},
}


class FakeDirector(ai_director.AIDirector):
    """analyze -> sabit EDL; refine -> gerçek refine mantığı, sahte Gemini modeli."""
    analyze_calls: list = []
    refine_transform = staticmethod(lambda e: e)

    def __init__(self):
        def generate_content(*, model, contents, config):
            sent = json.loads(contents.split("CURRENT EDL:\n")[1].split("\n\nAUDIO HINTS")[0])
            out = FakeDirector.refine_transform(sent)
            return SimpleNamespace(parsed=config.response_schema.model_validate(out), text=None, candidates=[])
        super().__init__(client=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content),
                                                files=None))

    def analyze(self, video_path, instruction=None):
        FakeDirector.analyze_calls.append((str(video_path), instruction))
        return EditDecisionList.model_validate(FIRST_EDL)


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("UPLOADS_DIR", "OUTPUTS_DIR", "PROJECTS_DIR"):
        (tmp_path / name).mkdir()
        monkeypatch.setattr(config, name, tmp_path / name)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(projects, "AIDirector", FakeDirector)
    FakeDirector.analyze_calls = []
    FakeDirector.refine_transform = staticmethod(lambda e: e)
    return TestClient(main.app)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    p = tmp_path_factory.mktemp("clip") / "in.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=10",
                "-f", "lavfi", "-i", "sine=f=300:d=10", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)])
    return p


def create(client, clip, instruction="hızlı tempolu olsun"):
    with clip.open("rb") as f:
        return client.post("/api/projects/create", files={"video": ("in.mp4", f, "video/mp4")},
                           data={"instruction": instruction})


def rendered_duration(client, state):
    r = client.get(state["video_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    path = config.OUTPUTS_DIR / state["project_id"] / f"v{state['version']}.mp4"
    return probe(path).duration


def test_full_iterative_flow(client, clip):
    r = create(client, clip)
    assert r.status_code == 201, r.text
    v1 = r.json()
    assert v1["version"] == 1 and v1["duration"] == pytest.approx(5.5)
    assert FakeDirector.analyze_calls[0][1] == "hızlı tempolu olsun"
    assert rendered_duration(client, v1) == pytest.approx(5.5, abs=0.05)
    pid = v1["project_id"]

    # Çıktıdaki 2.5. sn'deki whoosh kaynakta 4.0. sn'dedir; Gemini çıktı zamanını görmeli.
    def transform(e):
        assert e["sfx_events"][0]["timestamp"] == 2.5  # Gemini çıktı zamanını görür
        e["sfx_events"] = []
        e["zoom_effects"].append({"start": 3.0, "end": 4.0, "scale": 1.3})
        e["bgm"]["volume"] = 0.3
        return e
    FakeDirector.refine_transform = staticmethod(transform)
    r = client.post(f"/api/projects/{pid}/edit", json={"instruction": "whoosh kaldır, zoom ekle, müziği aç"})
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["changed"] and len(FakeDirector.analyze_calls) == 1  # video tekrar gitmedi
    assert v2["edl"]["sfx_events"] == []
    assert v2["edl"]["zoom_effects"] == [{"start": 4.5, "end": 5.5, "scale": 1.3}]  # çıktı 3-4 => kaynak 4.5-5.5
    assert any("SFX kaldırıldı" in c for c in v2["changes"])
    assert rendered_duration(client, v2) == pytest.approx(5.5, abs=0.05)

    # "son 2 saniyeyi kes"
    FakeDirector.refine_transform = staticmethod(lambda e: {**e, "keep_segments": [{"start": 0, "end": 3.5}]})
    v3 = client.post(f"/api/projects/{pid}/edit", json={"instruction": "son 2 saniyeyi kes"}).json()
    assert v3["version"] == 3 and v3["duration"] == pytest.approx(3.5)
    assert v3["edl"]["keep_segments"] == [{"start": 0.0, "end": 2.5}, {"start": 4.0, "end": 5.0}]

    # anlamsız istek: sürüm artmaz, render yok
    FakeDirector.refine_transform = staticmethod(lambda e: e)
    r = client.post(f"/api/projects/{pid}/edit", json={"instruction": "merhaba"}).json()
    assert r["changed"] is False and r["version"] == 3 and r["message"]

    # elle EDL (ham zaman)
    manual = {**v3["edl"], "keep_segments": [{"start": 1, "end": 4}], "zoom_effects": []}
    m = client.post(f"/api/projects/{pid}/manual-edl", json=manual).json()
    assert m["version"] == 4 and m["duration"] == pytest.approx(3.0)

    detail = client.get(f"/api/projects/{pid}").json()
    assert [h["version"] for h in detail["history"]] == [1, 2, 3, 4]
    assert detail["history"][2]["instruction"] == "son 2 saniyeyi kes"
    # eski sürümler hâlâ indirilebilir
    assert client.get(f"/api/projects/{pid}/video?version=1").status_code == 200
    assert client.get(f"/api/projects/{pid}/video?version=9").status_code == 404


def test_state_survives_restart(client, clip):
    pid = create(client, clip).json()["project_id"]
    assert client.get(f"/api/projects/{pid}").json()["version"] == 1  # depo diskten okur


def test_validation_and_errors(client, clip):
    assert client.get("/api/projects/aaaaaaaaaaaa").status_code == 404
    assert client.get("/api/projects/..%2Fetc").status_code == 404
    assert client.post("/api/projects/create", files={"video": ("a.txt", b"x", "text/plain")}).status_code == 415
    assert client.post("/api/projects/create", files={"video": ("a.mp4", b"not a video", "video/mp4")}).status_code == 422
    assert list(config.UPLOADS_DIR.iterdir()) == []  # başarısız yükleme temizlendi

    pid = create(client, clip).json()["project_id"]
    assert client.post(f"/api/projects/{pid}/edit", json={"instruction": ""}).status_code == 422
    assert client.post(f"/api/projects/{pid}/manual-edl", json={"summary": "x"}).status_code == 422
    assert client.post("/api/projects/bbbbbbbbbbbb/edit", json={"instruction": "x"}).status_code == 404


def test_busy_project_returns_409(client, clip):
    pid = create(client, clip).json()["project_id"]
    with exclusive(pid):
        r = client.post(f"/api/projects/{pid}/edit", json={"instruction": "müziği kıs"})
    assert r.status_code == 409


def test_failed_render_does_not_corrupt_project(client, clip, monkeypatch):
    pid = create(client, clip).json()["project_id"]
    bad = {**FIRST_EDL, "keep_segments": [{"start": 0, "end": 3}]}
    with monkeypatch.context() as m:
        m.setattr(projects, "render_locked", lambda *a, **k: (_ for _ in ()).throw(
            projects.FFmpegError("boom\ndetay")))
        assert client.post(f"/api/projects/{pid}/manual-edl", json=bad).status_code == 500
    assert client.get(f"/api/projects/{pid}").json()["version"] == 1


def test_create_without_key(client, clip, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert create(client, clip).status_code == 503


def test_create_failure_cleans_up(client, clip, monkeypatch):
    monkeypatch.setattr(FakeDirector, "analyze",
                        lambda self, *a, **k: (_ for _ in ()).throw(ai_director.AIDirectorError("kota")))
    assert create(client, clip).status_code == 502
    assert list(config.UPLOADS_DIR.iterdir()) == [] and list(config.OUTPUTS_DIR.iterdir()) == []


def _three_versions(client, clip):
    v1 = create(client, clip).json()
    pid = v1["project_id"]
    FakeDirector.refine_transform = staticmethod(lambda e: {**e, "keep_segments": [{"start": 0, "end": 3.5}]})
    v2 = client.post(f"/api/projects/{pid}/edit", json={"instruction": "son 2 saniyeyi kes"}).json()
    FakeDirector.refine_transform = staticmethod(lambda e: {**e, "sfx_events": []})
    v3 = client.post(f"/api/projects/{pid}/edit", json={"instruction": "sfx kaldır"}).json()
    return pid, v1, v2, v3


def test_revert_to_version_and_undo(client, clip):
    pid, v1, v2, v3 = _three_versions(client, clip)
    assert (v1["duration"], v2["duration"], v3["duration"]) == pytest.approx((5.5, 3.5, 3.5))

    # kesilen kısma geri dön: v1'in EDL'i yeni sürüm (v4) olarak gelir, yeniden render gerekmez
    r = client.post(f"/api/projects/{pid}/revert", json={"version": 1})
    assert r.status_code == 200, r.text
    v4 = r.json()
    assert v4["version"] == 4 and v4["edl"] == v1["edl"] and v4["duration"] == pytest.approx(5.5)
    assert v4["changes"] == ["v1 sürümüne dönüldü"]
    assert rendered_duration(client, v4) == pytest.approx(5.5, abs=0.05)

    detail = client.get(f"/api/projects/{pid}").json()
    assert [(h["version"], h["parent"], h["restored_from"]) for h in detail["history"]] == [
        (1, None, None), (2, 1, None), (3, 2, None), (4, 3, 1)]

    # gövdesiz "geri al": parent'a (v3) döner => geri almanın geri alınması
    v5 = client.post(f"/api/projects/{pid}/revert").json()
    assert v5["version"] == 5 and v5["edl"] == v3["edl"] and v5["duration"] == pytest.approx(3.5)

    # düzenleme geri dönülen sürümün üstüne kurulur
    FakeDirector.refine_transform = staticmethod(lambda e: e)
    assert client.get(f"/api/projects/{pid}").json()["edl"] == v3["edl"]


def test_revert_errors(client, clip):
    pid = create(client, clip).json()["project_id"]
    assert client.post(f"/api/projects/{pid}/revert").status_code == 409          # önceki sürüm yok
    assert client.post(f"/api/projects/{pid}/revert", json={"version": 7}).status_code == 404
    r = client.post(f"/api/projects/{pid}/revert", json={"version": 1}).json()    # zaten v1
    assert r["changed"] is False and r["version"] == 1
    assert client.post("/api/projects/cccccccccccc/revert").status_code == 404


def test_manual_edl_reports_diff_and_skips_noop(client, clip):
    v1 = create(client, clip).json()
    pid = v1["project_id"]
    same = client.post(f"/api/projects/{pid}/manual-edl", json=v1["edl"]).json()
    assert same["changed"] is False and same["version"] == 1

    edl = {**v1["edl"], "sfx_events": [], "bgm": {**v1["edl"]["bgm"], "volume": 0.05}}
    m = client.post(f"/api/projects/{pid}/manual-edl", json=edl).json()
    assert m["version"] == 2 and m["changes"]
    assert any(c.startswith("Elle: SFX kaldırıldı: whoosh") for c in m["changes"])
    assert any("Müzik sesi" in c for c in m["changes"])


def test_source_endpoint_serves_original(client, clip):
    pid = create(client, clip).json()["project_id"]
    r = client.get(f"/api/projects/{pid}/source")
    assert r.status_code == 200 and len(r.content) == clip.stat().st_size
    assert r.headers["content-disposition"].startswith("inline")
    assert client.get("/api/projects/dddddddddddd/source").status_code == 404
