"""Cheap per-page analysis: does a page have a usable text layer or is it a scan?"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

MIN_TEXT_CHARS = 30  # fewer non-space characters than this = "no text layer"
SCAN_COVERAGE = 0.80  # an image covering this much of the page = scanned page


@dataclass
class PageInfo:
    number: int  # 1-based
    chars: int
    image_coverage: float
    kind: str  # "text" | "scanned" | "scanned_text_layer"

    @property
    def needs_ocr(self) -> bool:
        return self.kind != "text"


def _image_coverage(page: pdfium.PdfPage, width: float, height: float) -> float:
    area = 0.0
    for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=3):
        # pypdfium2 >= 5 renamed get_pos() to get_bounds()
        bounds = getattr(obj, "get_bounds", None) or getattr(obj, "get_pos", None)
        try:
            left, bottom, right, top = bounds()
        except Exception:
            continue
        w = max(0.0, min(right, width) - max(left, 0.0))
        h = max(0.0, min(top, height) - max(bottom, 0.0))
        area += w * h
    return min(1.0, area / max(width * height, 1.0))


def classify(chars: int, coverage: float) -> str:
    if chars < MIN_TEXT_CHARS:
        return "scanned"
    if coverage >= SCAN_COVERAGE:
        return "scanned_text_layer"
    return "text"


def inspect_pdf(path: Path) -> list[PageInfo]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            width, height = page.get_size()
            textpage = page.get_textpage()
            text = textpage.get_text_range() if textpage.count_chars() else ""
            chars = sum(1 for c in text if not c.isspace())
            coverage = _image_coverage(page, width, height)
            pages.append(PageInfo(i + 1, chars, round(coverage, 3), classify(chars, coverage)))
            textpage.close()
            page.close()
        return pages
    finally:
        pdf.close()


def pdf_title(path: Path) -> str | None:
    pdf = pdfium.PdfDocument(str(path))
    try:
        title = (pdf.get_metadata_dict().get("Title") or "").strip()
        if title.lower() in {"(anonymous)", "untitled", "untitled document", "microsoft word"}:
            return None
        return title or None
    except Exception:
        return None
    finally:
        pdf.close()


def render_page_png(path: Path, page_number: int, dpi: int = 200, max_side: int = 2200) -> bytes:
    """Render one page (1-based) to PNG bytes for a vision model."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_number - 1]
        width, height = page.get_size()
        scale = dpi / 72.0
        longest = max(width, height) * scale
        if longest > max_side:
            scale *= max_side / longest
        image = page.render(scale=scale).to_pil().convert("RGB")
        page.close()
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()


def page_runs(pages: list[PageInfo], redo_text_layer: bool) -> list[tuple[bool, int, int]]:
    """Group consecutive pages into (needs_full_ocr, first, last) runs."""
    runs: list[tuple[bool, int, int]] = []
    for p in pages:
        ocr = p.kind == "scanned" or (p.kind == "scanned_text_layer" and redo_text_layer)
        if runs and runs[-1][0] == ocr and runs[-1][2] == p.number - 1:
            runs[-1] = (ocr, runs[-1][1], p.number)
        else:
            runs.append((ocr, p.number, p.number))
    return runs
