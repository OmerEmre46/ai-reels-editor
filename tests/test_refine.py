"""refine_edl: Gemini'ye video gitmez; çıktı-zamanı EDL'i ham zamana doğru döner."""

from types import SimpleNamespace

import pytest

from models.edl import EditDecisionList
import config
from services import ai_director
from services.ai_director import AIDirector, refine_edl
from services.edl_diff import describe_changes

CURRENT = EditDecisionList.model_validate({
    "summary": "orijinal özet",
    "keep_segments": [{"start": 0, "end": 2.5}, {"start": 4, "end": 7}, {"start": 8, "end": 10}],
    "zoom_effects": [{"start": 6, "end": 9, "scale": 1.2}],
    "sfx_events": [{"timestamp": 4.0, "type": "whoosh", "volume": 0.8},
                   {"timestamp": 6.5, "type": "riser", "volume": 0.6}],
    "bgm": {"track": "upbeat", "volume": 0.2, "fade_out_last_seconds": 1.5},
})


class FakeModels:
    def __init__(self, transform):
        self.transform, self.calls = transform, []

    def generate_content(self, *, model, contents, config):
        self.calls.append((model, contents, config))
        import json
        sent = json.loads(contents.split("CURRENT EDL:\n")[1].split("\n\nAUDIO HINTS")[0])
        out = self.transform(sent)
        return SimpleNamespace(parsed=config.response_schema.model_validate(out), text=None, candidates=[])


def director(transform):
    models = FakeModels(transform)
    return AIDirector(client=SimpleNamespace(models=models, files=None)), models


def test_prompt_shows_edited_timeline_and_never_the_video():
    d, models = director(lambda e: e)
    d.refine(CURRENT, "müziği kıs", 10.0)
    model, contents, cfg = models.calls[0]
    assert model == config.GEMINI_MODEL
    assert isinstance(contents, str)  # video part yok
    assert '"end": 7.5' in contents and "müziği kıs" in contents  # çıktı zamanı süresi
    assert "7.50 s" in cfg.system_instruction and cfg.temperature == 0


def test_no_change_reports_unchanged_and_keeps_edl():
    d, _ = director(lambda e: e)
    r = d.refine(CURRENT, "asdf", 10.0)
    assert r.changed is False and r.changes == [] and r.edl == CURRENT


def test_remove_sfx_zoom_and_music_volume():
    def transform(e):
        e["sfx_events"] = [x for x in e["sfx_events"] if x["type"] != "whoosh"]
        e["zoom_effects"].append({"start": 5.0, "end": 6.0, "scale": 1.3})
        e["bgm"]["volume"] = 0.12
        return e

    d, _ = director(transform)
    r = d.refine(CURRENT, "whoosh'u kaldır, 5-6 arası zoom, müziği kıs", 10.0)
    assert r.changed
    assert [e.type for e in r.edl.sfx_events] == ["riser"]
    assert r.edl.bgm.volume == pytest.approx(0.12)
    assert r.edl.summary == "orijinal özet"
    # çıktı 5.0-6.0 sn => kaynak 7.0'ı aşar: [4,7] parçasının çıktı aralığı 2.5-5.5, sonrası [8,10]
    assert (5.0 - 2.5 + 4, 7.0) in [(z.start, z.end) for z in r.edl.zoom_effects]  # (6.5, 7.0)
    assert (8.0, 8.5) in [(z.start, z.end) for z in r.edl.zoom_effects]
    assert any("SFX kaldırıldı: whoosh" in c for c in r.changes)
    assert any("Müzik sesi" in c for c in r.changes)


def test_trim_last_two_seconds_via_module_function():
    d, _ = director(lambda e: {**e, "keep_segments": [{"start": 0, "end": 5.5}]})
    edl = refine_edl(CURRENT, "son 2 saniyeyi kes", 10.0, director=d)
    assert [(s.start, s.end) for s in edl.keep_segments] == [(0, 2.5), (4, 7)]


def test_out_of_range_request_is_a_noop():
    def transform(e):
        e["sfx_events"].append({"timestamp": 30.0, "type": "pop", "volume": 1.0})
        return e

    d, _ = director(transform)
    assert d.refine(CURRENT, "30. saniyeye pop ekle", 10.0).changed is False


def test_describe_changes_pairs_events():
    old = CURRENT
    new = old.model_copy(deep=True)
    new.sfx_events[0].type = "ding"
    new.sfx_events[1].volume = 1.0
    notes = describe_changes(old, new, 10.0)
    assert "SFX değişti (4.0 sn): whoosh → ding" in notes
    assert any(n.startswith("SFX sesi (riser") for n in notes)


def test_audio_hints_are_mapped_to_edited_time():
    d, models = director(lambda e: e)
    # ham zaman sessizlikleri; 2. parça [4,7] çıktıda 2.5-5.5, 3. parça [8,10] çıktıda 5.5-7.5
    d.refine(CURRENT, "boşlukları kes", 10.0, silences=[(0.5, 1.5), (4.2, 4.9), (5.0, 5.8), (8.2, 9.0)])
    contents = models.calls[0][1]
    assert "SILENT_RANGES: [[0.5, 1.5], [2.7, 3.4], [3.5, 4.3], [5.7, 6.5]]" in contents
    assert "PHRASE_STARTS: [1.5, 3.4, 4.3, 6.5]" in contents


def test_manual_diff_reports_only_new_cuts_and_restores():
    def edl(segs):
        return EditDecisionList.model_validate(
            {**CURRENT.model_dump(), "keep_segments": [{"start": a, "end": b} for a, b in segs]})

    old = edl([(0, 2.5), (4, 7), (8, 10)])         # boşluklar: 2.5-4 ve 7-8 (zaten kesik)
    new = edl([(0, 2.5), (4, 6), (9, 10)])         # yeni kesik: 6-7 hariç 7-9; 7-8 zaten kesikti; 8-9 yeni
    notes = describe_changes(old, new, 10.0, "sn (ham)")
    assert notes == ["Kesildi: 6.0-7.0 sn (ham) arası", "Kesildi: 8.0-9.0 sn (ham) arası"]
    back = describe_changes(new, old, 10.0)
    assert back == ["Geri eklendi: 6.0-7.0 sn arası", "Geri eklendi: 8.0-9.0 sn arası"]
    # boş keep_segments = tüm video: önceki kesikler "yeni kesildi" sayılmamalı
    assert describe_changes(edl([]), edl([(0, 10)]), 10.0) == []
