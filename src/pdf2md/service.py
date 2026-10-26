"""High-level operations: organise a PDF into its project, convert it once,
keep the manifest / INDEX.md / CLAUDE.md up to date."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable

from pdf2md import FORMAT_VERSION, __version__
from pdf2md.config import Config
from pdf2md.layout import FileLock, ProjectLayout, atomic_write, guess_project, iter_projects
from pdf2md.manifest import Manifest, hash_file, now_iso, transaction
from pdf2md.naming import render_name

log = logging.getLogger("pdf2md")

CLAUDE_MD_START = "<!-- pdf2md:start -->"
CLAUDE_MD_END = "<!-- pdf2md:end -->"


@dataclass
class DocResult:
    status: str  # converted | cached | duplicate | failed | ignored
    pdf: Path
    md: Path | None
    project: ProjectLayout
    message: str = ""
    entry: dict | None = None

    def summary(self) -> str:
        md = self.project.rel(self.md) if self.md else "-"
        return f"[{self.status}] {self.project.rel(self.pdf)} -> {md} {self.message}".rstrip()


def page_ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in sorted(nums):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)


def _docling_version() -> str:
    try:
        return metadata.version("docling")
    except metadata.PackageNotFoundError:
        return "?"


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._converter = None
        self._lock = threading.RLock()
        self._convert_lock = threading.Lock()  # Docling pipelines are not shared across threads

    @property
    def converter(self):
        if self._converter is None:
            from pdf2md.converter import Converter

            self._converter = Converter(self.cfg)
        return self._converter

    # ------------------------------------------------------------ projects
    def project_for(self, pdf: Path, project: Path | None = None) -> ProjectLayout:
        if project:
            return ProjectLayout.for_root(Path(project), self.cfg.layout)
        return guess_project(pdf, self.cfg)

    def ensure_project(self, layout: ProjectLayout) -> None:
        layout.ensure()
        if not layout.index_path.exists():
            with transaction(layout):
                pass
        if self.cfg.layout.write_claude_md:
            self._write_claude_md(layout)

    def _write_claude_md(self, layout: ProjectLayout) -> None:
        md_dir = layout.rel(layout.md_dir)
        pdf_dir = layout.rel(layout.pdf_dir)
        block = "\n".join(
            [
                CLAUDE_MD_START,
                "## PDFs in this project: read the Markdown versions",
                "",
                f"PDFs here are converted locally to Markdown by pdf2md (originals in `{pdf_dir}/`, "
                f"Markdown in `{md_dir}/`). To save tokens:",
                "",
                f"- Do not open PDFs directly. Find the document in `{md_dir}/INDEX.md` and read its `.md` file.",
                "- The Markdown keeps page markers (`<!-- page N -->`) for citations, tables as Markdown tables, "
                f"and text descriptions with data tables for charts and figures (images in `{md_dir}/assets/`).",
                "- Large documents: search the `.md` (grep) or read only the pages you need.",
                "- No Markdown yet? It is probably still converting: check the Status column in INDEX.md, wait and "
                "re-check, or call the `pdf_to_markdown` tool if it is available.",
                "- Open the original PDF only if the user asks for it, or to visually verify one specific page.",
                CLAUDE_MD_END,
            ]
        )
        path = layout.root / "CLAUDE.md"
        try:
            current = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = ""
        if CLAUDE_MD_START in current and CLAUDE_MD_END in current:
            head, rest = current.split(CLAUDE_MD_START, 1)
            _, tail = rest.split(CLAUDE_MD_END, 1)
            new = head + block + tail
        else:
            new = (current.rstrip() + "\n\n" if current.strip() else "") + block + "\n"
        if new != current:
            atomic_write(path, new)

    # -------------------------------------------------------------- intake
    def _target_name(self, layout: ProjectLayout, pdf: Path) -> Path:
        stem = render_name(self.cfg.layout.name_template, pdf.stem, layout.name)
        return layout.pdf_dir / f"{stem}.pdf"

    def intake(self, pdf: Path, layout: ProjectLayout, move: bool | None = None) -> tuple[Path, str, str | None]:
        """Move a newly dropped PDF into the project's PDF folder (clean name).

        Returns (final_path, sha256, duplicate_of_rel_or_None)."""
        pdf = Path(pdf).resolve()
        sha = hash_file(pdf)
        do_move = layout.should_move(pdf) if move is None else (move and layout.pdf_dir not in pdf.parents)
        if not do_move:
            return pdf, sha, None
        dest = self._target_name(layout, pdf)
        if dest.exists():
            if hash_file(dest) == sha:
                return pdf, sha, layout.rel(dest)
            i = 2
            while True:
                cand = dest.with_name(f"{dest.stem}-{i}.pdf")
                if not cand.exists():
                    dest = cand
                    break
                if hash_file(cand) == sha:
                    return pdf, sha, layout.rel(cand)
                i += 1
        original_rel = layout.rel(pdf)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pdf), str(dest))
        with transaction(layout) as m:
            m.upsert(layout.rel(dest), original_name=pdf.name, aliases=[original_rel], sha256=sha)
        log.info("moved %s -> %s", original_rel, layout.rel(dest))
        return dest, sha, None

    @staticmethod
    def _md_path(layout: ProjectLayout, m: Manifest, rel: str, pdf: Path, entry: dict | None) -> Path:
        """Markdown path for a PDF, keeping an earlier choice and avoiding clashes
        between PDFs whose cleaned names coincide ("a b.pdf" vs "a-b.pdf")."""
        if entry and entry.get("md"):
            return layout.root / entry["md"]
        taken = {e.get("md") for k, e in m.docs.items() if k != rel}
        md = layout.md_path_for(pdf)
        i = 2
        while layout.rel(md) in taken:
            md = md.with_name(f"{layout.md_path_for(pdf).stem}-{i}.md")
            i += 1
        return md

    @staticmethod
    def _is_current(entry: dict | None, sha: str, md_abs: Path) -> bool:
        return bool(
            entry
            and entry.get("status") == "done"
            and entry.get("sha256") == sha
            and entry.get("format_version") == FORMAT_VERSION
            and md_abs.exists()
        )

    @staticmethod
    def _quick_check(layout: ProjectLayout, pdf: Path) -> DocResult | None:
        """Answer from the manifest without hashing when size and mtime are
        unchanged (keeps rescans cheap and avoids downloading cloud-only files)."""
        if layout.should_move(pdf):
            return None
        entry = Manifest.load(layout).get(layout.rel(pdf))
        if not entry:
            return None
        st = pdf.stat()
        if entry.get("size") != st.st_size or entry.get("mtime") != int(st.st_mtime):
            return None
        md = layout.root / entry.get("md", "")
        if entry.get("status") == "done" and entry.get("format_version") == FORMAT_VERSION and md.is_file():
            return DocResult("cached", pdf, md, layout, "", entry)
        if entry.get("status") == "failed" and entry.get("attempts", 0) >= 2:
            return DocResult("failed", pdf, None, layout, "failed twice before; use --force to retry", entry)
        return None

    # ------------------------------------------------------------- process
    def process(
        self,
        pdf: Path,
        project: Path | None = None,
        force: bool = False,
        move: bool | None = None,
        describe_figures: bool | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> DocResult:
        pdf = Path(pdf).resolve()
        layout = self.project_for(pdf, project)
        if layout.is_ignored(pdf):
            return DocResult("ignored", pdf, None, layout, "inside an ignored folder")
        with self._lock:
            self.ensure_project(layout)
            if not force and move is not True:
                quick = self._quick_check(layout, pdf)
                if quick:
                    return quick
            pdf, sha, duplicate_of = self.intake(pdf, layout, move)
            if duplicate_of:
                with transaction(layout) as m:
                    m.upsert(duplicate_of, aliases=[layout.rel(pdf)])
                    entry = m.get(duplicate_of)
                md = layout.root / entry["md"] if entry and entry.get("md") else None
                return DocResult("duplicate", pdf, md, layout, f"same content as {duplicate_of}", entry)
            rel = layout.rel(pdf)
            with transaction(layout) as m:
                entry = m.get(rel)
                md_path = self._md_path(layout, m, rel, pdf, entry)
                if not force and self._is_current(entry, sha, md_path):
                    return DocResult("cached", pdf, md_path, layout, "", entry)
                m.upsert(rel, status="queued", sha256=sha, md=layout.rel(md_path), original_name=(entry or {}).get("original_name", pdf.name))

        # One conversion per document at a time, across processes (watcher + MCP server).
        with FileLock(layout.state_dir / "locks" / f"{sha[:24]}.lock", timeout=4 * 3600):
            entry = Manifest.load(layout).get(rel)
            if not force and self._is_current(entry, sha, md_path):
                return DocResult("cached", pdf, md_path, layout, "", entry)
            with transaction(layout) as m:
                m.upsert(rel, status="converting", started_at=now_iso(), error=None)
            try:
                with self._convert_lock:
                    result = self.converter.convert(
                        pdf, md_path, layout.assets_for(md_path), describe_figures, progress
                    )
            except Exception as e:  # noqa: BLE001 - recorded in the manifest
                log.exception("conversion failed for %s", rel)
                st = pdf.stat()
                with transaction(layout) as m:
                    attempts = (m.get(rel) or {}).get("attempts", 0) + 1
                    entry = m.upsert(
                        rel, status="failed", error=f"{type(e).__name__}: {e}", attempts=attempts,
                        size=st.st_size, mtime=int(st.st_mtime),
                    )
                return DocResult("failed", pdf, None, layout, str(e), entry)
            text = self._render_document(layout, pdf, sha, result, entry)
            atomic_write(md_path, text)
            st = pdf.stat()
            with transaction(layout) as m:
                entry = m.upsert(
                    rel,
                    status="done",
                    size=st.st_size,
                    mtime=int(st.st_mtime),
                    attempts=0,
                    md=layout.rel(md_path),
                    sha256=sha,
                    format_version=FORMAT_VERSION,
                    title=result.title,
                    pages=len(result.pages),
                    ocr_pages=result.ocr_pages,
                    vision_ocr_pages=result.vlm_pages,
                    tables=result.tables,
                    figures=result.figures,
                    figures_described=result.figures_described,
                    ocr_engine=result.ocr_engine,
                    vision_model=result.vision_model,
                    warnings=result.warnings,
                    seconds=result.seconds,
                    md_chars=len(text),
                    converted_at=now_iso(),
                    error=None,
                )
        log.info("converted %s in %.1fs (%d pages)", rel, result.seconds, len(result.pages))
        return DocResult("converted", pdf, md_path, layout, f"({len(result.pages)} page{'s' if len(result.pages) != 1 else ''}, {result.seconds}s)", entry)

    def _render_document(self, layout: ProjectLayout, pdf: Path, sha: str, r, entry: dict | None) -> str:
        rel_pdf = layout.rel(pdf)
        original = (entry or {}).get("original_name") or pdf.name
        fm = {
            "title": r.title,
            "source_pdf": rel_pdf,
            "original_filename": original,
            "sha256": sha,
            "pages": len(r.pages),
            "ocr_pages": r.ocr_pages,
            "vision_ocr_pages": r.vlm_pages,
            "tables": r.tables,
            "figures": r.figures,
            "ocr_engine": r.ocr_engine,
            "vision_model": r.vision_model,
            "converted_at": now_iso(),
            "converter": f"pdf2md {__version__} / docling {_docling_version()}",
            "warnings": r.warnings,
        }
        lines = ["---"]
        for k, v in fm.items():
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        lines.append("---")
        n_pages = f"{len(r.pages)} page" + ("s" if len(r.pages) != 1 else "")
        notes = [f"Local Markdown conversion of `{rel_pdf}` ({n_pages}). Read this instead of the PDF."]
        notes.append("Page boundaries are marked `<!-- page N -->`; tables are Markdown tables")
        if r.figures:
            notes[-1] += "; charts and figures are described in text with extracted data (images in `assets/`)"
        notes[-1] += "."
        if r.ocr_pages:
            how = f"OCR ({r.ocr_engine})"
            if r.vlm_pages:
                how = f"the local vision model on pages {page_ranges(r.vlm_pages)} and {how} elsewhere"
            which = "Page" if len(r.ocr_pages) == 1 else "Pages"
            verb = "was a scanned image" if len(r.ocr_pages) == 1 else "were scanned images"
            notes.append(f"{which} {page_ranges(r.ocr_pages)} {verb}, transcribed with {how}.")
        if r.warnings:
            notes.append("Conversion warnings are listed in the front matter.")
        header = "\n".join(lines) + "\n\n" + f"# {r.title}\n\n> " + " ".join(notes) + "\n\n"
        return header + r.markdown

    # ----------------------------------------------------------------- scans
    def scan_project(self, layout: ProjectLayout, force: bool = False, progress=None) -> list[DocResult]:
        results = []
        for pdf in sorted(layout.iter_pdfs()):
            results.append(self.process(pdf, layout.root, force=force, progress=progress))
        self.mark_missing(layout)
        return results

    def mark_missing(self, layout: ProjectLayout) -> None:
        if not layout.manifest_path.exists():
            return
        m = Manifest.load(layout)
        gone = [rel for rel, e in m.docs.items() if not (layout.root / rel).exists() and e.get("status") != "missing"]
        if gone:
            with transaction(layout) as m:
                for rel in gone:
                    m.upsert(rel, status="missing")

    def scan_all(self, force: bool = False, progress=None) -> list[DocResult]:
        results = []
        for layout in iter_projects(self.cfg):
            results.extend(self.scan_project(layout, force=force, progress=progress))
        return results
