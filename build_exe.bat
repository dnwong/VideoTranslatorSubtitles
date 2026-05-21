@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0auto_build.ps1"
if errorlevel 1 goto failed

echo.
echo Build complete.
echo EXE location: dist\VideoTranslatorSubtitles\VideoTranslatorSubtitles.exe
pause
exit /b 0

:failed
echo.
echo Build failed.
pause
exit /b 1
