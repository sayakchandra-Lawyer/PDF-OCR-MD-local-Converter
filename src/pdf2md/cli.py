"""Command line interface: `pdf2md <command>`."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from pdf2md import __version__
from pdf2md.config import CONFIG_PATH, LOG_DIR, Config, expand, load_config, save_config


def _setup_logging(to_file: bool = False, verbose: bool = False) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if to_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(LOG_DIR / "watcher.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )
    for noisy in ("docling", "httpx", "rapidocr", "RapidOCR", "watchdog", "huggingface_hub", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _ask(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{question}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or default


def _yes(question: str, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    ans = _ask(question + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not ans else ans.startswith("y")


def _default_projects_folder() -> str:
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


# ----------------------------------------------------------------- commands
def cmd_convert(args, cfg: Config) -> int:
    from pdf2md.service import Service

    svc = Service(cfg)
    failures = 0
    for raw in args.paths:
        path = expand(raw)
        if path.is_dir():
            from pdf2md.layout import ProjectLayout

            layout = ProjectLayout.for_root(Path(args.project) if args.project else path, cfg.layout)
            pdfs = sorted(layout.iter_pdfs())
        elif path.is_file():
            pdfs = [path]
        else:
            print(f"not found: {raw}", file=sys.stderr)
            failures += 1
            continue
        for pdf in pdfs:
            res = svc.process(
                pdf,
                project=Path(args.project) if args.project else None,
                force=args.force,
                move=False if args.no_move else None,
                describe_figures=False if args.no_figures else None,
                progress=lambda m: print(f"   ... {m}", file=sys.stderr),
            )
            print(res.summary())
            for w in (res.entry or {}).get("warnings") or []:
                print(f"   warning: {w}")
            failures += res.status == "failed"
    return 1 if failures else 0


def cmd_scan(args, cfg: Config) -> int:
    from pdf2md.service import Service

    results = Service(cfg).scan_all(force=args.force)
    for r in results:
        print(r.summary())
    if not results:
        print("No PDFs found in the watched folders.")
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_watch(args, cfg: Config) -> int:
    from pdf2md.watcher import Watcher, watcher_state

    st = watcher_state()
    if st and st.get("alive") and not args.force:
        print(f"A watcher is already running (pid {st.get('pid')}). Use --force to start another.", file=sys.stderr)
        return 1
    _setup_logging(to_file=True, verbose=args.verbose)
    logging.getLogger("pdf2md").info("pdf2md %s watcher starting (config %s)", __version__, CONFIG_PATH)
    Watcher(cfg).run()
    return 0


def cmd_status(args, cfg: Config) -> int:
    from pdf2md.autostart import status
    from pdf2md.layout import iter_projects
    from pdf2md.manifest import Manifest

    print(status())
    for layout in iter_projects(cfg):
        if not layout.manifest_path.exists():
            continue
        if args.project and args.project.lower() not in layout.name.lower():
            continue
        m = Manifest.load(layout)
        print(f"\n{layout.name}  ({layout.root})")
        for rel, e in sorted(m.docs.items()):
            print(f"  [{e.get('status', '?'):10}] {rel}  ->  {e.get('md', '-')}  ({e.get('pages', '?')} pages)")
    return 0


def cmd_add_folder(args, cfg: Config) -> int:
    path = expand(args.path)
    if not path.is_dir():
        print(f"not a folder: {path}", file=sys.stderr)
        return 1
    target = cfg.watch.projects if args.single else cfg.watch.project_parents
    if str(path) not in target:
        target.append(str(path))
    save_config(cfg)
    kind = "project folder" if args.single else "folder of project folders"
    print(f"Added {kind}: {path}\nRestart the watcher to pick it up: pdf2md autostart restart")
    return 0


def cmd_remove_folder(args, cfg: Config) -> int:
    path = str(expand(args.path))
    cfg.watch.projects = [p for p in cfg.watch.projects if str(expand(p)) != path]
    cfg.watch.project_parents = [p for p in cfg.watch.project_parents if str(expand(p)) != path]
    save_config(cfg)
    print(f"Removed {path} (if it was configured).")
    return 0


def cmd_models(args, cfg: Config) -> int:
    from pdf2md.ollama import OllamaClient, OllamaError

    client = OllamaClient(cfg.llm.ollama_url)
    if not client.available():
        print(f"Ollama is not running at {cfg.llm.ollama_url}. Install it from https://ollama.com/download and start it.")
        return 1
    if args.action == "list":
        print("\n".join(client.models()) or "(no models)")
        return 0
    models = {cfg.llm.vision_model, cfg.llm.page_model}
    for model in sorted(models):
        if client.has_model(model):
            print(f"{model}: already installed")
            continue
        print(f"pulling {model} (one-time download) ...")
        try:
            client.pull(model, on_progress=lambda s: print(f"  {s}", end="\r", flush=True))
            print(f"\n{model}: ready")
        except OllamaError as e:
            print(f"\nfailed to pull {model}: {e}")
            return 1
    return 0


def cmd_autostart(args, cfg: Config) -> int:
    from pdf2md import autostart

    print({"install": autostart.install, "uninstall": autostart.uninstall,
           "restart": autostart.restart, "status": autostart.status}[args.action]())
    return 0


def cmd_claude(args, cfg: Config) -> int:
    from pdf2md import claude_setup as cs

    if args.action == "install-mcp":
        for p in cs.install_desktop_mcp():
            print(f"Claude Desktop: added 'pdf2md' MCP server to {p} (backup saved). Restart Claude Desktop.")
        print(cs.install_claude_code_mcp())
    elif args.action == "plugin-zip":
        out = Path(args.out).expanduser() if args.out else Path.cwd()
        print(f"Cowork plugin: {cs.build_plugin_zip(out)}")
        print(f"Skill only:    {cs.build_skill_zip(out)}")
        print("Upload in Claude Desktop: Customize > Plugins > Upload (or Settings > Capabilities > Skills).")
    elif args.action == "instructions":
        print(cs.GLOBAL_INSTRUCTIONS)
    return 0


def cmd_doctor(args, cfg: Config) -> int:
    from pdf2md.doctor import run_doctor

    return run_doctor(cfg)


def cmd_mcp(args, cfg: Config) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)  # stdout belongs to the MCP protocol
    from pdf2md.mcp_server import run

    run(http=args.http, port=args.port)
    return 0


def cmd_config(args, cfg: Config) -> int:
    if not CONFIG_PATH.exists():
        save_config(cfg)
    print(f"# {CONFIG_PATH}\n")
    print(CONFIG_PATH.read_text(encoding="utf-8"))
    return 0


def cmd_setup(args, cfg: Config) -> int:
    from pdf2md import autostart
    from pdf2md import claude_setup as cs
    from pdf2md.doctor import suggested_vision_model, total_ram_gb
    from pdf2md.ollama import OllamaClient

    y = args.yes
    print(f"pdf2md {__version__} setup\n")

    folders = list(args.folder or [])
    if not folders and not y:
        default = _default_projects_folder()
        print("Which folder holds your Claude/Cowork project folders? Every sub-folder is treated as one project.")
        ans = _ask("Folder", default)
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
        langs = _ask("OCR languages, ISO codes comma-separated (e.g. en,hi,bn)", ",".join(cfg.ocr.languages))
        cfg.ocr.languages = [c.strip() for c in langs.split(",") if c.strip()] or ["en"]

    if not CONFIG_PATH.exists() or args.model:
        cfg.llm.vision_model = args.model or suggested_vision_model()
    print(f"\nRAM {total_ram_gb():.0f} GB -> local vision model: {cfg.llm.vision_model}")
    path = save_config(cfg)
    print(f"Config written: {path}")

    client = OllamaClient(cfg.llm.ollama_url)
    if client.available():
        if not client.has_model(cfg.llm.vision_model) and _yes(f"Download {cfg.llm.vision_model} now (several GB)?", True, y):
            cmd_models(argparse.Namespace(action="pull"), cfg)
    else:
        print("Ollama is not running: figures will be saved as images without descriptions and scans will use "
              "classical OCR until you install it (https://ollama.com/download) and run: pdf2md models pull")

    if _yes("Start the watcher automatically at login?", True, y):
        print(autostart.install())
    if _yes("Add the pdf2md tool to Claude Desktop / Claude Code (MCP)?", True, y):
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


# --------------------------------------------------------------------- main
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
        _setup_logging()
        logging.getLogger("pdf2md").setLevel(logging.WARNING)
    cfg = load_config()
    sys.exit(args.func(args, cfg) or 0)
