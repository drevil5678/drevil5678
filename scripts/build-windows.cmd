@echo off
setlocal
cd /d "%~dp0.."
where uv >nul 2>nul
if errorlevel 1 (
  echo Please install Python 3.12 or 3.13 and uv, then run this script again.
  pause
  exit /b 1
)
uv sync --locked --group build
if errorlevel 1 goto fail
.venv\Scripts\python.exe scripts\build_portable.py
if errorlevel 1 goto fail
echo Portable package created in dist. Please extract and test it on the event server.
pause
exit /b 0
:fail
echo Build failed. Review the output above; existing event data was not overwritten.
pause
exit /b 1
