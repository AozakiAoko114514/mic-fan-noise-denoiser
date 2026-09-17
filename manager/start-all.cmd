@echo off
rem ============================================================
rem  One-click start: Voicemeeter + denoise pipeline
rem
rem  ASCII-only on purpose: cmd.exe parses .cmd files using the
rem  OEM code page, and UTF-8 non-ASCII text inside a .cmd can
rem  swallow the following ASCII characters and break the script.
rem ============================================================
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

set "PYEXE=python"
where python >nul 2>&1 || set "PYEXE=py -3"

%PYEXE% "%~dp0manager.py" --start
echo.
pause
