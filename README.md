

# AI Reels Editor

**Drop in a raw video, describe the edit in plain language, get a 9:16 Instagram Reel.**
Gemini plans the edit; a deterministic FFmpeg pipeline renders it. Follow-up requests like *"remove the whoosh at 3 s"* or *"cut the last 2 seconds"* update the plan and re-render in seconds, without re-uploading the video.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)

🇹🇷 Türkçe dokümantasyon: [README.tr.md](README.tr.md)

https://github.com/user-attachments/assets/83cf9a74-0ec9-401c-8294-f2e373ad3a82



## Features

- **AI first cut:** Gemini watches the video and returns a structured **Edit Decision List (EDL)**: segments to keep, zoom punches, sound effects and background music.
- **Deterministic rendering:** one FFmpeg pass applies cuts, center zooms (smooth or instant), sound effects (`adelay`, millisecond-accurate) and looped/faded background music (`amix`), output as 1080×1920 H.264/AAC.
- **Iterative natural-language editing:** small requests change only the EDL (text), so they are fast and never disturb the rest of your edit.
- **Timeline-aware:** you talk about the edited video you are watching; the app maps those times back to the original video for you.
- **Versioning:** every change is a new version (`v1…vN`); undo or jump to any version.
- **Web studio:** drag-and-drop upload, chat assistant, quick actions, 9:16 preview (edited / original), layer timeline with one-click removal, music volume slider, download button.
- **REST API** for everything the UI does.

## How it works

```
video ──► ai_director (Gemini, response_schema) ──► EDL (JSON)
                                                     │
video + EDL ──► timeline (cleanup, time remapping) ──► video_renderer (single FFmpeg pass) ──► MP4
                     ▲
"remove the whoosh at 3 s" ──► refine_edl (EDL text only, no video) ──┘
```

- All EDL timestamps are in **original-video time**. `services/timeline.py` remaps them onto the cut timeline (segments are snapped to the frame grid so audio/video never drift).
- For iterative edits the EDL is shown to Gemini in **edited-video time** (what the user watches), and the answer is converted back in code, so timestamp arithmetic never depends on the LLM.
- Audio-dependent requests ("cut the gaps", "add zoom on emphasis") use silence and phrase-start hints measured locally with FFmpeg `silencedetect`; the video is not sent again.
- A human-readable change list is computed by diffing the two EDLs (not by asking the model).

## Requirements

- **Python 3.10+** (tested on 3.13)
- **FFmpeg 7+** (tested with 8.1) with `libx264` and `libmp3lame`, on `PATH` (Windows: `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`)
- A **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey)

## Quick start

### Windows (double-click)

1. Copy `.env.example` to `.env` and set `GEMINI_API_KEY` (or let `baslat.bat` create it and open it for you).
2. Double-click **`baslat.bat`**. On first run it creates `.venv`, installs dependencies and generates placeholder audio. It then starts the server and opens <http://localhost:8000>. Closing its window stops the server.

### Any platform

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
cp .env.example .env                              # then set GEMINI_API_KEY
.venv/bin/python scripts/generate_assets.py       # placeholder sounds (see below)
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>.

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | – | **Required** for AI analysis and refinement |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Any Gemini model that supports video input and structured output. Some older models (e.g. `gemini-2.5-flash`) are closed to new API users. Preview models can change or be busy (`503`); switch models here if needed |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.1-flash-lite` | Fallback model used automatically when the primary model returns **429** (quota) or **503** (overloaded). Comma-separate several; leave empty to disable |
| `GEMINI_ANALYZE_TIMEOUT_SECONDS` | `150` | Timeout for video analysis |
| `GEMINI_REFINE_TIMEOUT_SECONDS` | `45` | Timeout for text-only refinement |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | Set if they are not on `PATH` |

> **Quota and fallback:** The free Gemini tier has per-model daily limits (20 requests/day on some models). If the primary model fails with a quota or overload error, `services/ai_director.py` completes the same request with the fallback model (the video is not re-uploaded). The lighter fallback may cut less precisely; the log shows which model answered. Other errors (bad request, timeout, …) do not trigger the fallback.

## Custom audio assets (sound effects and music)

`python scripts/generate_assets.py` creates **synthetic placeholder sounds** so everything runs out of the box. For real edit quality, download royalty-free audio and drop the files into `assets/sfx/` and `assets/bgm/`:

- Sound effects: <https://pixabay.com/sound-effects/search/rhythmic%20beats/>
- Background music: <https://pixabay.com/music/search/rhythmic%20beats/>

Notes:

- Supported: `.mp3 .wav .m4a .aac .ogg .flac`. No restart needed; the folders are scanned on every request.
- The file name (lowercase, no extension) is what Gemini sees as an option, so name files meaningfully (`whoosh-fast.mp3`, `chill-lofi.mp3`). Adding a file needs no code change.
- SFX durations are passed to Gemini. Short accents (~0.5–2 s) work best; long files (drum breaks, music "logos") are used sparingly.
- Music shorter than the video is looped; longer music is trimmed and faded out.
- Audio files are **git-ignored** (size and licensing): each user downloads their own. Please check the current Pixabay license terms before using or redistributing any file.

## Using the web studio

1. Drop a video (`.mp4 .mov .m4v .webm`, up to 500 MB) and optionally describe the first edit.
2. Refine with short requests in the chat or the quick-action buttons. Times you mention refer to the **edited** video: *"remove the whoosh at 3 s"*, *"add a zoom between 4 and 6 s"*, *"cut the last 2 seconds"*, *"make the music a bit quieter"*.
3. Or edit visually in **Active Edit Layers**: remove a cut/zoom/SFX with ×, change the music track or volume. These use `/manual-edl` and produce a new version.
4. Use **Undo** or the version selector to go back. Undoing never deletes history, it creates a new version copied from the target.
5. **Download** the result (`.mp4`, 1080×1920).

The open project is kept in the URL (`#p=<id>`), so a page reload keeps your history.

