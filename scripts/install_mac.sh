#!/usr/bin/env bash
# pdf2md installer for macOS.  Run from the repository folder:
#     ./scripts/install_mac.sh
# Installs into ~/.pdf2md (no admin rights needed), then runs the setup wizard.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/.pdf2md"
VENV="$APP/venv"
BIN="$HOME/.local/bin"

say() { printf "\n\033[1m==> %s\033[0m\n" "$*"; }

say "pdf2md installer for macOS (repo: $REPO)"

# 1. uv: installs a private Python and the Python packages
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python package manager, https://astral.sh/uv)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$BIN:$HOME/.cargo/bin:$PATH"
fi

# 2. Virtual environment + pdf2md (Docling, OCR engines, MCP server)
say "Installing pdf2md and the conversion engine (first time: ~1-2 GB download)"
mkdir -p "$APP"
[ -x "$VENV/bin/python" ] || uv venv --python 3.12 "$VENV"
uv pip install --python "$VENV/bin/python" -e "$REPO"
mkdir -p "$BIN"
ln -sf "$VENV/bin/pdf2md" "$BIN/pdf2md"
case ":$PATH:" in *":$BIN:"*) ;; *)
  echo "export PATH=\"$BIN:\$PATH\"" >> "$HOME/.zprofile"
  export PATH="$BIN:$PATH"
  echo "Added $BIN to your PATH in ~/.zprofile (open a new terminal to use 'pdf2md')." ;;
esac

# 3. Ollama: runs the local vision LLM for scanned pages and charts
if ! command -v ollama >/dev/null 2>&1 && [ ! -d "/Applications/Ollama.app" ]; then
  say "Installing Ollama (local LLM runtime, https://ollama.com)"
  if command -v brew >/dev/null 2>&1; then
    brew install --cask ollama
  else
    echo "Please install Ollama from the page that opens, then come back here."
    open "https://ollama.com/download/mac"
    read -r -p "Press Enter once Ollama is installed... " _
  fi
fi
if ! curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  open -a Ollama 2>/dev/null || (command -v ollama >/dev/null && (ollama serve >/dev/null 2>&1 &))
  for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
    sleep 1
  done
fi

# 4. Setup wizard: folders, languages, model download, autostart, Claude wiring
say "Running setup"
"$VENV/bin/pdf2md" setup

REAL_PY="$("$VENV/bin/python" -c 'import os,sys; print(os.path.realpath(sys.executable))')"
cat <<EOF

macOS privacy note: the background watcher runs as
    $REAL_PY
If your project folder is in Documents, Desktop, iCloud Drive or Google Drive and conversions
do not happen, open System Settings > Privacy & Security > Full Disk Access, click +,
press Cmd+Shift+G, paste the path above and enable it. Then run: pdf2md autostart restart
EOF
