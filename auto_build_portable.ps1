$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    throw 'Virtual environment not found. Create .venv and install requirements first.'
}

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.\.venv\Scripts\python.exe -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.\.venv\Scripts\python.exe -m py_compile app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$distPath = Join-Path $PSScriptRoot ('dist_portable_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --distpath $distPath --name VideoTranslatorSubtitles --collect-all faster_whisper --collect-all ctranslate2 --collect-all tokenizers --collect-all imageio_ffmpeg app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host 'Portable build complete (single file, copy just this .exe):'
Write-Host (Join-Path $distPath 'VideoTranslatorSubtitles.exe')
