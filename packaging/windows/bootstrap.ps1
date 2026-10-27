<#
  Installs or updates pdf2md for the current Windows user (no admin rights needed).
  Used by the Windows installer (pdf2md-setup-<version>.exe) and by scripts\install_windows.ps1.

    -Source      a pdf2md wheel (.whl), or the repository folder together with -Editable
    -Uv          path to uv.exe (the installer bundles one); otherwise uv is found or installed
    -Unattended  accept all setup defaults (used for automated tests)
    -SkipOllama  do not install Ollama
    -NoPause     do not wait for Enter at the end
#>
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$Uv = "",
    [switch]$Editable,
    [switch]$Unattended,
    [switch]$SkipOllama,
    [switch]$NoPause
)
$ErrorActionPreference = "Stop"

$App     = Join-Path $env:USERPROFILE ".pdf2md"
$Venv    = Join-Path $App "venv"
$Py      = Join-Path $Venv "Scripts\python.exe"
$Exe     = Join-Path $Venv "Scripts\pdf2md.exe"
$Bin     = Join-Path $env:USERPROFILE ".local\bin"
$Config  = Join-Path $App "config.toml"
$Startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\pdf2md-watcher.vbs"
$Desktop = [Environment]::GetFolderPath("Desktop")

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }

$failed = $false
try {
    Say "pdf2md installer"

    # 1. uv: provides Python 3.12 and installs the packages
    if (-not $Uv -or -not (Test-Path $Uv)) {
        $found = Get-Command uv -ErrorAction SilentlyContinue
        if ($found) {
            $Uv = $found.Source
        } else {
            Say "Installing uv (Python package manager, https://astral.sh/uv)"
            powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
            $Uv = Join-Path $Bin "uv.exe"
        }
    }

    # 2. An update: stop the running converter so its files can be replaced
    $hadAutostart = Test-Path $Startup
    if (Test-Path $Exe) {
        Say "Stopping the background converter for the update"
        & $Exe autostart uninstall | Out-Null
    }

    # 3. Private Python environment + pdf2md
    Say "Installing pdf2md (first time: downloads about 1-2 GB of OCR and layout components)"
    New-Item -ItemType Directory -Force -Path $App | Out-Null
    if (-not (Test-Path $Py)) {
        & $Uv venv --python 3.12 $Venv
        Check "Creating the Python environment"
    }
    if ($Editable) {
        & $Uv pip install --python $Py -e $Source
    } else {
        & $Uv pip install --python $Py --upgrade $Source
    }
    Check "Installing pdf2md"

    # 4. 'pdf2md' command on PATH
    New-Item -ItemType Directory -Force -Path $Bin | Out-Null
    Set-Content -Path (Join-Path $Bin "pdf2md.cmd") -Value "@`"$Exe`" %*" -Encoding ASCII
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { $userPath = "" }
    if (-not ($userPath -split ";" | Where-Object { $_ -eq $Bin })) {
        [Environment]::SetEnvironmentVariable("Path", "$Bin;$userPath", "User")
        Write-Host "Added $Bin to your PATH (new terminals can run 'pdf2md')."
    }
    $env:Path = "$Bin;$env:Path"

    # 5. Ollama: runs the local vision model for scanned pages and charts
    if (-not $SkipOllama) {
        $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
        if (-not (Get-Command ollama -ErrorAction SilentlyContinue) -and -not (Test-Path $ollamaExe)) {
            Say "Installing Ollama (local AI runtime, https://ollama.com)"
            if (Get-Command winget -ErrorAction SilentlyContinue) {
                winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
            } else {
                Start-Process "https://ollama.com/download/windows"
                Read-Host "Install Ollama from the page that opened, then press Enter"
            }
        }
        $up = $false
        for ($i = 0; $i -lt 30 -and -not $up; $i++) {
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null
                $up = $true
            } catch {
                if ($i -eq 0) {
                    $app = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
                    if (Test-Path $app) { Start-Process $app }
                }
                Start-Sleep -Seconds 1
            }
        }
        if (-not $up) { Write-Host "Ollama is not running yet; later run: pdf2md models pull" }
    }

    # 6. First install -> setup wizard; update -> keep settings, restart the converter
    if ((Test-Path $Config) -and -not $Unattended) {
        Say "Existing settings kept ($Config)"
        if ($hadAutostart) {
            & $Exe autostart install
            Check "Restarting the background converter"
        }
        & $Exe claude install-mcp
        & $Exe claude plugin-zip --out $Desktop
        Write-Host "If the plugin version changed, upload the new zip from your Desktop in Claude Desktop (Customize > Plugins)."
    } elseif ($Unattended) {
        & $Exe setup --yes --out $Desktop
        Check "Setup"
    } else {
        Say "First-time setup"
        & $Exe setup --out $Desktop
        Check "Setup"
    }

    Say "Checking the installation"
    & $Exe doctor
    Write-Host "`npdf2md is installed. Useful commands: pdf2md progress --live | pdf2md doctor | pdf2md update" -ForegroundColor Green
} catch {
    $failed = $true
    Write-Host "`nInstallation failed: $_" -ForegroundColor Red
    Write-Host "Send a screenshot of this window if you need help." -ForegroundColor Red
}

if (-not $NoPause) { Read-Host "`nPress Enter to close this window" | Out-Null }
if ($failed) { exit 1 }
