"""API uçtan uca: EDL'li yükleme -> kuyruk -> render -> indirme."""

import shutil
import time

import pytest
from fastapi.testclient import TestClient

import config
import main
from services.ffmpeg_tools import run_ffmpeg

pytestmark = pytest.mark.skipif(shutil.which(config.FFMPEG_BIN) is None, reason="ffmpeg yok")

EDL = """{"summary": "api", "keep_segments": [{"start": 0.5, "end": 2.5}],
 "zoom_effects": [{"start": 1, "end": 2, "scale": 1.2}],
 "sfx_events": [{"timestamp": 1.0, "type": "ding", "volume": 0.8}],
 "bgm": {"track": "dramatic", "volume": 0.2, "fade_out_last_seconds": 0.5}}"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path)
    return TestClient(main.app)


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "in.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=3",
                "-f", "lavfi", "-i", "sine=f=300:d=3", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)])
    return p


def wait(client, job_id, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.2)
    raise TimeoutError(job_id)


def test_render_job_with_manual_edl(client, clip):
    with clip.open("rb") as f:
        r = client.post("/jobs", files={"video": ("in.mp4", f, "video/mp4")}, data={"edl": EDL})
    assert r.status_code == 202, r.text
    job = wait(client, r.json()["id"])
    assert job["status"] == "done", job
    assert job["duration"] == pytest.approx(2.0)

    video = client.get(f"/jobs/{job['id']}/video")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert len(video.content) > 10_000


def test_rejects_bad_extension_and_bad_edl(client, clip):
    assert client.post("/jobs", files={"video": ("a.txt", b"x", "text/plain")}, data={"edl": EDL}).status_code == 415
    with clip.open("rb") as f:
        r = client.post("/jobs", files={"video": ("in.mp4", f, "video/mp4")}, data={"edl": "{bad"})
    assert r.status_code == 422


def test_ai_job_requires_key(client, clip, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with clip.open("rb") as f:
        assert client.post("/jobs", files={"video": ("in.mp4", f, "video/mp4")}).status_code == 503


def test_health(client):
    h = client.get("/health").json()
    assert h["ffmpeg"] and {"pop", "whoosh"} <= set(h["sfx"]) and "chill" in h["bgm"]


def test_web_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "AI Reels Kurgu Stüdyosu" in r.text
    for asset in ("/static/app.js", "/static/app.css"):
        a = client.get(asset)
        assert a.status_code == 200 and a.headers["cache-control"] == "no-cache"
    assert 'id="dropzone"' in r.text and 'id="undoBtn"' in r.text and 'id="downloadBtn"' in r.text
