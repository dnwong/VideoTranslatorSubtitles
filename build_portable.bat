@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0auto_build_portable.ps1"
if errorlevel 1 goto failed

echo.
echo Portable build complete.
echo Copy this single file to any Windows computer:
echo   dist_portable_*\VideoTranslatorSubtitles.exe
pause
exit /b 0

:failed
echo.
echo Build failed.
pause
exit /b 1
