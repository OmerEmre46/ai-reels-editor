"""EDL temizliği ve zaman çizelgesi yeniden haritalama (timestamp remapping).

Saf Python; FFmpeg'e dokunmaz, bu yüzden birim testi kolaydır.

Kurallar:
  * EDL zamanları HAM videoya göredir; buradan çıkan her şey ÇIKTI zamanıdır.
  * Segment sınırları kare ızgarasına (1/fps) oturtulur; böylece her parçada
    video kare sayısı ile ses örnek sayısı birebir aynı süreye denk gelir ve
    kesimler boyunca A/V kayması birikmez.
  * Kesilen bir bölgeye düşen SFX, kesim sınırına SNAP_TOLERANCE'tan yakınsa
    birleşme noktasına taşınır (geçiş efekti niyeti), değilse atılır.
  * Kesim sınırını aşan zoom parçalara bölünür; çıktıda bitişik kalan
    parçalar tekrar birleştirilir ki kesimde gereksiz zoom-out/in olmasın.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import PurePath

from models.edl import BgmSettings, EditDecisionList, Segment, SfxEvent, ZoomEffect

MIN_SEGMENT_FRAMES = 3
SNAP_TOLERANCE = 0.3
ZOOM_MIN, ZOOM_MAX = 1.0, 2.0
SFX_VOLUME_MAX = 2.0
BGM_VOLUME_MAX = 1.0
BGM_FADE_MAX = 10.0
_EPS = 1e-6


@dataclass(frozen=True)
class Cut:
    src_start: float
    src_end: float
    out_start: float

    @property
    def length(self) -> float:
        return self.src_end - self.src_start

    @property
    def out_end(self) -> float:
        return self.out_start + self.length


@dataclass(frozen=True)
class ZoomWindow:
    start: float
    end: float
    scale: float


@dataclass(frozen=True)
class PlacedSfx:
    time: float
    type: str
    volume: float


@dataclass(frozen=True)
class BgmPlan:
    track: str
    volume: float
    fade_out: float


@dataclass
class Timeline:
    cuts: list[Cut]
    zooms: list[ZoomWindow]
    sfx: list[PlacedSfx]
    bgm: BgmPlan | None
    duration: float
    warnings: list[str] = field(default_factory=list)

    def map_point(self, t: float) -> float | None:
        return _map_point(self.cuts, t)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _finite(*vals: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def _build_cuts(edl: EditDecisionList, source_duration: float, fps: int,
                warnings: list[str]) -> list[Cut]:
    last_frame = math.floor(source_duration * fps + _EPS)
    frames: list[tuple[int, int]] = []

    for seg in edl.keep_segments:
        if not _finite(seg.start, seg.end) or seg.end <= seg.start:
            warnings.append(f"Geçersiz segment atlandı: {seg.start}-{seg.end}")
            continue
        a = max(0, round(seg.start * fps))
        b = min(last_frame, round(seg.end * fps))
        if b - a < MIN_SEGMENT_FRAMES:
            warnings.append(f"Çok kısa/aralık dışı segment atlandı: {seg.start:.2f}-{seg.end:.2f}")
            continue
        frames.append((a, b))

    if not frames:
        if edl.keep_segments:
            warnings.append("Geçerli segment kalmadı; tüm video korunuyor.")
        frames = [(0, last_frame)]

    frames.sort()
    merged: list[list[int]] = [list(frames[0])]
    for a, b in frames[1:]:
        if a <= merged[-1][1]:  # çakışan/bitişik segmentleri birleştir
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    cuts: list[Cut] = []
    out_frames = 0
    for a, b in merged:
        cuts.append(Cut(a / fps, b / fps, out_frames / fps))
        out_frames += b - a
    return cuts


def _map_point(cuts: list[Cut], t: float, snap: float = SNAP_TOLERANCE) -> float | None:
    """Ham zaman -> çıktı zamanı. Kesilmiş bölgede ve sınıra uzaksa None."""
    for cut in cuts:
        if cut.src_start - _EPS <= t <= cut.src_end + _EPS:
            return cut.out_start + _clamp(t - cut.src_start, 0.0, cut.length)

    # Kesilen boşluktaysa: her boşluk çıktıda tek bir noktaya (birleşme) çöker.
    if t < cuts[0].src_start:
        return 0.0 if cuts[0].src_start - t <= snap else None
    if t > cuts[-1].src_end:
        return cuts[-1].out_end if t - cuts[-1].src_end <= snap else None
    for prev, nxt in zip(cuts, cuts[1:]):
        if prev.src_end < t < nxt.src_start:
            near = min(t - prev.src_end, nxt.src_start - t)
            return prev.out_end if near <= snap else None
    return None


def _map_range(cuts: list[Cut], a: float, b: float) -> list[tuple[float, float]]:
    pieces: list[tuple[float, float]] = []
    for cut in cuts:
        lo, hi = max(a, cut.src_start), min(b, cut.src_end)
        if hi - lo <= _EPS:
            continue
        o_lo = cut.out_start + (lo - cut.src_start)
        o_hi = cut.out_start + (hi - cut.src_start)
        if pieces and abs(pieces[-1][1] - o_lo) <= _EPS:
            pieces[-1] = (pieces[-1][0], o_hi)
        else:
            pieces.append((o_lo, o_hi))
    return pieces


def _normalize_track(name: str) -> str:
    return PurePath(name.strip()).stem.lower()


def build_timeline(
    edl: EditDecisionList,
    source_duration: float,
    fps: int,
    available_sfx: Collection[str],
    available_bgm: Collection[str],
) -> Timeline:
    warnings: list[str] = []
    cuts = _build_cuts(edl, source_duration, fps, warnings)
    total = cuts[-1].out_end
    min_len = 1.0 / fps

    zooms: list[ZoomWindow] = []
    for z in edl.zoom_effects:
        if not _finite(z.start, z.end, z.scale) or z.end <= z.start:
            warnings.append(f"Geçersiz zoom atlandı: {z.start}-{z.end}")
            continue
        scale = _clamp(z.scale, ZOOM_MIN, ZOOM_MAX)
        if scale <= ZOOM_MIN + 1e-3:
            continue
        pieces = [(a, b) for a, b in _map_range(cuts, z.start, z.end) if b - a >= min_len]
        if not pieces:
            warnings.append(f"Zoom kesilen bölgede kaldı, atlandı: {z.start:.2f}-{z.end:.2f}")
        zooms.extend(ZoomWindow(a, b, scale) for a, b in pieces)
    zooms.sort(key=lambda w: w.start)

    sfx_names = {s.lower() for s in available_sfx}
    placed: list[PlacedSfx] = []
    for ev in edl.sfx_events:
        kind = ev.type.lower()
        if kind not in sfx_names:
            warnings.append(f"Bilinmeyen SFX '{ev.type}' atlandı.")
            continue
        if not _finite(ev.timestamp, ev.volume):
            warnings.append(f"Geçersiz SFX atlandı: {ev}")
            continue
        t = _map_point(cuts, ev.timestamp)
        if t is None or t >= total - min_len:
            warnings.append(f"SFX '{kind}' @ {ev.timestamp:.2f}s kesilen bölgede, atlandı.")
            continue
        vol = _clamp(ev.volume, 0.0, SFX_VOLUME_MAX)
        if vol > 0:
            placed.append(PlacedSfx(round(t, 3), kind, vol))
    placed.sort(key=lambda s: s.time)

    bgm: BgmPlan | None = None
    track = _normalize_track(edl.bgm.track) if edl.bgm.track else ""
    bgm_names = {_normalize_track(b) for b in available_bgm}
    if track and track != "none":
        if track not in bgm_names:
            warnings.append(f"BGM '{edl.bgm.track}' kütüphanede yok; müziksiz devam.")
        elif _finite(edl.bgm.volume) and edl.bgm.volume > 0:
            fade = edl.bgm.fade_out_last_seconds if _finite(edl.bgm.fade_out_last_seconds) else 0.0
            bgm = BgmPlan(
                track=track,
                volume=_clamp(edl.bgm.volume, 0.0, BGM_VOLUME_MAX),
                fade_out=_clamp(fade, 0.0, min(BGM_FADE_MAX, total)),
            )

    return Timeline(cuts=cuts, zooms=zooms, sfx=placed, bgm=bgm, duration=total, warnings=warnings)


# --------------------------------------------------------------------------
# Kurgulanmış video görünümü (iteratif düzenleme için)
#
# Kullanıcı sonucu izleyerek "3. saniyedeki sesi kaldır" der; bu, ÇIKTI zamanıdır.
# Gemini'ye EDL'i çıktı zamanında gösterir, dönen sonucu buradan ham zamana
# çeviririz. Böylece zaman aritmetiği LLM'e kalmaz.
# --------------------------------------------------------------------------

_ND = 4


def _r(v: float) -> float:
    return round(v, _ND)


def _view(summary: str, tl: Timeline, keep: list[tuple[float, float]]) -> EditDecisionList:
    return EditDecisionList(
        summary=summary,
        keep_segments=[Segment(start=_r(a), end=_r(b)) for a, b in keep],
        zoom_effects=[ZoomEffect(start=_r(z.start), end=_r(z.end), scale=_r(z.scale)) for z in tl.zooms],
        sfx_events=[SfxEvent(timestamp=_r(s.time), type=s.type, volume=_r(s.volume)) for s in tl.sfx],
        bgm=BgmSettings(track=tl.bgm.track, volume=_r(tl.bgm.volume), fade_out_last_seconds=_r(tl.bgm.fade_out))
        if tl.bgm else BgmSettings(track="none", volume=0.0, fade_out_last_seconds=0.0),
    )


def to_edited_view(edl: EditDecisionList, tl: Timeline) -> EditDecisionList:
    """Temizlenmiş EDL'in çıktı zamanındaki karşılığı: kesimler birleşmiş tek parça [0, süre]."""
    return _view(edl.summary, tl, [(0.0, tl.duration)])


