"""Per-project manifest (machine readable) and INDEX.md (Claude readable)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath

from pdf2md.layout import FileLock, ProjectLayout, atomic_write

STATUS_LABEL = {
    "done": "done",
    "queued": "queued",
    "converting": "converting...",
    "failed": "FAILED",
    "missing": "source PDF missing",
}


class Manifest:
    def __init__(self, layout: ProjectLayout, data: dict | None = None):
        self.layout = layout
        self.data = data or {"version": 1, "project": layout.name, "documents": {}}

    @classmethod
    def load(cls, layout: ProjectLayout) -> "Manifest":
        try:
            data = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            data.setdefault("documents", {})
        except (OSError, ValueError):
            data = None
        return cls(layout, data)

    @property
    def docs(self) -> dict[str, dict]:
        return self.data["documents"]

    def get(self, rel_pdf: str) -> dict | None:
        return self.docs.get(rel_pdf)

    def upsert(self, rel_pdf: str, **fields) -> dict:
        entry = self.docs.setdefault(rel_pdf, {"pdf": rel_pdf, "aliases": []})
        aliases = fields.pop("aliases", None) or []
        for a in aliases:
            if a and a != rel_pdf and a not in entry["aliases"]:
                entry["aliases"].append(a)
        entry.update(fields)
        entry["updated_at"] = now_iso()
        return entry

    def find(self, query: str) -> tuple[str, dict] | None:
        """Find an entry by project-relative path, original path/alias, file
        name or stem (case-insensitive)."""
        q = query.replace("\\", "/").strip().lower()
        if not q:
            return None
        q_name = PurePosixPath(q).name
        q_stem = q_name[:-4] if q_name.endswith(".pdf") else q_name
        best = None
        for rel, e in self.docs.items():
            candidates = [rel, *e.get("aliases", []), e.get("md", "")]
            for c in candidates:
                c = c.lower()
                if c == q or q.endswith("/" + c):
                    return rel, e
                name = PurePosixPath(c).name
                if name == q_name or PurePosixPath(name).stem == q_stem:
                    best = best or (rel, e)
        return best

    def save(self) -> None:
        atomic_write(self.layout.manifest_path, json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")
        write_index(self)


@contextmanager
def transaction(layout: ProjectLayout):
    """Load the manifest under a project lock, yield it and save on success."""
    layout.state_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(layout.state_dir / "manifest.lock", timeout=60):
        m = Manifest.load(layout)
        yield m
        m.save()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _md_link(layout: ProjectLayout, entry: dict) -> str:
    md_rel = entry.get("md")
    if not md_rel:
        return "-"
    md_abs = layout.root / md_rel
    try:
        rel_to_md_dir = md_abs.relative_to(layout.md_dir).as_posix()
    except ValueError:
        rel_to_md_dir = md_rel
    label = PurePosixPath(rel_to_md_dir).name
    return f"[{label}](<{rel_to_md_dir}>)"


def write_index(m: Manifest) -> None:
    layout = m.layout
    rows = []
    for rel, e in sorted(m.docs.items(), key=lambda kv: kv[0].lower()):
        status = STATUS_LABEL.get(e.get("status", ""), e.get("status", "?"))
        if e.get("status") == "failed" and e.get("error"):
            status += f": {str(e['error'])[:80]}"
        if e.get("warnings"):
            status += f" ({len(e['warnings'])} warning(s))"
        scanned = e.get("ocr_pages") or []
        rows.append(
            "| {md} | `{pdf}` | {pages} | {scanned} | {tables} | {figures} | {status} | {when} |".format(
                md=_md_link(layout, e),
                pdf=rel,
                pages=e.get("pages", "?"),
                scanned=len(scanned),
                tables=e.get("tables", "-"),
                figures=e.get("figures", "-"),
                status=status,
                when=(e.get("converted_at") or "")[:16].replace("T", " "),
            )
        )
    md_dir_rel = layout.rel(layout.md_dir)
    text = "\n".join(
        [
            f"# PDF -> Markdown index: {layout.name}",
            "",
            "_Maintained automatically by pdf2md (runs locally). Read the Markdown files listed here "
            "instead of opening the PDFs: they contain the full text, tables as Markdown tables, and "
            "text descriptions (with data tables) of charts and figures. Page boundaries are marked "
            f"`<!-- page N -->`. Links are relative to `{md_dir_rel}/`._",
            "",
            "| Markdown | Source PDF | Pages | OCR pages | Tables | Figures | Status | Converted |",
            "|---|---|---|---|---|---|---|---|",
            *rows,
            "",
        ]
    )
    atomic_write(layout.index_path, text)


def hash_file(path: Path, chunk: int = 1 << 20) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
