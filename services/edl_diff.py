"""İki EDL (aynı zaman uzayında) arasındaki farkı kullanıcıya gösterilecek Türkçe maddelere çevirir.

Gemini'nin ne yaptığını LLM'e sormak yerine deterministik hesaplanır; böylece
"istek anlaşılmadı, hiçbir şey değişmedi" durumu da güvenilir şekilde yakalanır.
"""

from __future__ import annotations

from models.edl import EditDecisionList

TOL = 0.05  # sn; bu kadar yakın zamanlar aynı olay sayılır


def _gaps(segments, total: float) -> list[tuple[float, float]]:
    if not segments:  # boş liste = tüm video korunur
        return []
    out, pos = [], 0.0
    for s in sorted(segments, key=lambda s: s.start):
        if s.start - pos > TOL:
            out.append((pos, s.start))
        pos = max(pos, s.end)
    if total - pos > TOL:
        out.append((pos, total))
    return out


def _subtract(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """a aralıklarından b aralıklarını çıkarır."""
    out = []
    for lo, hi in a:
        pieces = [(lo, hi)]
        for blo, bhi in b:
            nxt = []
            for x, y in pieces:
                if bhi <= x or blo >= y:
                    nxt.append((x, y))
                    continue
                if blo > x:
                    nxt.append((x, blo))
                if bhi < y:
                    nxt.append((bhi, y))
            pieces = nxt
        out += [(x, y) for x, y in pieces if y - x > TOL]
    return out


def _pair(old: list, new: list, same) -> tuple[list[tuple], list, list]:
    """Eşleşenler, kaldırılanlar, eklenenler."""
    matched, removed, free = [], [], list(new)
    for o in old:
        m = next((n for n in free if same(o, n)), None)
        if m is None:
            removed.append(o)
        else:
            free.remove(m)
            matched.append((o, m))
    return matched, removed, free


def describe_changes(old: EditDecisionList, new: EditDecisionList, total: float, unit: str = "sn") -> list[str]:
    """`old`/`new` aynı zaman uzayında; `total` o uzaydaki süre. `unit`: kullanıcıya gösterilen birim
    (kurgulanmış zaman için "sn", ham zaman için "sn (ham)")."""
    notes: list[str] = []
    old_gaps, new_gaps = _gaps(old.keep_segments, total), _gaps(new.keep_segments, total)

    for a, b in _subtract(new_gaps, old_gaps):
        notes.append(f"Kesildi: {a:.1f}-{b:.1f} {unit} arası")
    for a, b in _subtract(old_gaps, new_gaps):
        notes.append(f"Geri eklendi: {a:.1f}-{b:.1f} {unit} arası")

    m, rem, add = _pair(old.sfx_events, new.sfx_events, lambda o, n: abs(o.timestamp - n.timestamp) <= TOL)
    for o, n in m:
        if o.type != n.type:
            notes.append(f"SFX değişti ({o.timestamp:.1f} {unit}): {o.type} → {n.type}")
        if abs(o.volume - n.volume) > 0.01:
            notes.append(f"SFX sesi ({n.type}, {n.timestamp:.1f} {unit}): {o.volume:.2f} → {n.volume:.2f}")
    notes += [f"SFX kaldırıldı: {o.type} ({o.timestamp:.1f} {unit})" for o in rem]
    notes += [f"SFX eklendi: {n.type} ({n.timestamp:.1f} {unit})" for n in add]

    m, rem, add = _pair(old.zoom_effects, new.zoom_effects,
                        lambda o, n: abs(o.start - n.start) <= TOL and abs(o.end - n.end) <= TOL)
    for o, n in m:
        if abs(o.scale - n.scale) > 0.01:
            notes.append(f"Zoom ({n.start:.1f}-{n.end:.1f} {unit}): x{o.scale:.2f} → x{n.scale:.2f}")
    notes += [f"Zoom kaldırıldı: {o.start:.1f}-{o.end:.1f} {unit}" for o in rem]
    notes += [f"Zoom eklendi: {n.start:.1f}-{n.end:.1f} {unit} (x{n.scale:.2f})" for n in add]

    ob, nb = old.bgm, new.bgm
    if ob.track != nb.track:
        notes.append(f"Müzik: {ob.track} → {nb.track}")
    if nb.track != "none":
        if abs(ob.volume - nb.volume) > 0.005:
            notes.append(f"Müzik sesi: {ob.volume:.2f} → {nb.volume:.2f}")
        if abs(ob.fade_out_last_seconds - nb.fade_out_last_seconds) > 0.05:
            notes.append(f"Müzik fade-out: {ob.fade_out_last_seconds:.1f} → {nb.fade_out_last_seconds:.1f} sn")
    return notes
