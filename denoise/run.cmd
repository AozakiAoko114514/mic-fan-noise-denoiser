@echo off
rem run.cmd -- start the real-time denoise virtual microphone
rem This file is intentionally ASCII-only: cmd.exe parses .cmd in the OEM
rem codepage, and UTF-8 Chinese text can swallow following characters.
rem To stop: just close this window (microphone returns to normal immediately).
rem Live stats: run   Get-Content live_run.log -Wait   in another PowerShell window.
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ============================================================
echo  Mic Array  -^>  Denoise  -^>  Voicemeeter virtual input
echo  Devices are read from config.json
echo  In CS2 choose microphone: "Voicemeeter Out 6"
echo  Live stats are written to live_run.log
echo  Close this window to stop (mic returns to normal)
echo ============================================================
python -u live.py --run %* > "%~dp0live_run.log" 2>&1
echo.
echo Pipeline exited.
pause
