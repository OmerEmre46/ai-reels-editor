# Reels AI Editor — Çekirdek Motor

Ham videoyu Gemini ile analiz edip bir **Edit Decision List (EDL)** üretir, EDL'i deterministik bir FFmpeg boru hattıyla 1080x1920 Reels videosuna çevirir.

```
video ──> ai_director (Gemini 2.5 Flash, response_schema) ──> EDL (JSON)
                                                              │
video + EDL ──> timeline (temizlik + zaman haritalama) ──> video_renderer (tek ffmpeg geçişi) ──> MP4
```

## Kurulum

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # GEMINI_API_KEY girin
.venv\Scripts\python scripts\generate_assets.py
```

FFmpeg ≥ 7 (libx264 + libmp3lame) PATH'te olmalı.

## Çalıştırma

```bash
.venv\Scripts\python -m uvicorn main:app --reload
```

| Uç | Açıklama |
|---|---|
| `POST /jobs` | `video` (mp4/mov/m4v/webm) + isteğe bağlı `edl` (JSON). `edl` yoksa Gemini analiz eder. |
| `GET /jobs/{id}` | `queued → analyzing → rendering → done/failed`, EDL ve uyarılar |
| `GET /jobs/{id}/video` | Bitmiş MP4 |
| `GET /health` | ffmpeg, API anahtarı ve asset kütüphanesi |

## EDL zaman kuralı

EDL'deki **tüm** zamanlar ham videoya göredir. `services/timeline.py`:

- Segmentleri sıralar, birleştirir, sınırlar ve kare ızgarasına oturtur (kesimlerde A/V kayması birikmez).
- Zoom'ları çıktı zamanına taşır; kesimi aşan zoom bölünür, çıktıda bitişik kalan parçalar yeniden birleştirilir.
- Kesilen bölgeye düşen SFX, kesime 0.3 sn'den yakınsa birleşme noktasına taşınır, değilse atılır (uyarı olarak raporlanır).

## Ses dosyaları

`assets/sfx/` ve `assets/bgm/` içindeki her `.mp3/.wav/.m4a/...` dosyası adıyla (uzantısız, küçük harf) kullanılabilir hale gelir ve Gemini şemasına **enum olarak** girer. Yani yeni bir ses eklemek kod değişikliği gerektirmez. `generate_assets.py` var olan dosyaların üzerine yazmaz.

## Test

```bash
.venv\Scripts\python -m pytest            # birim + ffmpeg entegrasyon + API testleri
.venv\Scripts\python scripts\smoke_test.py  # uploads/test_input.mp4 -> outputs/test_output.mp4
```

## İteratif kurgu (projeler)

Ham video Gemini'ye yalnızca `create` sırasında gider. Sonraki her küçük istekte yalnızca **mevcut EDL (metin)** Gemini'ye gönderilir; FFmpeg yeni sürümü saniyeler içinde basar.

| Uç | Açıklama |
|---|---|
| `POST /api/projects/create` | `video` + `instruction` (isteğe bağlı ilk istek) → Gemini EDL → render **v1** |
| `POST /api/projects/{id}/edit` | `{"instruction": "3. saniyedeki whoosh'u kaldır"}` → `refine_edl` → render **vN+1** |
| `POST /api/projects/{id}/manual-edl` | Gövde: EDL JSON (yanıtlardaki `edl` ile aynı biçim) → doğrudan render |
| `GET /api/projects/{id}` | Güncel durum + tüm sürümlerin geçmişi |
| `GET /api/projects/{id}/video?version=N` | İstenen sürümün MP4'ü (varsayılan: güncel) |

Yanıt: `video_url`, `edl`, `duration`, `changes` (ne değiştiği, Türkçe), `warnings`, `changed`.

**İki zaman uzayı vardır:**
- `edit` isteklerindeki saniyeler, kullanıcının izlediği **kurgulanmış videonun** saniyeleridir ("son 2 saniyeyi kes", "4-6 arası zoom").
- Yanıtlardaki `edl` ve `manual-edl` gövdesi **ham video** zamanındadır (kesimlerden önce).

`refine_edl` Gemini'ye EDL'i kurgulanmış zamanda gösterir ve dönen sonucu kod ile ham zamana çevirir (`services/timeline.py`: `to_edited_view` / `from_edited_view`); zaman aritmetiği LLM'e bırakılmaz. "Ne değişti" bilgisi de LLM'e sorulmaz, iki EDL karşılaştırılarak hesaplanır (`services/edl_diff.py`). Hiçbir değişiklik çıkmazsa (`changed: false`) yeni sürüm/render oluşturulmaz.

Kısıtlar: kesilen içeriği geri getirmek `edit` ile mümkün değildir (kurgulanmış videoda o kısım yoktur); önceki sürüm `GET .../video?version=N` ile alınabilir ya da `manual-edl` ile ham zamanda geri eklenebilir. Aynı projede eşzamanlı iki istek `409` döner. Proje durumu `projects/{id}.json` içinde diskte tutulur (sunucu yeniden başlasa kaybolmaz).

## Gemini modeli

Model `GEMINI_MODEL` ile seçilir (varsayılan `gemini-3-flash-preview`; `gemini-2.5-flash` bu anahtarda kapalı). Google bu modeli **yeni kullanıcılara kapatmış** olabilir (404 `no longer available to new users`); o durumda `.env` içine başka bir model yazın. Gemini çağrılarına zaman aşımı (`GEMINI_ANALYZE_TIMEOUT_SECONDS`=150, `GEMINI_REFINE_TIMEOUT_SECONDS`=45) ve 2 deneme uygulanır; `503` (yoğunluk) durumunda API `502` döner.

## Web arayüzü (AI Reels Kurgu Stüdyosu)

```bash
.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Tarayıcıdan `http://localhost:8000`. Statik dosyalar `web/` altındadır (derleme adımı yok).

