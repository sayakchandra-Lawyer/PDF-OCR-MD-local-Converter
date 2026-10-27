; Inno Setup script for the pdf2md Windows installer.
; Built by .github/workflows/release.yml:
;   ISCC.exe /DAppVersion=0.2.0 /DWheelName=pdf_ocr_md_local_converter-0.2.0-py3-none-any.whl packaging\windows\pdf2md.iss
; Expects in dist\ : the wheel and pdf-markdown-first-<version>.zip (python scripts/build.py)
; and packaging\windows\uv.exe (downloaded by the workflow).

#ifndef AppVersion
  #error Pass /DAppVersion=X.Y.Z
#endif
#ifndef WheelName
  #error Pass /DWheelName=<wheel file name in dist>
#endif

#define VenvExe "{%USERPROFILE}\.pdf2md\venv\Scripts\pdf2md.exe"

[Setup]
AppId={{7B6E3E1C-6C1A-4F7B-9A57-2D0C5E1B9F42}
AppName=pdf2md - PDF to Markdown for Claude
AppVersion={#AppVersion}
AppVerName=pdf2md {#AppVersion}
AppPublisher=Sayak Chandra
AppPublisherURL=https://github.com/sayakchandra-Lawyer/PDF-OCR-MD-local-Converter
AppSupportURL=https://github.com/sayakchandra-Lawyer/PDF-OCR-MD-local-Converter/issues
AppUpdatesURL=https://github.com/sayakchandra-Lawyer/PDF-OCR-MD-local-Converter/releases
DefaultDirName={localappdata}\Programs\pdf2md
DefaultGroupName=pdf2md
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=pdf2md-setup-{#AppVersion}
LicenseFile=..\..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=pdf2md (PDF to Markdown for Claude)
SetupLogging=yes

[Messages]
FinishedLabel=pdf2md is installed.%n%nThe background converter now turns new PDFs in your project folders into Markdown automatically. Start menu > pdf2md has shortcuts for progress, checks and updates.

[Files]
Source: "..\..\dist\{#WheelName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\pdf-markdown-first-{#AppVersion}.zip"; DestDir: "{app}"; Flags: ignoreversion
Source: "bootstrap.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "uv.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\pdf2md - Conversion progress"; Filename: "powershell.exe"; Parameters: "-NoExit -NoProfile -Command ""& '{#VenvExe}' progress --live"""
Name: "{group}\pdf2md - Check installation (doctor)"; Filename: "powershell.exe"; Parameters: "-NoExit -NoProfile -Command ""& '{#VenvExe}' doctor"""
Name: "{group}\pdf2md - Setup (folders, languages, Claude)"; Filename: "powershell.exe"; Parameters: "-NoExit -NoProfile -Command ""& '{#VenvExe}' setup"""
Name: "{group}\pdf2md - Update"; Filename: "powershell.exe"; Parameters: "-NoExit -NoProfile -Command ""& '{#VenvExe}' update"""
Name: "{group}\pdf2md - Logs folder"; Filename: "{%USERPROFILE}\.pdf2md\logs"
Name: "{group}\pdf2md - Claude plugin (zip)"; Filename: "{app}"
Name: "{group}\Uninstall pdf2md"; Filename: "{uninstallexe}"

[Run]
; Installs Python, pdf2md and Ollama, then runs the setup wizard in a visible window.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\bootstrap.ps1"" -Source ""{app}\{#WheelName}"" -Uv ""{app}\uv.exe"""; \
  StatusMsg: "Installing the conversion engine and running setup (in the separate window)..."; \
  Flags: waituntilterminated skipifsilent

[UninstallRun]
Filename: "{#VenvExe}"; Parameters: "autostart uninstall"; Flags: runhidden skipifdoesntexist; RunOnceId: "StopWatcher"

[UninstallDelete]
; The Python environment is large; settings, logs and your converted Markdown are kept.
Type: filesandordirs; Name: "{%USERPROFILE}\.pdf2md\venv"
Type: files; Name: "{%USERPROFILE}\.local\bin\pdf2md.cmd"
