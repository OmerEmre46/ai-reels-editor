# AI Reels Editor

**Ham videoyu bırakın, kurguyu sade bir dille anlatın, 9:16 bir Instagram Reels alın.**
Kurguyu Gemini planlar; deterministik bir FFmpeg hattı render eder. *"3. saniyedeki whoosh'u kaldır"* ya da *"son 2 saniyeyi kes"* gibi devam istekleri planı günceller ve videoyu yeniden yüklemeden saniyeler içinde yeniden render eder.

[![Lisans: MIT](https://img.shields.io/badge/lisans-MIT-green.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)

🇬🇧 English documentation: [README.md](README.md)

## Özellikler

- **Yapay zeka ile ilk kurgu:** Gemini videoyu izler ve yapılandırılmış bir **Kurgu Karar Listesi (EDL)** döndürür: tutulacak kesimler, zoom vurguları, ses efektleri ve arka plan müziği.
- **Deterministik render:** tek FFmpeg geçişinde kesimler, merkez zoom (yumuşak ya da anlık), ses efektleri (`adelay`, milisaniye hassasiyetli) ve döngülenen/kısılan arka plan müziği (`amix`) uygulanır; çıktı 1080×1920 H.264/AAC.
- **Doğal dille iteratif düzenleme:** küçük istekler yalnızca EDL'i (metin) değiştirir; hızlıdır ve kurgunun geri kalanını bozmaz.
- **Zaman uzayı bilinci:** siz izlediğiniz kurgulanmış videoya göre konuşursunuz; uygulama bu zamanları ham videoya çevirir.
- **Sürümleme:** her değişiklik yeni bir sürümdür (`v1…vN`); geri alabilir ya da istediğiniz sürüme dönebilirsiniz.
- **Web stüdyosu:** sürükle-bırak yükleme, sohbet asistanı, hızlı aksiyonlar, 9:16 önizleme (kurgulanmış / orijinal), tek tıkla silinebilen katman zaman çizelgesi, müzik ses kaydırıcısı, indirme butonu.
- Arayüzün yaptığı her şey için **REST API**.

## Nasıl çalışır?

```
video ──► ai_director (Gemini, response_schema) ──► EDL (JSON)
                                                     │
video + EDL ──► timeline (temizlik, zaman haritalama) ──► video_renderer (tek FFmpeg geçişi) ──► MP4
                     ▲
"3. saniyedeki whoosh'u kaldır" ──► refine_edl (yalnızca EDL metni, video yok) ──┘
```

- Tüm EDL zaman damgaları **ham video zamanındadır**. `services/timeline.py` bunları kesimli zaman çizelgesine yeniden haritalar (kesimler kare ızgarasına oturtulur; ses/görüntü kayması birikmez).
- İteratif düzenlemede EDL, Gemini'ye **kurgulanmış video zamanında** (kullanıcının izlediği) gösterilir ve cevap kodla geri çevrilir; zaman aritmetiği LLM'e kalmaz.
- Sese bağlı istekler ("boşlukları kes", "vurgulara zoom ekle") yerelde FFmpeg `silencedetect` ile ölçülen sessizlik ve konuşma başlangıcı ipuçlarını kullanır; video tekrar gönderilmez.
- İnsan okunur değişiklik listesi modele sorulmaz, iki EDL karşılaştırılarak hesaplanır.

## Gereksinimler

- **Python 3.10+** (3.13'te test edildi)
- **FFmpeg 7+** (8.1 ile test edildi), `libx264` ve `libmp3lame` ile, `PATH` içinde (Windows: `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`)
- [Google AI Studio](https://aistudio.google.com/apikey)'dan bir **Gemini API anahtarı**

## Hızlı başlangıç

### Windows (çift tıklama)

1. `.env.example` dosyasını `.env` olarak kopyalayıp `GEMINI_API_KEY` değerini girin (ya da `baslat.bat` dosyasının oluşturup açmasına izin verin).
2. **`baslat.bat`** dosyasına çift tıklayın. İlk çalıştırmada `.venv` oluşturur, bağımlılıkları kurar ve yer tutucu sesleri üretir; sonra sunucuyu başlatıp <http://localhost:8000> adresini açar. Penceresini kapatmak sunucuyu durdurur. Sunucu zaten açıksa yalnızca tarayıcıyı açar.

### Diğer platformlar

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
cp .env.example .env                              # sonra GEMINI_API_KEY girin
.venv/bin/python scripts/generate_assets.py       # yer tutucu sesler (aşağıya bakın)
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

<http://localhost:8000> adresini açın.

## Yapılandırma (`.env`)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `GEMINI_API_KEY` | – | Yapay zeka analizi ve düzenlemesi için **zorunlu** |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Video girişi ve yapılandırılmış çıktıyı destekleyen herhangi bir Gemini modeli. Bazı eski modeller (örn. `gemini-2.5-flash`) yeni API kullanıcılarına kapalıdır. Preview modeller değişebilir ya da yoğun olabilir (`503`); gerekirse buradan model değiştirin |
| `GEMINI_ANALYZE_TIMEOUT_SECONDS` | `150` | Video analizi zaman aşımı |
| `GEMINI_REFINE_TIMEOUT_SECONDS` | `45` | Yalnızca metin içeren düzenleme zaman aşımı |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | `PATH` içinde değillerse ayarlayın |

## Kendi Ses ve Müziklerinizi Ekleme (Custom Audio Assets)

`python scripts/generate_assets.py` her şeyin kutudan çıktığı gibi çalışması için **sentetik test sesleri** üretir. Gerçek kurgu kalitesi için telifsiz sesleri indirip `assets/sfx/` ve `assets/bgm/` klasörlerine atın:

- Ses efektleri: <https://pixabay.com/sound-effects/search/rhythmic%20beats/>
- Arka plan müzikleri: <https://pixabay.com/music/search/rhythmic%20beats/>

Notlar:

- Desteklenen: `.mp3 .wav .m4a .aac .ogg .flac`. Yeniden başlatmak gerekmez; klasörler her istekte taranır.
- Dosya adı (küçük harf, uzantısız) Gemini'ye seçenek olarak sunulur; bu yüzden anlamlı adlar verin (`whoosh-fast.mp3`, `chill-lofi.mp3`). Yeni dosya eklemek için kod değişikliği gerekmez.
- SFX süreleri Gemini'ye bildirilir. Kısa vurgu sesleri (~0.5–2 sn) en iyi sonucu verir; uzun dosyalar (davul geçişleri, müzik "logo"ları) az kullanılır.
- Videodan kısa müzik döngülenir; uzun müzik kesilip sonda kısılır.
- Ses dosyaları boyut ve lisans nedeniyle **git'e eklenmez**: her kullanıcı kendi dosyalarını indirir. Kullanmadan ya da paylaşmadan önce Pixabay'in güncel lisans koşullarını kontrol edin.

## Web stüdyosunu kullanma

1. Bir video bırakın (`.mp4 .mov .m4v .webm`, en fazla 500 MB), isterseniz ilk kurguyu tarif edin.
2. Sohbetten ya da hızlı aksiyon butonlarından kısa isteklerle ince ayar yapın. Söylediğiniz zamanlar **kurgulanmış** videoya göredir: *"3. saniyedeki whoosh'u kaldır"*, *"4 ile 6. saniye arasına zoom ekle"*, *"son 2 saniyeyi kes"*, *"müziğin sesini biraz kıs"*.
3. Ya da **Aktif Kurgu Katmanları**'ndan görsel düzenleyin: bir kesimi/zoom'u/SFX'i × ile silin, müzik parçasını ya da sesini değiştirin. Bunlar `/manual-edl` kullanır ve yeni sürüm üretir.
4. Geri dönmek için **Geri Al** ya da sürüm seçiciyi kullanın. Geri alma geçmişi silmez; hedef sürümden kopyalanan yeni bir sürüm oluşturur.
5. Sonucu **indirin** (`.mp4`, 1080×1920).

Açık proje adreste (`#p=<id>`) tutulur; sayfayı yenilemek geçmişi kaybettirmez.

## API

Etkileşimli dokümantasyon: <http://localhost:8000/docs>.

| Uç nokta | Açıklama |
|---|---|
| `POST /api/projects/create` | `video` (multipart) + isteğe bağlı `instruction` → Gemini EDL → **v1** render |
| `POST /api/projects/{id}/edit` | `{"instruction": "…"}` → EDL'i güncelle → **vN+1** render (yapılacak bir şey yoksa `changed:false`) |
| `POST /api/projects/{id}/manual-edl` | Gövde: EDL (ham video zamanında) → doğrudan render |
| `POST /api/projects/{id}/revert` | `{"version": N}` ya da boş (bir önceki sürüm) |
| `GET /api/projects/{id}` | Güncel durum ve tüm sürüm geçmişi |
| `GET /api/projects/{id}/video?version=N` | Render edilmiş MP4 (range isteklerini destekler) |
| `GET /api/projects/{id}/source` | Yüklenen orijinal video |
| `POST /jobs`, `GET /jobs/{id}` | Tek seferlik kuyruklu render (isteğe bağlı kendi EDL'inizle) |
| `GET /health` | ffmpeg / anahtar / ses kütüphanesi kontrolü |

```bash
curl -F "video=@clip.mp4" -F "instruction=Hızlı tempolu ve enerjik olsun" http://localhost:8000/api/projects/create
curl -H "Content-Type: application/json" -d '{"instruction":"son 2 saniyeyi kes"}' \
     http://localhost:8000/api/projects/<id>/edit
```

Örnek EDL için [`samples/sample_edl.json`](samples/sample_edl.json) dosyasına bakın.

## Proje yapısı

```
api/          FastAPI router'ları (projects) ve ortak yardımcılar
models/       EDL pydantic şeması
services/     ai_director (Gemini), timeline (zaman haritalama), video_renderer (FFmpeg),
              edl_diff, ffmpeg_tools (probe, silencedetect), project_store
scripts/      generate_assets.py, smoke_test.py
web/          Tek sayfalık stüdyo (düz HTML/CSS/JS, derleme adımı yok)
tests/        Birim, FFmpeg entegrasyon ve API testleri
assets/       sfx/ ve bgm/ (git'e eklenmez)
uploads/ outputs/ projects/   Çalışma verisi (git'e eklenmez)
```

## Geliştirme

```bash
.venv/bin/python scripts/generate_assets.py   # testler yer tutucu sesleri gerektirir
.venv/bin/python -m pytest                    # birim + FFmpeg entegrasyon + API testleri (API anahtarı gerekmez)
.venv/bin/python scripts/smoke_test.py        # uçtan uca render kontrolü
```

Testler sahte bir Gemini istemcisi kullanır; yalnızca FFmpeg gerekir.

## Güvenlik ve gizlilik

- **Yalnızca yerel kullanım.** Kimlik doğrulama yoktur. `127.0.0.1` üzerinde çalıştırın (`baslat.bat` böyle yapar) ve internete açmayın: erişen herkes dosya yükleyebilir ve Gemini kotanızı harcayabilir.
- `create` sırasında video analiz için Google Gemini API'sine yüklenir ve yanıttan sonra Google'dan silinir. İteratif düzenlemeler yalnızca EDL metnini gönderir, videoyu asla.
- `.env` (API anahtarınız), `uploads/`, `outputs/` ve `projects/` git'e eklenmez.

## Sınırlamalar

- Proje durumu `projects/` altında JSON dosyalarında tutulur; çok kullanıcılı kullanım yoktur.
- Kesilen içerik sohbet isteğiyle geri getirilemez (kurgulanmış videoda yoktur); önceki bir sürümü ya da ham zamanda `manual-edl` kullanın.
- Kalite Gemini modeline bağlıdır. Preview modeller yükte yavaşlayabilir ya da `503` dönebilir.

## Katkı

Issue ve pull request'ler memnuniyetle karşılanır. Göndermeden önce test paketini çalıştırın ve değişiklikleri odaklı tutun.

## Lisans

[MIT](LICENSE) © 2026 Ömer Emre
