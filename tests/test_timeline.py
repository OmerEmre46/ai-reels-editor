import pytest

from models.edl import EditDecisionList
from services.timeline import SNAP_TOLERANCE, build_timeline

SFX = ["whoosh", "pop", "ding", "riser", "bass_drop"]
BGM = ["upbeat", "chill", "dramatic"]


def make_edl(segments=(), zooms=(), sfx=(), track="none", volume=0.2, fade=1.0) -> EditDecisionList:
    return EditDecisionList.model_validate({
        "summary": "t",
        "keep_segments": [{"start": a, "end": b} for a, b in segments],
        "zoom_effects": [{"start": a, "end": b, "scale": s} for a, b, s in zooms],
        "sfx_events": [{"timestamp": t, "type": k, "volume": v} for t, k, v in sfx],
        "bgm": {"track": track, "volume": volume, "fade_out_last_seconds": fade},
    })


def tl(edl, duration=10.0):
    return build_timeline(edl, duration, 30, SFX, BGM)


def test_cuts_are_remapped_and_sorted():
    t = tl(make_edl([(8, 10), (0, 2.5), (4, 7)]))
    assert [(c.src_start, c.src_end, c.out_start) for c in t.cuts] == [
        (0, 2.5, 0), (4, 7, 2.5), (8, 10, 5.5)]
    assert t.duration == pytest.approx(7.5)
    assert t.map_point(4.5) == pytest.approx(3.0)
    assert t.map_point(9.0) == pytest.approx(6.5)


def test_overlapping_segments_merge_and_clamp():
    t = tl(make_edl([(-1, 3), (2, 5), (9, 42)]))
    assert [(c.src_start, c.src_end) for c in t.cuts] == [(0, 5), (9, 10)]
    assert t.duration == pytest.approx(6.0)


def test_segments_snap_to_frame_grid():
    t = tl(make_edl([(1.01, 2.02)]))
    c = t.cuts[0]
    assert c.src_start * 30 == pytest.approx(round(c.src_start * 30))
    assert c.src_end * 30 == pytest.approx(round(c.src_end * 30))


def test_empty_or_invalid_segments_keep_full_video():
    assert tl(make_edl([])).duration == pytest.approx(10.0)
    t = tl(make_edl([(5, 4), (3, 3.02)]))
    assert t.duration == pytest.approx(10.0)
    assert t.warnings


def test_zoom_across_cut_is_split_then_merged():
    t = tl(make_edl([(0, 2.5), (4, 7), (8, 10)], zooms=[(6, 9, 1.2)]))
    # kaynak 6-7 -> çıktı 4.5-5.5, kaynak 8-9 -> çıktı 5.5-6.5: bitişik => tek pencere
    assert len(t.zooms) == 1
    assert (t.zooms[0].start, t.zooms[0].end) == pytest.approx((4.5, 6.5))


def test_zoom_in_removed_region_is_dropped_and_scale_clamped():
    t = tl(make_edl([(0, 2), (5, 10)], zooms=[(2.5, 4.5, 1.3), (6, 7, 9.0), (6, 7, 0.5)]))
    assert len(t.zooms) == 1
    assert t.zooms[0].scale == 2.0


def test_sfx_remap_snap_and_drop():
    t = tl(make_edl([(0, 2), (5, 10)], sfx=[
        (1.0, "pop", 1.0),                         # tutulan bölge
        (6.0, "ding", 0.5),                         # 6 -> 3
        (2.0 + SNAP_TOLERANCE / 2, "whoosh", 1),    # boşluğun başına yakın -> birleşme (2.0)
        (3.5, "riser", 1),                          # boşluğun ortası -> atılır
        (1.0, "laser", 1),                          # bilinmeyen tür -> atılır
        (4.0, "bass_drop", 5.0),                    # hacim sınırlanır; 4.0 boşlukta, sınıra 1s -> atılır
    ]))
    placed = [(s.time, s.type) for s in t.sfx]
    assert placed == [(1.0, "pop"), (2.0, "whoosh"), (3.0, "ding")]
    assert len(t.warnings) == 3


