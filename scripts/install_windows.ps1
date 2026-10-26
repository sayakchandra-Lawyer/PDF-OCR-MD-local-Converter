# pdf2md installer for Windows. Run from the repository folder in PowerShell:
#     powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
# Installs into %USERPROFILE%\.pdf2md (no admin rights needed), then runs the setup wizard.
$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$App  = Join-Path $env:USERPROFILE ".pdf2md"
$Venv = Join-Path $App "venv"
$Bin  = Join-Path $env:USERPROFILE ".local\bin"

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Say "pdf2md installer for Windows (repo: $Repo)"

# 1. uv: installs a private Python and the Python packages
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "Installing uv (Python package manager, https://astral.sh/uv)"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$Bin;$env:Path"
}

# 2. Virtual environment + pdf2md
Say "Installing pdf2md and the conversion engine (first time: ~1-2 GB download)"
New-Item -ItemType Directory -Force -Path $App | Out-Null
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) { uv venv --python 3.12 $Venv }
uv pip install --python (Join-Path $Venv "Scripts\python.exe") -e $Repo

# 'pdf2md' command on PATH
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Set-Content -Path (Join-Path $Bin "pdf2md.cmd") -Value "@`"$Venv\Scripts\pdf2md.exe`" %*" -Encoding ASCII
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($userPath -split ";" | Where-Object { $_ -eq $Bin })) {
    [Environment]::SetEnvironmentVariable("Path", "$Bin;$userPath", "User")
    $env:Path = "$Bin;$env:Path"
    Write-Host "Added $Bin to your user PATH (open a new terminal to use 'pdf2md')."
}

# 3. Ollama: runs the local vision LLM for scanned pages and charts
if (-not (Get-Command ollama -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) {
    Say "Installing Ollama (local LLM runtime, https://ollama.com)"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    } else {
        Start-Process "https://ollama.com/download/windows"
        Read-Host "Install Ollama from the page that opened, then press Enter"
    }
}
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null; $up = $true; break }
    catch {
        if ($i -eq 0) {
            $exe = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
            if (Test-Path $exe) { Start-Process $exe } elseif (Get-Command ollama -ErrorAction SilentlyContinue) { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden }
        }
        Start-Sleep -Seconds 1
    }
}
if (-not $up) { Write-Host "Ollama is not running yet; you can download models later with: pdf2md models pull" }

# 4. Optional: Tesseract (better for Hindi / Bengali and other Indic scans)
if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Host "`nOptional: for Indic-language scans also install Tesseract:  winget install UB-Mannheim.TesseractOCR"
}

# 5. Setup wizard: folders, languages, model download, autostart, Claude wiring
Say "Running setup"
& (Join-Path $Venv "Scripts\pdf2md.exe") setup
