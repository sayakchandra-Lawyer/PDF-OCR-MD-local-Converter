"""User configuration (~/.pdf2md/config.toml) with sensible defaults."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

APP_DIR = Path(os.environ.get("PDF2MD_HOME", Path.home() / ".pdf2md")).expanduser()
CONFIG_PATH = APP_DIR / "config.toml"
LOG_DIR = APP_DIR / "logs"
WATCHER_STATE = APP_DIR / "watcher.json"

MARKER_ONLY_DEFAULT = [
    "logo",
    "icon",
    "signature",
    "stamp",
    "bar_code",
    "qr_code",
    "page_thumbnail",
]


@dataclass
class WatchConfig:
    # Folders whose sub-folders are project folders (e.g. ".../Claude Projects/01_Active").
    project_parents: list[str] = field(default_factory=list)
    # Individual project folders.
    projects: list[str] = field(default_factory=list)
    # Polling is slower but works on network shares and some cloud-sync folders.
    use_polling: bool = False
    rescan_minutes: float = 10.0
    settle_seconds: float = 3.0


@dataclass
class LayoutConfig:
    pdf_dir: str = "PDF"
    md_dir: str = "MD"
    # If a project contains this folder, PDF/ and MD/ are created inside it.
    context_dir: str = "01_Context"
    # Which PDFs are moved into pdf_dir: "top-level" (dropped directly into the
    # project or context folder), "all" (anywhere in the project) or "never".
    move_pdfs: str = "top-level"
    # Tokens: {stem} original file name, {date} YYYY-MM-DD of import, {project}.
    name_template: str = "{stem}"
    ignore_dirs: list[str] = field(
        default_factory=lambda: ["04_Archive", "node_modules", ".git", "__pycache__"]
    )
    # Keep a short "read the Markdown, not the PDF" block in each project's CLAUDE.md.
    write_claude_md: bool = True
    include_headers_footers: bool = False


@dataclass
class OcrConfig:
    # auto | vlm | ocrmac | rapidocr | tesseract | easyocr
    engine: str = "auto"
    # ISO 639-1 codes, most likely first, e.g. ["en", "hi", "bn"].
    languages: list[str] = field(default_factory=lambda: ["en"])
    # Re-OCR scanned pages that already carry an (often poor) embedded text layer.
    redo_existing_text_layer: bool = True
    # Compare vision-model transcriptions with classical OCR and reject outliers.
    vlm_crosscheck: bool = True
    min_crosscheck_recall: float = 0.55
    vlm_dpi: int = 200


@dataclass
class TablesConfig:
    mode: str = "accurate"  # accurate | fast


@dataclass
class FiguresConfig:
    describe: bool = True
    # Pictures smaller than this fraction of the page are ignored.
    min_area: float = 0.02
    # Picture classes that get a short marker (e.g. "[Signature]") instead of a description.
    marker_only: list[str] = field(default_factory=lambda: list(MARKER_ONLY_DEFAULT))
    save_images: bool = True


@dataclass
class LlmConfig:
    ollama_url: str = "http://127.0.0.1:11434"
    vision_model: str = "qwen3-vl:8b-instruct"
    # Model for transcribing scanned pages; empty = vision_model.
    ocr_model: str = ""
    timeout_seconds: float = 600.0
    keep_alive: str = "15m"
    num_ctx: int = 16384

    @property
    def page_model(self) -> str:
        return self.ocr_model or self.vision_model


@dataclass
class Config:
    watch: WatchConfig = field(default_factory=WatchConfig)
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    tables: TablesConfig = field(default_factory=TablesConfig)
    figures: FiguresConfig = field(default_factory=FiguresConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)

    def watch_roots(self) -> tuple[list[Path], list[Path]]:
        parents = [expand(p) for p in self.watch.project_parents]
        projects = [expand(p) for p in self.watch.projects]
        return parents, projects


def expand(p: str | Path) -> Path:
    return Path(os.path.expandvars(str(p))).expanduser().resolve()


def _apply(section: Any, values: dict[str, Any], name: str) -> None:
    known = {f.name for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            print(f"pdf2md: ignoring unknown config key [{name}] {key}", file=sys.stderr)
            continue
        setattr(section, key, value)


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or CONFIG_PATH
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for f in fields(cfg):
            if f.name in data:
                _apply(getattr(cfg, f.name), data[f.name], f.name)
    return cfg


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


_COMMENTS = {
    "watch": "Folders the background watcher monitors. Each sub-folder of a project_parent is one project.",
    "layout": "Where converted files go inside each project folder, and how PDFs are named.",
    "ocr": "OCR for scanned / image-only pages. engine: auto | vlm | ocrmac | rapidocr | tesseract | easyocr",
    "tables": "Table structure recognition: accurate | fast",
    "figures": "Charts, diagrams and images: described by the local vision model.",
    "llm": "Local vision LLM served by Ollama (https://ollama.com). Nothing leaves your machine.",
}


def dump_config(cfg: Config) -> str:
    lines = ["# pdf2md configuration - edit and restart the watcher (pdf2md autostart restart).", ""]
    for f in fields(cfg):
        section = getattr(cfg, f.name)
        lines.append(f"# {_COMMENTS.get(f.name, '')}")
        lines.append(f"[{f.name}]")
        for sf in fields(section):
            lines.append(f"{sf.name} = {_toml_value(getattr(section, sf.name))}")
        lines.append("")
    return "\n".join(lines)


def save_config(cfg: Config, path: Path | None = None) -> Path:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_config(cfg), encoding="utf-8")
    return path
