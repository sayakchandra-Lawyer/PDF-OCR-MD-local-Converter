"""`setup`: the first-time wizard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pdf2md import __version__
from pdf2md.cli.integration import cmd_claude
from pdf2md.cli.prompts import ask, yes
from pdf2md.cli.settings import cmd_models
from pdf2md.config import CONFIG_PATH, Config, expand, save_config


def default_projects_folder() -> str:
    """Best guess for the folder holding the user's project folders, including a
    Google Drive for Desktop copy of "Claude Projects/01_Active"."""
    home = Path.home()
    candidates = []
    for drive_root in sorted((home / "Library/CloudStorage").glob("GoogleDrive-*/My Drive")):
        candidates.append(drive_root / "Claude Projects/01_Active")
    if sys.platform == "win32":
        for letter in "GHIJKLMNOPQRSTUVWXYZ":
            candidates.append(Path(f"{letter}:/My Drive/Claude Projects/01_Active"))
    candidates += [home / "Documents/Claude Projects", home / "Claude Projects"]
    return next((str(p) for p in candidates if p.is_dir()), "")


def cmd_setup(args, cfg: Config) -> int:
    from pdf2md.integrations import autostart
    from pdf2md.integrations import claude as cs
    from pdf2md.cli.doctor import suggested_vision_model, total_ram_gb
    from pdf2md.engine.ollama import OllamaClient

    y = args.yes
    print(f"pdf2md {__version__} setup\n")

    folders = list(args.folder or [])
    if not folders and not y:
        default = default_projects_folder()
        print("Which folder holds your Claude/Cowork project folders? Every sub-folder is treated as one project.")
        ans = ask("Folder", default)
        if ans:
            folders.append(ans)
    for f in folders:
        p = expand(f)
        p.mkdir(parents=True, exist_ok=True)
        if str(p) not in cfg.watch.project_parents:
            cfg.watch.project_parents.append(str(p))

    if args.languages:
        cfg.ocr.languages = [c.strip() for c in args.languages.split(",") if c.strip()]
    elif not y:
        langs = ask("OCR languages, ISO codes comma-separated (e.g. en,hi,bn)", ",".join(cfg.ocr.languages))
        cfg.ocr.languages = [c.strip() for c in langs.split(",") if c.strip()] or ["en"]

    if not CONFIG_PATH.exists() or args.model:
        cfg.llm.vision_model = args.model or suggested_vision_model()
    print(f"\nRAM {total_ram_gb():.0f} GB -> local vision model: {cfg.llm.vision_model}")
    path = save_config(cfg)
    print(f"Config written: {path}")

    client = OllamaClient(cfg.llm.ollama_url)
    if client.available():
        if not client.has_model(cfg.llm.vision_model) and yes(f"Download {cfg.llm.vision_model} now (several GB)?", True, y):
            cmd_models(argparse.Namespace(action="pull"), cfg)
    else:
        print("Ollama is not running: figures will be saved as images without descriptions and scans will use "
              "classical OCR until you install it (https://ollama.com/download) and run: pdf2md models pull")

    if yes("Start the watcher automatically at login?", True, y):
        print(autostart.install())
    if yes("Add the pdf2md tool to Claude Desktop / Claude Code (MCP)?", True, y):
        cmd_claude(argparse.Namespace(action="install-mcp"), cfg)
    out = Path(args.out).expanduser() if args.out else Path.home() / "Desktop"
    if not out.is_dir():
        out = Path.home()
    try:
        cmd_claude(argparse.Namespace(action="plugin-zip", out=str(out)), cfg)
    except FileNotFoundError as e:
        print(e)
    print("\nFinally, paste this into Claude Desktop > Settings > General > 'Instructions for Claude':\n")
    print(cs.GLOBAL_INSTRUCTIONS)
    print("\nDone. Check everything with: pdf2md doctor")
    return 0
