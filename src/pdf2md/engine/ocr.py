"""Choice and configuration of the classical OCR engine used by Docling."""

from __future__ import annotations

import importlib.util
import shutil
import sys

from pdf2md.config import Config


def installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def pick_ocr_engine(cfg: Config) -> str:
    """Classical OCR engine used for scanned pages (and as cross-check for the
    vision model)."""
    engine = cfg.ocr.engine.lower()
    if engine not in ("auto", "vlm"):
        return engine
    if sys.platform == "darwin" and installed("ocrmac"):
        return "ocrmac"  # Apple Vision framework: fast and accurate
    multilingual = len(cfg.ocr.languages) > 1
    if multilingual and shutil.which("tesseract"):
        return "tesseract"
    if installed("rapidocr") and installed("onnxruntime"):
        return "rapidocr"
    if shutil.which("tesseract"):
        return "tesseract"
    if installed("easyocr"):
        return "easyocr"
    return "rapidocr"


def ocr_options(engine: str, languages: list[str], full_page: bool):
    """Docling OCR options for `engine`; full_page re-OCRs the whole page image."""
    from docling.datamodel import pipeline_options as po

    mode = po.OcrMode.FULL_PAGE if full_page else po.OcrMode.DEFAULT
    langs = [c if ":" in c else f"iso:{c}" for c in (languages or ["en"])]
    if engine == "ocrmac":
        return po.OcrMacOptions(lang=langs, mode=mode)
    if engine == "tesseract":
        if installed("tesserocr"):
            return po.TesseractOcrOptions(lang=langs, mode=mode)
        return po.TesseractCliOcrOptions(lang=langs, mode=mode)
    if engine == "easyocr":
        return po.EasyOcrOptions(lang=langs, mode=mode)
    return po.RapidOcrOptions(lang=langs[:1], mode=mode)
