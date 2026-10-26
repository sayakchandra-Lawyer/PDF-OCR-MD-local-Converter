"""Project folder layout: where PDFs and their Markdown twins live."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pdf2md import __version__
from pdf2md.config import Config, LayoutConfig, expand
from pdf2md.naming import clean_stem

MARKER_NAME = ".pdf2md.json"
STATE_DIR_NAME = ".pdf2md"
INDEX_NAME = "INDEX.md"
ASSETS_DIR_NAME = "assets"


@dataclass(frozen=True)
class ProjectLayout:
    root: Path  # the project folder
    base: Path  # root, or root/01_Context when that convention is used
    pdf_dir: Path
    md_dir: Path
    move_pdfs: str
    ignore_dirs: tuple[str, ...]

    @classmethod
    def for_root(cls, root: Path, cfg: LayoutConfig) -> "ProjectLayout":
        root = Path(root).resolve()
        base = root
        if cfg.context_dir and (root / cfg.context_dir).is_dir():
            base = root / cfg.context_dir
        return cls(
            root=root,
            base=base,
            pdf_dir=base / cfg.pdf_dir,
            md_dir=base / cfg.md_dir,
            move_pdfs=cfg.move_pdfs,
            ignore_dirs=tuple(cfg.ignore_dirs),
        )

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def state_dir(self) -> Path:
        return self.md_dir / STATE_DIR_NAME

    @property
    def manifest_path(self) -> Path:
        return self.state_dir / "manifest.json"

    @property
    def index_path(self) -> Path:
        return self.md_dir / INDEX_NAME

    @property
    def assets_dir(self) -> Path:
        return self.md_dir / ASSETS_DIR_NAME

    @property
    def marker_path(self) -> Path:
        return self.root / MARKER_NAME

    def rel(self, p: Path) -> str:
        """Path relative to the project root, with forward slashes."""
        return Path(os.path.relpath(Path(p).resolve(), self.root)).as_posix()

    def contains(self, p: Path) -> bool:
        try:
            Path(p).resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def md_path_for(self, pdf: Path) -> Path:
        """MD/<same sub-folders>/<clean stem>.md"""
        pdf = Path(pdf).resolve()
        for anchor in (self.pdf_dir, self.base, self.root):
            try:
                rel = pdf.relative_to(anchor)
                return self.md_dir / rel.with_name(clean_stem(rel.stem) + ".md")
            except ValueError:
                continue
        return self.md_dir / (clean_stem(pdf.stem) + ".md")

    def assets_for(self, md_path: Path) -> Path:
        rel = md_path.relative_to(self.md_dir).with_suffix("")
        return self.assets_dir / rel

    def should_move(self, pdf: Path) -> bool:
        pdf = Path(pdf).resolve()
        if self.pdf_dir in pdf.parents:
            return False
        if self.move_pdfs == "all":
            return True
        if self.move_pdfs == "top-level":
            return pdf.parent in (self.root, self.base)
        return False

    def is_ignored(self, p: Path) -> bool:
        p = Path(p).resolve()
        if p == self.md_dir or self.md_dir in p.parents:
            return True
        try:
            parts = p.relative_to(self.root).parts
        except ValueError:
            return True
        if any(part.startswith(".") for part in parts):
            return True
        if any(part in self.ignore_dirs for part in parts[:-1]):
            return True
        name = parts[-1] if parts else ""
        return name.startswith("~$") or name.startswith("._")

    def iter_pdfs(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            d = Path(dirpath)
            dirnames[:] = [
                n for n in dirnames
                if not n.startswith(".") and n not in self.ignore_dirs and d / n != self.md_dir
            ]
            for fn in filenames:
                if fn.lower().endswith(".pdf"):
                    p = d / fn
                    if not self.is_ignored(p):
                        yield p

    def marker_data(self) -> dict:
        return {
            "tool": "pdf2md",
            "version": __version__,
            "about": "PDFs in this folder are converted locally to Markdown. Read the .md files, not the PDFs.",
            "pdf_dir": self.rel(self.pdf_dir),
            "md_dir": self.rel(self.md_dir),
            "index": self.rel(self.index_path),
            "manifest": self.rel(self.manifest_path),
            "hook": {"wait_seconds": 150},
        }

    def ensure(self) -> None:
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.md_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = self.marker_data()
        try:
            current = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        if current != data:
            atomic_write(self.marker_path, json.dumps(data, indent=2) + "\n")


def find_project(path: Path, cfg: Config) -> ProjectLayout | None:
    """Return the configured project that contains `path`, if any."""
    path = Path(path).resolve()
    parents, projects = cfg.watch_roots()
    # Most specific explicit project wins.
    for proj in sorted(projects, key=lambda p: len(p.parts), reverse=True):
        if path == proj or proj in path.parents:
            return ProjectLayout.for_root(proj, cfg.layout)
    for parent in parents:
        try:
            rel = path.relative_to(parent)
        except ValueError:
            continue
        if len(rel.parts) >= 2 and not rel.parts[0].startswith("."):
            return ProjectLayout.for_root(parent / rel.parts[0], cfg.layout)
    return None


def guess_project(pdf: Path, cfg: Config) -> ProjectLayout:
    """Project for a PDF outside the configured roots: walk up to an existing
    marker, otherwise use the PDF's folder (or its parent when the PDF already
    sits in a pdf_dir)."""
    pdf = Path(pdf).resolve()
    found = find_project(pdf, cfg)
    if found:
        return found
    for d in list(pdf.parents)[:6]:
        if (d / MARKER_NAME).exists():
            return ProjectLayout.for_root(d, cfg.layout)
    parent = pdf.parent
    if parent.name == cfg.layout.pdf_dir:
        parent = parent.parent
        if parent.name == cfg.layout.context_dir:
            parent = parent.parent
    elif parent.name == cfg.layout.context_dir:
        parent = parent.parent
    return ProjectLayout.for_root(parent, cfg.layout)


def iter_projects(cfg: Config):
    parents, projects = cfg.watch_roots()
    seen: set[Path] = set()
    for proj in projects:
        if proj.is_dir() and proj not in seen:
            seen.add(proj)
            yield ProjectLayout.for_root(proj, cfg.layout)
    for parent in parents:
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and child not in seen:
                seen.add(child)
                yield ProjectLayout.for_root(child, cfg.layout)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: target briefly open in another program
            if attempt == 19:
                raise
            time.sleep(0.25)


class FileLock:
    """Tiny cross-process lock based on exclusive file creation."""

    def __init__(self, path: Path, timeout: float = 120.0, stale_after: float = 4 * 3600):
        self.path = Path(path)
        self.timeout = timeout
        self.stale_after = stale_after
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, f"{os.getpid()} {time.time():.0f}".encode())
                return
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_after:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Timed out waiting for lock {self.path}")
                time.sleep(0.2)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def resolve_user_path(raw: str, cfg: Config) -> Path | None:
    """Map a path as Claude sees it (possibly inside the Cowork VM, e.g.
    /sessions/x/mnt/My-Project/PDF/a.pdf) to the real path on this machine."""
    p = Path(expand(raw)) if raw else None
    if p and p.exists():
        return p
    parts = PurePosixPath(raw.replace("\\", "/")).parts
    parents, projects = cfg.watch_roots()
    bases = [*projects, *parents]
    bases += [proj.parent for proj in projects]
    for i in range(len(parts)):
        tail = parts[i:]
        if not tail or tail[0] in ("/", ""):
            continue
        for base in bases:
            cand = base.joinpath(*tail)
            if cand.exists():
                return cand
            # The VM mount name usually equals the project folder name.
            if base.name == tail[0] and len(tail) > 1:
                cand = base.joinpath(*tail[1:])
                if cand.exists():
                    return cand
    return None
