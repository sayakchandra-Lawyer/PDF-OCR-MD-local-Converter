"""Command line interface: `pdf2md <command>`.

The commands live in the sibling modules; this module only builds the parser
and dispatches.
"""

from __future__ import annotations

import argparse
import logging
import sys

from pdf2md import __version__
from pdf2md.cli.convert import cmd_convert, cmd_scan, cmd_watch
from pdf2md.cli.integration import cmd_autostart, cmd_claude, cmd_doctor, cmd_mcp
from pdf2md.cli.logs import setup_logging
from pdf2md.cli.monitor import cmd_progress, cmd_status
from pdf2md.cli.settings import cmd_add_folder, cmd_config, cmd_models, cmd_remove_folder
from pdf2md.cli.setup import cmd_setup
from pdf2md.config import LOG_DIR, load_config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdf2md", description="Local PDF -> Markdown converter for Claude.")
    p.add_argument("--version", action="version", version=f"pdf2md {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("convert", help="convert PDF files or every PDF in a folder")
    s.add_argument("paths", nargs="+")
    s.add_argument("--project", help="project folder (default: detected from the path)")
    s.add_argument("--force", action="store_true", help="re-convert even if up to date")
    s.add_argument("--no-move", action="store_true", help="leave the PDF where it is")
    s.add_argument("--no-figures", action="store_true", help="skip figure descriptions (faster)")
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("scan", help="convert everything new in the watched folders once")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("watch", help="run the background watcher in the foreground")
    s.add_argument("--force", action="store_true", help="start even if another watcher seems to run")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("progress", help="how many files are converted and how many are left")
    s.add_argument("project", nargs="?", help="only projects whose name contains this text")
    s.add_argument("--live", action="store_true", help="keep refreshing the display")
    s.add_argument("--every", type=int, default=15, help="seconds between refreshes with --live")
    s.set_defaults(func=cmd_progress)

    s = sub.add_parser("status", help="watcher state and converted documents")
    s.add_argument("project", nargs="?")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("add-folder", help="watch a folder of project folders (or one project with --single)")
    s.add_argument("path")
    s.add_argument("--single", action="store_true", help="the folder itself is one project")
    s.set_defaults(func=cmd_add_folder)

    s = sub.add_parser("remove-folder", help="stop watching a folder")
    s.add_argument("path")
    s.set_defaults(func=cmd_remove_folder)

    s = sub.add_parser("models", help="download / list the local vision models (Ollama)")
    s.add_argument("action", choices=["pull", "list"], nargs="?", default="pull")
    s.set_defaults(func=cmd_models)

    s = sub.add_parser("autostart", help="run the watcher at login")
    s.add_argument("action", choices=["install", "uninstall", "restart", "status"])
    s.set_defaults(func=cmd_autostart)

    s = sub.add_parser("claude", help="connect pdf2md to Claude")
    s.add_argument("action", choices=["install-mcp", "plugin-zip", "instructions"])
    s.add_argument("--out", help="output folder for plugin-zip")
    s.set_defaults(func=cmd_claude)

    s = sub.add_parser("doctor", help="check the installation")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("mcp", help="run the MCP server (used by Claude)")
    s.add_argument("--http", action="store_true", help="streamable HTTP on 127.0.0.1 instead of stdio")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_mcp)

    s = sub.add_parser("config", help="show the configuration file")
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("setup", help="first-time setup wizard")
    s.add_argument("--folder", action="append", help="folder of project folders (repeatable)")
    s.add_argument("--languages", help="OCR languages, e.g. en,hi,bn")
    s.add_argument("--model", help="Ollama vision model to use")
    s.add_argument("--out", help="where to save the Cowork plugin zip (default: Desktop)")
    s.add_argument("-y", "--yes", action="store_true", help="accept all defaults")
    s.set_defaults(func=cmd_setup)
    return p


def main(argv: list[str] | None = None) -> None:
    if sys.stderr is None or sys.stdout is None:  # pythonw.exe (Windows autostart) has no console
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stream = open(LOG_DIR / "watcher.console.log", "a", encoding="utf-8", buffering=1)
        sys.stderr = sys.stderr or stream
        sys.stdout = sys.stdout or stream
    args = build_parser().parse_args(argv)
    if args.command not in ("watch", "mcp"):
        setup_logging()
        logging.getLogger("pdf2md").setLevel(logging.WARNING)
    cfg = load_config()
    sys.exit(args.func(args, cfg) or 0)
