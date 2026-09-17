@echo off
rem Show manager status only (ASCII-only, see start-all.cmd)
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

set "PYEXE=python"
where python >nul 2>&1 || set "PYEXE=py -3"

%PYEXE% "%~dp0manager.py" --status
echo.
pause
