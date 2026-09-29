"""Edit Decision List (EDL) şeması.

Bu model hem Gemini'nin `response_schema`'sı olarak hem de render motorunun
girdi sözleşmesi olarak kullanılır. Gemini şema dönüştürücüsüyle uyumlu kalsın
diye burada sayısal sınır (ge/le) yok; aralık kontrolü ve temizlik
`services/timeline.py` içinde deterministik olarak yapılır.

ZAMAN KURALI: EDL'deki TÜM zaman damgaları (keep_segments, zoom_effects,
sfx_events) HAM (kesilmemiş) videonun saniyeleridir. Kesim sonrası çıktı
zaman çizelgesine yeniden haritalama render motorunun işidir.

SFX `type` ve BGM `track` burada serbest `str`'dir; Gemini'ye giderken
`ai_director` bunları assets/ klasöründeki dosya adlarından üretilen enum'lara
daraltır. Böylece yeni bir ses dosyası eklemek kod değişikliği gerektirmez.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Segment(BaseModel):
    start: float = Field(description="Segment start, seconds in the ORIGINAL video.")
    end: float = Field(description="Segment end, seconds in the ORIGINAL video.")


class ZoomEffect(BaseModel):
    start: float = Field(description="Zoom start, seconds in the ORIGINAL video.")
    end: float = Field(description="Zoom end, seconds in the ORIGINAL video.")
    scale: float = Field(description="Center zoom factor, 1.05 (subtle) to 1.5 (strong punch-in).")


class SfxEvent(BaseModel):
    timestamp: float = Field(description="Moment the sound starts, seconds in the ORIGINAL video.")
    type: str = Field(description="Sound effect name from the provided list.")
    volume: float = Field(description="Linear gain, 0.1 to 1.5 (1.0 = unchanged).")


class BgmSettings(BaseModel):
    track: str = Field(description="Background music track name from the provided list, or 'none'.")
    volume: float = Field(description="Linear gain for the music, typically 0.05 to 0.3 under speech.")
    fade_out_last_seconds: float = Field(description="Fade-out length at the end of the edit, seconds.")


class EditDecisionList(BaseModel):
    summary: str = Field(description="Short description of the video and the editing choices.")
    keep_segments: list[Segment] = Field(description="Parts of the ORIGINAL video to keep, in chronological order.")
    zoom_effects: list[ZoomEffect] = Field(description="Emphasis zooms.")
    sfx_events: list[SfxEvent] = Field(description="Sound effects at key moments.")
    bgm: BgmSettings
