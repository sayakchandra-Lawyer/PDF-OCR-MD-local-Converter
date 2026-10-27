"""MCP server so Claude (Desktop / Cowork / Code) can convert and read PDFs as Markdown.

Run with:  pdf2md mcp            (stdio, used by claude_desktop_config.json)
           pdf2md mcp --http     (streamable HTTP on 127.0.0.1:8765, experimental)
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path

from pdf2md.config import Config, load_config
from pdf2md.layout import find_project, guess_project, iter_projects, resolve_user_path
from pdf2md.manifest import Manifest
from pdf2md.service import DocResult, Service

INSTRUCTIONS = """Local PDF -> Markdown converter (runs entirely on the user's machine).
Whenever you need the contents of a PDF, call pdf_to_markdown (or read_markdown for an already converted
document) instead of reading the PDF itself: the Markdown keeps all text, tables as Markdown tables, figure
and chart descriptions with data, and <!-- page N --> markers. Converted files are cached, so repeated
calls are cheap. Read only the pages you need with read_markdown(pages="3-5")."""

PAGE_MARK = re.compile(r"<!-- page (\d+) -->")


class Jobs:
    """Background conversions, so tool calls never exceed the client's timeout."""

    def __init__(self, service: Service):
        self.service = service
        self._jobs: dict[Path, dict] = {}
        self._lock = threading.Lock()

    def start(self, pdf: Path, force: bool) -> dict:
        with self._lock:
            job = self._jobs.get(pdf)
            if job and not job["done"].is_set():
                return job
            job = {"done": threading.Event(), "progress": "starting", "result": None, "error": None, "t0": time.time()}
            self._jobs[pdf] = job

        def run():
            try:
                job["result"] = self.service.process(
                    pdf, force=force, progress=lambda m: job.__setitem__("progress", m)
                )
            except Exception as e:  # noqa: BLE001
                job["error"] = f"{type(e).__name__}: {e}"
            finally:
                job["done"].set()

        threading.Thread(target=run, daemon=True).start()
        return job


def _parse_pages(spec: str) -> set[int]:
    pages: set[int] = set()
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        else:
            pages.add(int(part))
    return pages


def select_pages(markdown: str, spec: str) -> str:
    """Keep the front matter / header plus only the requested pages."""
    if not spec.strip():
        return markdown
    wanted = _parse_pages(spec)
    pieces = PAGE_MARK.split(markdown)
    out = [pieces[0]]
    for i in range(1, len(pieces), 2):
        if int(pieces[i]) in wanted:
            out.append(f"<!-- page {pieces[i]} -->{pieces[i + 1]}")
    return "".join(out)


def _truncate(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        last = list(PAGE_MARK.finditer(cut))
        next_page = int(last[-1].group(1)) if last else 1
        return cut + (
            f"\n\n[... truncated at {max_chars} characters. Continue with read_markdown(pages=\"{next_page}-\")"
            " or a narrower page range ...]"
        )
    return text


def _describe(res: DocResult, include_content: bool, max_chars: int) -> str:
    e = res.entry or {}
    lines = [f"status: {res.status}"]
    if res.message:
        lines.append(f"note: {res.message}")
    lines.append(f"project folder: {res.project.root}")
    lines.append(f"pdf: {res.project.rel(res.pdf)}")
    if res.md:
        lines.append(f"markdown: {res.project.rel(res.md)}  (path relative to the project folder)")
        lines.append(f"markdown (absolute, this machine): {res.md}")
    for key in ("pages", "ocr_pages", "vision_ocr_pages", "tables", "figures", "warnings"):
        if e.get(key):
            lines.append(f"{key}: {e[key]}")
    text = "\n".join(lines)
    if include_content and res.md and res.md.exists():
        text += "\n\n" + _truncate(res.md.read_text(encoding="utf-8"), max_chars)
    return text


def _all_entries(cfg: Config):
    for layout in iter_projects(cfg):
        if layout.manifest_path.exists():
            m = Manifest.load(layout)
            for rel, e in m.docs.items():
                yield layout, rel, e


def build_server(cfg: Config | None = None):
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:  # pragma: no cover - mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server

    cfg = cfg or load_config()
    service = Service(cfg)
    jobs = Jobs(service)
    server = Server("pdf2md", instructions=INSTRUCTIONS)

    @server.tool()
    async def pdf_to_markdown(
        pdf_path: str,
        include_content: bool = True,
        force: bool = False,
        wait_seconds: int = 50,
        max_chars: int = 60000,
    ) -> str:
        """Convert a PDF to Markdown locally (OCR for scans, tables, chart descriptions) or return the cached
        conversion. Moves loose PDFs into the project's PDF folder and writes the .md into its MD folder.

        pdf_path: path or file name of the PDF. Paths as seen inside Cowork (e.g. /sessions/.../mnt/<folder>/x.pdf)
          are mapped to this machine automatically.
        include_content: return the Markdown text. Set false if you can read the .md file yourself.
        force: re-convert even if a current conversion exists.
        wait_seconds: how long to wait for a conversion; if it takes longer, call again to collect the result.
        """
        pdf = resolve_user_path(pdf_path, cfg)
        if pdf is None:
            hit = _find(cfg, pdf_path)
            if hit:
                layout, rel, e = hit
                pdf = layout.root / rel
        if pdf is None or not pdf.exists():
            return (
                f"PDF not found on this machine: {pdf_path}. Make sure it is inside a folder pdf2md watches "
                "(see converter_status) or pass the full local path."
            )
        wait = max(1, min(wait_seconds, 300))
        layout = find_project(pdf, cfg)
        if not force and layout is not None and _watcher_alive():
            # The background watcher owns conversions in watched folders: just wait for its result,
            # so heavy OCR never runs inside the Claude app's process.
            return await asyncio.to_thread(_await_watcher, layout, pdf, wait, include_content, max_chars)
        job = jobs.start(pdf.resolve(), force)
        await asyncio.to_thread(job["done"].wait, wait)
        if not job["done"].is_set():
            return (
                f"status: converting ({job['progress']}); {int(time.time() - job['t0'])}s elapsed.\n"
                "Large or scanned PDFs can take a few minutes. Call pdf_to_markdown again with the same path to "
                "collect the result (it will not restart the conversion)."
            )
        if job["error"]:
            return f"status: failed\nerror: {job['error']}"
        return _describe(job["result"], include_content, max_chars)

    @server.tool()
    async def read_markdown(document: str, pages: str = "", max_chars: int = 60000) -> str:
        """Read an already converted document. document: PDF or .md file name/path (fuzzy match on name).
        pages: optional page selection such as "3", "3-5" or "1,4-6" (uses the <!-- page N --> markers)."""
        hit = _find(cfg, document)
        if not hit:
            return f"No converted document matches '{document}'. Use list_converted or pdf_to_markdown."
        layout, rel, e = hit
        md = layout.root / e.get("md", "")
        if e.get("status") != "done" or not md.exists():
            return f"'{rel}' is not converted yet (status: {e.get('status')}). Call pdf_to_markdown('{layout.root / rel}')."
        text = md.read_text(encoding="utf-8")
        if pages.strip().endswith("-"):
            pages = pages.strip() + str(e.get("pages", 9999))
        return _truncate(select_pages(text, pages), max_chars)

    @server.tool()
    async def list_converted(query: str = "", project: str = "") -> str:
        """List converted PDFs (all watched projects), optionally filtered by text in the file name/title or
        by project folder name."""
        q, pq = query.lower(), project.lower()
        rows = []
        for layout, rel, e in _all_entries(cfg):
            hay = f"{rel} {e.get('title', '')} {' '.join(e.get('aliases', []))}".lower()
            if (q and q not in hay) or (pq and pq not in layout.name.lower()):
                continue
            rows.append(
                f"- [{e.get('status')}] {layout.name} :: {rel} -> {e.get('md', '-')} "
                f"({e.get('pages', '?')} pages; title: {e.get('title', '')})"
            )
        return "\n".join(rows[:300]) or "No converted documents found."

    @server.tool()
    async def convert_folder(folder: str, force: bool = False) -> str:
        """Queue every PDF inside a project folder for conversion (runs in the background)."""
        path = resolve_user_path(folder, cfg)
        if path is None or not path.is_dir():
            return f"Folder not found on this machine: {folder}"
        parents, _ = cfg.watch_roots()
        if path.resolve() in parents:
            return (
                f"{path} is the folder that holds all project folders; convert one project folder at a time "
                "(the background watcher already handles every project in it)."
            )
        layout = guess_project(path / "x.pdf", cfg)
        if find_project(path / "x.pdf", cfg) is not None and _watcher_alive():
            m = Manifest.load(layout)
            counts: dict[str, int] = {}
            for e in m.docs.values():
                counts[e.get("status", "?")] = counts.get(e.get("status", "?"), 0) + 1
            return (
                f"The background watcher converts PDFs in {layout.root} automatically. Current status: "
                f"{counts or 'nothing recorded yet'}. Use list_converted(project='{layout.name}') to follow progress."
            )
        pdfs = list(layout.iter_pdfs())
        for p in pdfs:
            jobs.start(p.resolve(), force)
        return f"Queued {len(pdfs)} PDF(s) in {layout.root}. Check progress with list_converted(project='{layout.name}')."

    @server.tool()
    async def converter_status() -> str:
        """Health check: local vision model (Ollama), OCR engine, watched folders and the background watcher."""
        from pdf2md.converter import pick_ocr_engine
        from pdf2md.watcher import watcher_state

        vs = await asyncio.to_thread(service.converter.vision_status)
        parents, projects = cfg.watch_roots()
        st = watcher_state()
        return "\n".join(
            [
                f"ollama running: {vs['ollama']}",
                f"vision model for figures: {vs['vision_model'] or 'NOT available (' + cfg.llm.vision_model + ')'}",
                f"vision model for scanned pages: {vs['page_model'] or 'not available - classical OCR only'}",
                f"classical OCR engine: {pick_ocr_engine(cfg)}",
                f"watched project parents: {[str(p) for p in parents]}",
                f"watched projects: {[str(p) for p in projects]}",
                f"background watcher: {'running' if st and st.get('alive') else 'not running'}"
                + (f" (current: {st.get('current')})" if st and st.get("current") else ""),
            ]
        )

    return server


def _watcher_alive() -> bool:
    from pdf2md.watcher import watcher_state

    st = watcher_state()
    return bool(st and st.get("alive"))


def _await_watcher(layout, pdf: Path, wait: float, include_content: bool, max_chars: int) -> str:
    """Poll the project manifest until the watcher has converted `pdf` (it may also move it)."""
    original_rel = layout.rel(pdf)
    deadline = time.monotonic() + wait
    entry = None
    while True:
        hit = Manifest.load(layout).find(original_rel)
        rel, entry = hit if hit else (None, None)
        status = (entry or {}).get("status")
        md = layout.root / entry["md"] if entry and entry.get("md") else None
        if status == "done" and md and md.is_file():
            res = DocResult("cached", layout.root / rel, md, layout, "converted by the background watcher", entry)
            return _describe(res, include_content, max_chars)
        if status == "failed":
            return f"status: failed\nerror: {entry.get('error')}\nRetry with pdf_to_markdown(force=true)."
        if time.monotonic() >= deadline:
            from pdf2md.watcher import watcher_state

            current = (watcher_state() or {}).get("current") or "waiting in queue"
            return (
                f"status: {status or 'queued'} - the background converter is working on it ({current}).\n"
                "Scanned pages and the first run after installation are slow. Call pdf_to_markdown again "
                "with the same path in a minute to collect the result; this does not restart anything."
            )
        time.sleep(2)


def _find(cfg: Config, query: str):
    q = query.replace("\\", "/")
    best = None
    for layout in iter_projects(cfg):
        if not layout.manifest_path.exists():
            continue
        hit = Manifest.load(layout).find(q)
        if hit:
            rel, e = hit
            # Prefer a project whose name appears in the query path.
            if layout.name.lower() in q.lower():
                return layout, rel, e
            best = best or (layout, rel, e)
    return best


def run(http: bool = False, port: int = 8765) -> None:
    server = build_server()
    if not http:
        server.run()
        return
    try:
        server.run("streamable-http", host="127.0.0.1", port=port)
    except TypeError:  # mcp 1.x keeps host/port in settings
        server.settings.host, server.settings.port = "127.0.0.1", port
        server.run("streamable-http")
