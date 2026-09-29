"""Test amaçlı sahte SFX / BGM dosyalarını FFmpeg ile üretir.

Var olan dosyalar ASLA ezilmez (gerçek dosyalarınızı koyduğunuzda korunur);
yeniden üretmek için --force kullanın.

    python scripts/generate_assets.py [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from services.ffmpeg_tools import run_ffmpeg  # noqa: E402

SR = 44100

# 0.5 sn SFX kaynakları (lavfi)
SFX = {
    "whoosh": "anoisesrc=d=0.5:c=pink:a=0.7:r={sr},highpass=f=500,lowpass=f=5000,"
              "afade=t=in:d=0.25,afade=t=out:st=0.25:d=0.25",
    "pop": "aevalsrc='0.8*sin(2*PI*900*t)*exp(-25*t)':d=0.5:s={sr}",
    "ding": "aevalsrc='0.5*(sin(2*PI*1760*t)+0.4*sin(2*PI*3520*t))*exp(-6*t)':d=0.5:s={sr}",
    "riser": "aevalsrc='0.6*sin(2*PI*(200*t+1200*t*t))*(2*t)':d=0.5:s={sr}",
    "bass_drop": "aevalsrc='0.9*sin(2*PI*(120*t-80*t*t))*exp(-4*t)':d=0.5:s={sr}",
}

# 30 sn düşük frekanslı BGM kaynakları
BGM = {
    "upbeat": "aevalsrc='0.25*sin(2*PI*110*t)*(0.4+0.6*lt(mod(t,0.5),0.2))':d=30:s={sr}",
    "chill": "aevalsrc='0.2*sin(2*PI*82.41*t)*(0.7+0.3*sin(2*PI*0.25*t))':d=30:s={sr}",
    "dramatic": "aevalsrc='0.15*(sin(2*PI*55*t)+sin(2*PI*58*t))*(0.6+0.4*sin(2*PI*0.1*t))':d=30:s={sr}",
}


def _make(folder: Path, name: str, src: str, force: bool) -> None:
    out = folder / f"{name}.mp3"
    if out.exists() and not force:
        print(f"  = {out.relative_to(config.BASE_DIR)} (mevcut, atlandı)")
        return
    run_ffmpeg(["-f", "lavfi", "-i", src.format(sr=SR), "-ac", "2",
                "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
    print(f"  + {out.relative_to(config.BASE_DIR)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Mevcut dosyaların üzerine yaz")
    args = ap.parse_args()
    print("SFX:")
    for name, src in SFX.items():
        _make(config.SFX_DIR, name, src, args.force)
    print("BGM:")
    for name, src in BGM.items():
        _make(config.BGM_DIR, name, src, args.force)


if __name__ == "__main__":
    main()
