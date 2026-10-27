"""`convert`, `scan` and `watch`: running conversions."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from pdf2md import __version__
from pdf2md.config import CONFIG_PATH, Config, expand
from pdf2md.cli.logs import setup_logging


def cmd_convert(args, cfg: Config) -> int:
    from pdf2md.service import Service

    svc = Service(cfg)
    failures = 0
    for raw in args.paths:
        path = expand(raw)
        if path.is_dir():
            from pdf2md.core.layout import ProjectLayout

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
    setup_logging(to_file=True, verbose=args.verbose)
    logging.getLogger("pdf2md").info("pdf2md %s watcher starting (config %s)", __version__, CONFIG_PATH)
    Watcher(cfg).run()
    return 0
