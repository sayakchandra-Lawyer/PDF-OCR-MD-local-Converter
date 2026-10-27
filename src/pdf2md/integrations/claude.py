"""Wire pdf2md into Claude: Desktop MCP config, Claude Code, Cowork plugin zip,
and the global-instructions text."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

# Shipped inside the package so release builds (wheel / Windows installer) carry it too.
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "claude_plugin"

GLOBAL_INSTRUCTIONS = """PDF files: my PDFs are converted to Markdown locally by "pdf2md" so you never have to spend tokens reading a PDF.
Before reading any PDF in my folders: find the `.pdf2md.json` file at the project folder's root, open the INDEX.md in the Markdown folder it names (usually MD/ or 01_Context/MD/), and read the matching .md file instead of the PDF. The Markdown has page markers (<!-- page N -->), tables as Markdown tables and text descriptions with data for charts and figures.
If a PDF is not converted yet, call the pdf_to_markdown tool if you have it; otherwise the background converter will finish it shortly (check the Status column in INDEX.md, wait and re-check).
Open a PDF directly only if I ask you to, or to visually verify one specific page."""


def desktop_config_paths() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Claude/claude_desktop_config.json"]
    if os.name == "nt":
        paths = [Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Claude/claude_desktop_config.json"]
        # Microsoft Store / MSIX installs keep their config in a virtualised folder.
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")) / "Packages"
        if local.is_dir():
            for pkg in local.glob("*Claude*"):
                cand = pkg / "LocalCache/Roaming/Claude"
                if cand.is_dir():
                    paths.append(cand / "claude_desktop_config.json")
        return paths
    return [home / ".config/Claude/claude_desktop_config.json"]


def mcp_entry() -> dict:
    return {"command": sys.executable, "args": ["-m", "pdf2md", "mcp"]}


def install_desktop_mcp() -> list[str]:
    done = []
    for path in desktop_config_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                data = json.loads(text)
            backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(path, backup)
        data.setdefault("mcpServers", {})["pdf2md"] = mcp_entry()
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        done.append(str(path))
    return done


def install_claude_code_mcp() -> str:
    claude = shutil.which("claude")
    if not claude:
        return "Claude Code CLI not found; skipped. Later run: claude mcp add -s user pdf2md -- " + " ".join(
            [sys.executable, "-m", "pdf2md", "mcp"]
        )
    subprocess.run([claude, "mcp", "remove", "-s", "user", "pdf2md"], capture_output=True)
    r = subprocess.run(
        [claude, "mcp", "add", "-s", "user", "pdf2md", "--", sys.executable, "-m", "pdf2md", "mcp"],
        capture_output=True, text=True,
    )
    return "Claude Code: pdf2md MCP server added (user scope)." if r.returncode == 0 else f"Claude Code: {r.stderr.strip()}"


def build_plugin_zip(out_dir: Path) -> Path:
    if not (PLUGIN_DIR / ".claude-plugin" / "plugin.json").exists():
        raise FileNotFoundError(f"Plugin folder not found at {PLUGIN_DIR}; reinstall pdf2md.")
    out_dir.mkdir(parents=True, exist_ok=True)
    version = json.loads((PLUGIN_DIR / ".claude-plugin/plugin.json").read_text())["version"]
    out = out_dir / f"pdf-markdown-first-{version}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(PLUGIN_DIR.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(PLUGIN_DIR).as_posix())
    return out

