#!/usr/bin/env bash
# pdf2md installer for Linux (Claude Code users).  Run from the repository folder:
#     ./scripts/install_linux.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/.pdf2md"
VENV="$APP/venv"
BIN="$HOME/.local/bin"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv (https://astral.sh/uv)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$BIN:$PATH"
fi
mkdir -p "$APP" "$BIN"
[ -x "$VENV/bin/python" ] || uv venv --python 3.12 "$VENV"
uv pip install --python "$VENV/bin/python" -e "$REPO"
ln -sf "$VENV/bin/pdf2md" "$BIN/pdf2md"

if ! command -v ollama >/dev/null 2>&1; then
  echo "==> Ollama is not installed. Install it with your package manager or from https://ollama.com/download/linux"
  echo "    then run: pdf2md models pull"
fi
"$VENV/bin/pdf2md" setup