def test_bgm_resolution():
    assert tl(make_edl(track="Upbeat.mp3")).bgm.track == "upbeat"
    assert tl(make_edl(track="none")).bgm is None
    missing = tl(make_edl(track="jazz"))
    assert missing.bgm is None and missing.warnings
    fade = tl(make_edl([(0, 3)], track="chill", fade=99)).bgm
    assert fade.fade_out == pytest.approx(3.0)


# --- kurgulanmış video görünümü (iteratif düzenleme) -----------------------
from services.timeline import from_edited_view, sanitize_view, to_edited_view  # noqa: E402

CUT_EDL = dict(segments=[(0, 2.5), (4, 7), (8, 10)],
               zooms=[(6, 9, 1.2)], sfx=[(4.0, "ding", 0.7), (6.5, "riser", 0.6)],
               track="upbeat", volume=0.2, fade=1.5)


def cut_case():
    edl = make_edl(**CUT_EDL)
    t = tl(edl)
    return edl, t, to_edited_view(edl, t)


def upd(view, **kw):
    return EditDecisionList.model_validate({**view.model_dump(), **kw})


def san(view, t):
    return sanitize_view(view, t.duration, 30, SFX, BGM)


def test_edited_view_is_in_output_time():
    _, t, view = cut_case()
    assert [(s.start, s.end) for s in view.keep_segments] == [(0.0, 7.5)]
    assert [(s.timestamp, s.type) for s in view.sfx_events] == [(2.5, "ding"), (5.0, "riser")]
    assert [(z.start, z.end) for z in view.zoom_effects] == [(4.5, 6.5)]
    assert view.bgm.track == "upbeat"


def test_identity_round_trip_preserves_cuts():
    edl, t, view = cut_case()
    back = from_edited_view(san(view, t), t, edl.summary)
    assert [(s.start, s.end) for s in back.keep_segments] == [(0, 2.5), (4, 7), (8, 10)]
    assert [(e.timestamp, e.type) for e in back.sfx_events] == [(4.0, "ding"), (6.5, "riser")]
    assert tl(back).duration == pytest.approx(7.5)


def test_output_time_edits_map_back_to_source_time():
    edl, t, view = cut_case()
    view = upd(view,
               keep_segments=[{"start": 0, "end": 5.5}],  # "son 2 saniyeyi kes"
               zoom_effects=[*view.model_dump()["zoom_effects"], {"start": 3.0, "end": 4.0, "scale": 1.3}],
               sfx_events=[e.model_dump() for e in view.sfx_events if e.type != "ding"])  # "ding'i kaldır"
    back = from_edited_view(san(view, t), t, "s")
    assert [(s.start, s.end) for s in back.keep_segments] == [(0, 2.5), (4, 7)]
    assert (4.5, 5.5) in [(z.start, z.end) for z in back.zoom_effects]
    assert [e.type for e in back.sfx_events] == ["riser"]
    assert tl(back).duration == pytest.approx(5.5)


def test_middle_cut_and_range_spanning_a_join():
    edl, t, view = cut_case()
    span = upd(view, keep_segments=[{"start": 0, "end": 1}, {"start": 2, "end": 7.5}],
               zoom_effects=[{"start": 2.0, "end": 3.0, "scale": 1.2}])
    back = from_edited_view(san(span, t), t, "s")
    assert [(s.start, s.end) for s in back.keep_segments] == [(0, 1), (2, 2.5), (4, 7), (8, 10)]
    assert [(z.start, z.end) for z in back.zoom_effects] == [(2, 2.5), (4, 4.5)]


def test_sanitize_drops_out_of_range_and_clamps():
    _, t, view = cut_case()
    bad = upd(view, keep_segments=[{"start": -3, "end": 99}],
              sfx_events=[{"timestamp": 50, "type": "pop", "volume": 1}],
              zoom_effects=[{"start": 6, "end": 30, "scale": 9}])
    clean = san(bad, t)
    assert [(s.start, s.end) for s in clean.keep_segments] == [(0, 7.5)]
    assert clean.sfx_events == []
    assert (clean.zoom_effects[0].end, clean.zoom_effects[0].scale) == (7.5, 2.0)