## API

Interactive docs are at <http://localhost:8000/docs>.

| Endpoint | Description |
|---|---|
| `POST /api/projects/create` | `video` (multipart) + optional `instruction` → Gemini EDL → render **v1** |
| `POST /api/projects/{id}/edit` | `{"instruction": "…"}` → refine EDL → render **vN+1** (`changed:false` if nothing to do) |
| `POST /api/projects/{id}/manual-edl` | Body: an EDL (original-video time) → render directly |
| `POST /api/projects/{id}/revert` | `{"version": N}` or empty (previous version) |
| `GET /api/projects/{id}` | Current state and full version history |
| `GET /api/projects/{id}/video?version=N` | Rendered MP4 (supports range requests) |
| `GET /api/projects/{id}/source` | Original uploaded video |
| `POST /jobs`, `GET /jobs/{id}` | One-shot queued render (optionally with your own EDL) |
| `GET /health` | ffmpeg / key / asset library check |

```bash
curl -F "video=@clip.mp4" -F "instruction=Fast and energetic" http://localhost:8000/api/projects/create
curl -H "Content-Type: application/json" -d '{"instruction":"cut the last 2 seconds"}' \
     http://localhost:8000/api/projects/<id>/edit
```

A minimal EDL (see [`samples/sample_edl.json`](samples/sample_edl.json)):

```json
{
  "summary": "3 kept segments, two zooms, five SFX and upbeat music.",
  "keep_segments": [{"start": 0.0, "end": 2.5}, {"start": 4.0, "end": 7.0}],
  "zoom_effects":  [{"start": 1.0, "end": 2.0, "scale": 1.3}],
  "sfx_events":    [{"timestamp": 1.0, "type": "pop", "volume": 1.0}],
  "bgm": {"track": "upbeat", "volume": 0.2, "fade_out_last_seconds": 1.5}
}
```

## Project structure

```
api/          FastAPI routers (projects) and shared helpers
models/       EDL pydantic schema
services/     ai_director (Gemini), timeline (time mapping), video_renderer (FFmpeg),
              edl_diff, ffmpeg_tools (probe, silencedetect), project_store
scripts/      generate_assets.py, smoke_test.py
web/          Single-page studio (plain HTML/CSS/JS, no build step)
tests/        Unit, FFmpeg integration and API tests
assets/       sfx/ and bgm/ (git-ignored)
uploads/ outputs/ projects/   Runtime data (git-ignored)
```

## Development

```bash
.venv/bin/python scripts/generate_assets.py   # tests need the placeholder audio
.venv/bin/python -m pytest                    # unit + FFmpeg integration + API tests (no API key needed)
.venv/bin/python scripts/smoke_test.py        # end-to-end render check
```

Tests use a fake Gemini client; only FFmpeg is required.

## Security and privacy

- **Local use only.** There is no authentication. Run it on `127.0.0.1` (as `baslat.bat` does) and do not expose it to the internet: anyone reaching it could upload files and spend your Gemini quota.
- During `create`, the video is uploaded to the Google Gemini API for analysis and deleted from Google after the response. Iterative edits send only EDL text, never the video.
- `.env` (your API key), `uploads/`, `outputs/` and `projects/` are git-ignored.

## Limitations

- Project state is stored as JSON files under `projects/`; there is no multi-user support.
- Content cut from a video cannot be restored by a chat request (it is not in the edited video); use a previous version or `manual-edl` in original time.
- Quality depends on the Gemini model. Preview models may be slow or return `503` under load.

## Contributing

Issues and pull requests are welcome. Please run the test suite before submitting and keep changes focused.

## License

[MIT](LICENSE) © 2026 Ömer Emre