def sanitize_view(view: EditDecisionList, duration: float, fps: int,
                  available_sfx: Collection[str], available_bgm: Collection[str]) -> EditDecisionList:
    """Çıktı-zamanı EDL'ini (Gemini'den gelen) aralık/tür/hacim açısından temizler; zamanları kaydırmaz."""
    base = build_timeline(view.model_copy(update={"keep_segments": []}), duration, fps,
                          available_sfx, available_bgm)
    kept = _build_cuts(view, duration, fps, [])
    return _view(view.summary, base, [(c.src_start, c.src_end) for c in kept])


def _out_to_src(cuts: list[Cut], a: float, b: float) -> list[tuple[float, float]]:
    pieces = []
    for c in cuts:
        lo, hi = max(a, c.out_start), min(b, c.out_end)
        if hi - lo > _EPS:
            pieces.append((c.src_start + lo - c.out_start, c.src_start + hi - c.out_start))
    return pieces


def from_edited_view(view: EditDecisionList, tl: Timeline, summary: str) -> EditDecisionList:
    """Çıktı-zamanı (temizlenmiş) EDL -> ham video zamanı EDL. `tl` düzenleme ÖNCESİ zaman çizelgesidir."""
    cuts = tl.cuts

    def seg(a: float, b: float) -> list[Segment]:
        return [Segment(start=_r(s), end=_r(e)) for s, e in _out_to_src(cuts, a, b)]

    keep = [p for s in view.keep_segments for p in seg(s.start, s.end)] or \
           [Segment(start=_r(c.src_start), end=_r(c.src_end)) for c in cuts]
    zooms = [ZoomEffect(start=p.start, end=p.end, scale=z.scale)
             for z in view.zoom_effects for p in seg(z.start, z.end)]
    sfx = []
    for ev in view.sfx_events:
        for c in cuts:  # birleşme noktasında sonraki parçanın başına yerleşir
            if c.out_start - _EPS <= ev.timestamp < c.out_end - _EPS:
                sfx.append(SfxEvent(timestamp=_r(c.src_start + ev.timestamp - c.out_start),
                                    type=ev.type, volume=ev.volume))
                break
    return EditDecisionList(summary=summary, keep_segments=keep, zoom_effects=zooms,
                            sfx_events=sfx, bgm=view.bgm)
