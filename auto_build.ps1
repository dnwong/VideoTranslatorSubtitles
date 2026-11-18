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
$distPath = Join-Path $PSScriptRoot ('dist_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --distpath $distPath --name VideoTranslatorSubtitles --collect-all faster_whisper --collect-all ctranslate2 --collect-all tokenizers --collect-all imageio_ffmpeg --collect-all tkinterdnd2 app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host 'Build complete:'
Write-Host (Join-Path $distPath 'VideoTranslatorSubtitles\VideoTranslatorSubtitles.exe')
