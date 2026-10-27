"""`progress` and `status`: what has been converted and what is left."""

from __future__ import annotations

from pdf2md.config import Config


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} day{'s' if d != 1 else ''} {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{max(m, 1)} min"


def progress_report(cfg: Config, project_filter: str | None = None) -> str:
    from pdf2md.core.layout import iter_projects
    from pdf2md.core.manifest import Manifest
    from pdf2md.engine.pdfinspect import inspect_pdf
    from pdf2md.watcher import watcher_state

    totals = {"done": 0, "converting": 0, "waiting": 0, "failed": 0}
    pages_done = pages_left = 0
    secs = secs_pages = 0.0
    unknown_pages = 0
    rows, failed = [], []
    for layout in iter_projects(cfg):
        if project_filter and project_filter.lower() not in layout.name.lower():
            continue
        pdfs = list(layout.iter_pdfs())
        if not pdfs and not layout.manifest_path.exists():
            continue
        m = Manifest.load(layout)
        p_done = 0
        for pdf in pdfs:
            hit = m.find(layout.rel(pdf))
            e = hit[1] if hit else {}
            status = e.get("status")
            if status == "done":
                kind = "done"
                pages_done += e.get("pages") or 0
                if e.get("seconds") and e.get("pages"):
                    secs += e["seconds"]
                    secs_pages += e["pages"]
            elif status == "converting":
                kind = "converting"
            elif status == "failed":
                kind = "failed"
                failed.append(f"{layout.name} / {layout.rel(pdf)}: {str(e.get('error', ''))[:100]}")
            else:
                kind = "waiting"
            if kind in ("waiting", "converting"):
                try:
                    pages_left += len(inspect_pdf(pdf))
                except Exception:  # noqa: BLE001 - unreadable/encrypted PDFs are counted as 1 page
                    pages_left += 1
                    unknown_pages += 1
            totals[kind] += 1
            p_done += kind == "done"
        rows.append((len(pdfs) - p_done, layout.name, p_done, len(pdfs)))

    total = sum(totals.values())
    pct = 100 * totals["done"] / total if total else 100.0
    bar_len = 30
    filled = int(bar_len * pct / 100)
    st = watcher_state()
    alive = bool(st and st.get("alive"))
    lines = [
        f"Background converter: {'RUNNING' if alive else 'NOT RUNNING  (start it: pdf2md autostart install)'}",
    ]
    if alive and st.get("current"):
        current = str(st["current"])
        lines.append(f"Working on:  {current if len(current) < 160 else '...' + current[-157:]}")
    lines += [
        "",
        f"Files:  {totals['done']} of {total} converted  ({pct:.1f}%)",
        f"        [{'#' * filled}{'.' * (bar_len - filled)}]",
        f"        converting now: {totals['converting']}   waiting: {totals['waiting']}   failed: {totals['failed']}",
        f"Pages:  {pages_done:,} done, about {pages_left:,} left",
    ]
    if secs_pages:
        per_page = secs / secs_pages
        lines.append(f"Speed:  about {per_page:.0f} s per page so far")
        if pages_left:
            lines.append(f"Time left: roughly {fmt_duration(per_page * pages_left)} (estimate; scanned pages are slower)")
    remaining = sorted((r for r in rows if r[0] > 0), reverse=True)
    if remaining:
        lines += ["", "Projects with files still to convert:"]
        for left, name, done, n in remaining[:15]:
            lines.append(f"  {done:>4} / {n:<4} done   {name}")
        if len(remaining) > 15:
            lines.append(f"  ... and {len(remaining) - 15} more project(s)")
    if failed:
        lines += ["", "Failed (retry with: pdf2md convert --force \"<file>\"):"]
        lines += [f"  {f}" for f in failed[:20]]
    return "\n".join(lines)


def cmd_progress(args, cfg: Config) -> int:
    import os
    import time

    if not args.live:
        print(progress_report(cfg, args.project))
        return 0
    try:
        while True:
            report = progress_report(cfg, args.project)
            os.system("cls" if os.name == "nt" else "clear")
            print(report)
            print(f"\n(refreshing every {args.every} s - press Ctrl+C to stop)")
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0


def cmd_status(args, cfg: Config) -> int:
    from pdf2md.integrations.autostart import status
    from pdf2md.core.layout import iter_projects
    from pdf2md.core.manifest import Manifest

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
