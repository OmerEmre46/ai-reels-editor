"""EDL + ham video -> 1080x1920 Reels MP4. Deterministik, tek geçişli FFmpeg boru hattı.

Filtre grafiği (tek ffmpeg çağrısı):

  [0:v] fps -> 9:16 cover (scale+crop) -> split=N -> trim_i ─┐
  [0:a] (yoksa anullsrc) -> 48k stereo -> asplit=N -> atrim_i ┴> concat -> [vcat][voice]
  [vcat] -> zoompan (merkez, yumuşak rampalı) -> [vout]
  [sfx_k] -> volume -> adelay(ms)  ┐
  [bgm]   -> atrim(D) -> volume -> afade ┴> amix(normalize=0) + [voice] -> alimiter -> [aout]

Kesimler önce uygulanır; zoom/SFX zamanları `services.timeline` ile çıktı
zamanına haritalanmış olarak gelir.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import config
from models.edl import EditDecisionList
from services.ffmpeg_tools import probe, run_ffmpeg
from services.timeline import Timeline, ZoomWindow, build_timeline

log = logging.getLogger(__name__)

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
ZOOM_RAMP_SECONDS = 0.2    # 0 => anlık (punch-in) zoom
BGM_FADE_IN_SECONDS = 0.5
_INLINE_GRAPH_LIMIT = 7000  # Windows komut satırı sınırına karşı; üstü dosyadan okunur


@dataclass
class RenderResult:
    output_path: Path
    duration: float
    warnings: list[str] = field(default_factory=list)


def _library(folder: Path) -> dict[str, Path]:
    """Klasördeki ses dosyaları: {küçük harf dosya adı (uzantısız): yol}."""
    lib: dict[str, Path] = {}
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            lib.setdefault(p.stem.lower(), p)
    return lib


def available_sfx() -> dict[str, Path]:
    return _library(config.SFX_DIR)


def available_bgm() -> dict[str, Path]:
    return _library(config.BGM_DIR)


def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _zoom_expr(zooms: list[ZoomWindow], ramp: float) -> str:
    """Çıktı zamanı `it`'ye göre zoom faktörü; çakışmalarda en büyük kazanır.

    Her pencere için zarf: rampada smoothstep (0->1), ortada 1, sonda (1->0).
    """
    terms: list[str] = []
    for w in zooms:
        amp = _f(w.scale - 1.0)
        r = min(ramp, (w.end - w.start) / 2)
        if r <= 0:
            env = f"between(it,{_f(w.start)},{_f(w.end)})"
        else:
            lin = f"clip(min((it-{_f(w.start)})/{_f(r)},({_f(w.end)}-it)/{_f(r)}),0,1)"
            env = f"(({lin})*({lin})*(3-2*({lin})))"
        terms.append(f"{amp}*{env}")
    expr = terms[0]
    for t in terms[1:]:
        expr = f"max({expr},{t})"
    return f"1+{expr}"


def build_filter_graph(tl: Timeline, has_audio: bool, sfx_input_base: int,
                       bgm_input_index: int | None) -> tuple[str, str, str]:
    W, H, FPS, SR = config.OUT_WIDTH, config.OUT_HEIGHT, config.OUT_FPS, config.AUDIO_RATE
    n = len(tl.cuts)
    fmt = f"aresample={SR},aformat=sample_fmts=fltp:channel_layouts=stereo"
    g: list[str] = []

    # 1) Video: sabit fps + 9:16 cover, sonra kesim dalları
    v_split = "".join(f"[vsrc{i}]" for i in range(n))
    g.append(
        f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={W}:{H},setsar=1,format=yuv420p,split={n}{v_split}"
    )
    # 2) Ses: kaynakta ses yoksa sessizlik üret (süre boyunca)
    a_split = "".join(f"[asrc{i}]" for i in range(n))
    src_audio = "[0:a]" if has_audio else f"anullsrc=r={SR}:cl=stereo,atrim=0:{_f(tl.cuts[-1].src_end)},"
    g.append(f"{src_audio}{fmt},asplit={n}{a_split}")

    concat_in = []
    for i, c in enumerate(tl.cuts):
        s, e = _f(c.src_start), _f(c.src_end)
        g.append(f"[vsrc{i}]trim=start={s}:end={e},setpts=PTS-STARTPTS[v{i}]")
        g.append(f"[asrc{i}]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{i}]")
        concat_in.append(f"[v{i}][a{i}]")
    g.append(f"{''.join(concat_in)}concat=n={n}:v=1:a=1[vcat][voice]")

    # 3) Zoom (merkezden, 9:16 çıktı korunur)
    if tl.zooms:
        z = _zoom_expr(tl.zooms, ZOOM_RAMP_SECONDS)
        g.append(
            f"[vcat]zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p[vout]"
        )
    else:
        g.append("[vcat]null[vout]")

    # 4) Ses karışımı: orijinal ses + SFX + BGM
    mix = ["[voice]"]
    for k, ev in enumerate(tl.sfx):
        ms = int(round(ev.time * 1000))
        g.append(f"[{sfx_input_base + k}:a]{fmt},volume={_f(ev.volume)},adelay={ms}:all=1[sfx{k}]")
        mix.append(f"[sfx{k}]")
    if tl.bgm is not None and bgm_input_index is not None:
        d = tl.duration
        chain = f"[{bgm_input_index}:a]{fmt},atrim=0:{_f(d)},asetpts=PTS-STARTPTS,volume={_f(tl.bgm.volume)}"
        fade_in = min(BGM_FADE_IN_SECONDS, d / 4)
        if fade_in > 0:
            chain += f",afade=t=in:st=0:d={_f(fade_in)}"
        if tl.bgm.fade_out > 0:
            chain += f",afade=t=out:st={_f(max(0.0, d - tl.bgm.fade_out))}:d={_f(tl.bgm.fade_out)}"
        g.append(chain + "[bgm]")
        mix.append("[bgm]")

    if len(mix) > 1:
        # normalize=0: amix girdi sayısına bölmesin; tepe koruması alimiter'da
        g.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0,"
                 f"alimiter=limit=0.95:level=disabled[aout]")
    else:
        g.append("[voice]anull[aout]")

    return ";\n".join(g), "[vout]", "[aout]"


def render(video_path: str | Path, edl: EditDecisionList, output_path: str | Path,
           crf: int = 20, preset: str = "veryfast") -> RenderResult:
    video_path, output_path = Path(video_path), Path(output_path)
    info = probe(video_path)
    if not info.has_video:
        raise ValueError(f"Girdide video akışı yok: {video_path}")

    sfx_lib, bgm_lib = available_sfx(), available_bgm()
    tl = build_timeline(edl, info.duration, config.OUT_FPS, sfx_lib.keys(), bgm_lib.keys())
    for w in tl.warnings:
        log.warning("EDL: %s", w)

    inputs: list[str] = ["-i", str(video_path)]
    sfx_base = 1
    for ev in tl.sfx:
        inputs += ["-i", str(sfx_lib[ev.type])]
    bgm_index = None
    if tl.bgm is not None:
        bgm_index = sfx_base + len(tl.sfx)
        inputs += ["-stream_loop", "-1", "-i", str(bgm_lib[tl.bgm.track])]  # kısa parça döngülenir

    graph, vout, aout = build_filter_graph(tl, info.has_audio, sfx_base, bgm_index)
    log.info("Render: %d kesim, %d zoom, %d sfx, bgm=%s, süre=%.2fs",
             len(tl.cuts), len(tl.zooms), len(tl.sfx), tl.bgm.track if tl.bgm else None, tl.duration)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = output_path.with_name(output_path.stem + ".part" + output_path.suffix)
    encode = [
        "-map", vout, "-map", aout,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-r", str(config.OUT_FPS), "-g", str(config.OUT_FPS * 2),
        "-c:a", "aac", "-b:a", "192k", "-ar", str(config.AUDIO_RATE), "-ac", "2",
        "-t", _f(tl.duration), "-movflags", "+faststart", str(tmp_out),
    ]

    try:
        if len(graph) <= _INLINE_GRAPH_LIMIT:
            run_ffmpeg([*inputs, "-filter_complex", graph, *encode])
        else:
            with tempfile.TemporaryDirectory() as td:
                gpath = Path(td) / "graph.txt"
                gpath.write_text(graph, encoding="utf-8")
                run_ffmpeg([*inputs, "-/filter_complex", str(gpath), *encode])  # FFmpeg >= 7
        tmp_out.replace(output_path)
    finally:
        tmp_out.unlink(missing_ok=True)

    return RenderResult(output_path=output_path, duration=round(tl.duration, 3), warnings=tl.warnings)
