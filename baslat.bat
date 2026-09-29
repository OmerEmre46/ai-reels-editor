@echo off
rem ============================================================
rem  AI Reels Kurgu Studyosu - tek tikla baslat
rem  Sunucuyu baslatir, hazir olunca tarayicida http://localhost:8000 acar.
rem  Bu pencereyi kapatmak sunucuyu durdurur.
rem  (NOBROWSER=1 verilirse tarayici acilmaz.)
rem ============================================================
setlocal
cd /d "%~dp0"
title AI Reels Kurgu Studyosu
set "PORT=8000"
set "URL=http://localhost:%PORT%"
set "PY=.venv\Scripts\python.exe"

echo.
echo   AI Reels Kurgu Studyosu
echo   -----------------------
echo.

rem --- 1) Sunucu zaten calisiyorsa yalnizca tarayiciyi ac
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if %errorlevel%==0 goto :already_running

rem --- 2) FFmpeg gerekli
where ffmpeg >nul 2>nul
if errorlevel 1 goto :no_ffmpeg

rem --- 3) Ilk kurulum: sanal ortam + bagimliliklar + test sesleri
if exist "%PY%" goto :venv_ok
echo Ilk kurulum yapiliyor, birkac dakika surebilir...
where py >nul 2>nul
if %errorlevel%==0 (py -3 -m venv .venv) else (python -m venv .venv)
if not exist "%PY%" goto :no_python
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :pip_failed
"%PY%" scripts\generate_assets.py
:venv_ok

rem --- 4) .env yoksa olustur ve API anahtarini girmenizi iste
if exist ".env" goto :env_ok
if not exist ".env.example" goto :env_ok
copy ".env.example" ".env" >nul
echo .env dosyasi olusturuldu. Acilan dosyaya GEMINI_API_KEY degerinizi yazip
echo kaydedin, sonra baslat.bat dosyasini tekrar calistirin.
start "" notepad ".env"
pause
exit /b 1
:env_ok
findstr /R /C:"^GEMINI_API_KEY=." ".env" >nul
if errorlevel 1 echo UYARI: .env icinde GEMINI_API_KEY bos. Yapay zeka kurgusu calismaz.

rem --- 5) Sunucu hazir olunca tarayiciyi ac (arka planda bekler)
if defined NOBROWSER goto :run
start "" /min powershell -NoProfile -Command "for($i=0;$i -lt 90;$i++){ try{ Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 1 } }; Start-Process '%URL%'"

:run
echo Sunucu baslatiliyor: %URL%
echo Durdurmak icin bu pencereyi kapatin veya Ctrl+C basin.
echo.
"%PY%" -m uvicorn main:app --host 127.0.0.1 --port %PORT%
echo.
echo Sunucu durdu.
pause
exit /b 0

:already_running
echo Sunucu zaten calisiyor. Tarayici aciliyor: %URL%
if not defined NOBROWSER start "" "%URL%"
ping -n 4 127.0.0.1 >nul
exit /b 0

:no_ffmpeg
echo HATA: FFmpeg bulunamadi. Kurmak icin bir terminalde su komutu calistirin:
echo     winget install Gyan.FFmpeg
echo Sonra baslat.bat dosyasini tekrar calistirin.
pause
exit /b 1

:no_python
echo HATA: Python 3 bulunamadi. https://www.python.org/downloads/ adresinden kurun
echo ("Add python.exe to PATH" secenegini isaretleyin), sonra tekrar calistirin.
pause
exit /b 1

:pip_failed
echo HATA: Bagimliliklar kurulamadi. Internet baglantinizi kontrol edip tekrar deneyin.
pause
exit /b 1