- **Sol panel:** video sürükle-bırak + ilk komut, sohbet geçmişi (yapılan değişikliklerin özeti), hızlı aksiyonlar, **Geri Al** ve sürüm seçici (`v1…vN`, seçilen sürüm önizlenir; "Bu sürümden devam et" ile o sürüme dönülür).
- **Sağ panel:** 9:16 oynatıcı (Kurgulanmış / Orijinal Ham sekmeleri), zaman çizelgesi ve **Aktif Kurgu Katmanları**. Kesim/zoom/SFX çipindeki × ile silme veya müzik parçası/ses kaydırıcısı `POST /manual-edl` ile yeni sürüm üretir. Sağ üstte **Reels Olarak İndir (.mp4)**.
- Açık proje adres çubuğunda `#p=<id>` olarak tutulur; sayfa yenilense de sürüm geçmişi korunur.

**Geri alma:** `POST /api/projects/{id}/revert` gövde `{"version": N}` (boşsa bir önceki sürüm). Geçmiş silinmez: hedef sürüm yeni bir sürüm olarak kopyalanır, bu yüzden "geri al"ı geri almak da mümkündür.

**Sesli ipuçları:** "Boşlukları Kes" ve "Vurgulara Zoom + SFX Ekle" gibi istekler için video Gemini'ye yeniden gönderilmez. Yerel FFmpeg `silencedetect` sessiz aralıkları ve konuşmanın yeniden başladığı anları çıkarır, kurgulanmış zamana çevirip `refine` isteğine ekler.

## Tek tıkla başlatma (Windows)

`baslat.bat` dosyasına çift tıklayın: sunucuyu başlatır ve hazır olunca tarayıcıda `http://localhost:8000` adresini açar. Pencereyi kapatmak sunucuyu durdurur. Sunucu zaten açıksa yalnızca tarayıcıyı açar. İlk çalıştırmada `.venv` yoksa bağımlılıkları kurar, `.env` yoksa `.env.example`'dan oluşturup API anahtarını girmeniz için açar. Gereksinim: Python 3 ve FFmpeg (`winget install Gyan.FFmpeg`).

## Git

Bu klasör bağımsız bir git deposudur (üst dizinin deposundan ayrı). `.env`, `.venv/`, `uploads/`, `outputs/`, `projects/` ve ses dosyaları (`assets/sfx`, `assets/bgm`) izlenmez. Test seslerini yeniden üretmek için: `python scripts/generate_assets.py`.
