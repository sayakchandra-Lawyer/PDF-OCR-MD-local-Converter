# pdf2md installer for Windows, from a clone of the repository (for development).
# Most people should use the one-click installer instead: pdf2md-setup-<version>.exe from
# https://github.com/sayakchandra-Lawyer/PDF-OCR-MD-local-Converter/releases/latest
#
#     powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
#
# Installs the repository in editable mode (so `git pull` / `pdf2md update` picks up changes).
$Repo = Split-Path -Parent $PSScriptRoot
& (Join-Path $Repo "packaging\windows\bootstrap.ps1") -Source $Repo -Editable
exit $LASTEXITCODE
