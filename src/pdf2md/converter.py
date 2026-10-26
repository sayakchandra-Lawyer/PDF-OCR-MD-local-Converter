"""PDF -> Markdown conversion: Docling (layout, tables, OCR) + local vision LLM."""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pdf2md import vision
from pdf2md.config import Config
from pdf2md.ollama import OllamaClient, OllamaError
from pdf2md.pdfinspect import PageInfo, inspect_pdf, page_runs, pdf_title, render_page_png

log = logging.getLogger("pdf2md")

KIND_LABEL = {
    "bar_chart": "bar chart", "line_chart": "line chart", "pie_chart": "pie chart",
    "scatter_plot": "scatter plot", "flow_chart": "flow chart", "table": "table image",
    "table_image": "table image", "geographical_map": "map", "photograph": "photo",
    "screenshot_from_computer": "screenshot", "screenshot_from_manual": "screenshot",
    "engineering_drawing": "drawing", "stamp": "stamp / seal", "signature": "signature",
}


@dataclass
class ConversionResult:
    markdown: str
    title: str
    pages: list[PageInfo]
    ocr_pages: list[int] = field(default_factory=list)
    vlm_pages: list[int] = field(default_factory=list)
    tables: int = 0
    figures: int = 0
    figures_described: int = 0
    ocr_engine: str = ""
    vision_model: str = ""
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def pick_ocr_engine(cfg: Config) -> str:
    """Classical OCR engine used for scanned pages (and as cross-check for the
    vision model)."""
    engine = cfg.ocr.engine.lower()
    if engine not in ("auto", "vlm"):
        return engine
    if sys.platform == "darwin" and _installed("ocrmac"):
        return "ocrmac"  # Apple Vision framework: fast and accurate
    multilingual = len(cfg.ocr.languages) > 1
    if multilingual and shutil.which("tesseract"):
        return "tesseract"
    if _installed("rapidocr") and _installed("onnxruntime"):
        return "rapidocr"
    if shutil.which("tesseract"):
        return "tesseract"
    if _installed("easyocr"):
        return "easyocr"
    return "rapidocr"


def _ocr_options(engine: str, languages: list[str], full_page: bool):
    from docling.datamodel import pipeline_options as po

    mode = po.OcrMode.FULL_PAGE if full_page else po.OcrMode.DEFAULT
    langs = [c if ":" in c else f"iso:{c}" for c in (languages or ["en"])]
    if engine == "ocrmac":
        return po.OcrMacOptions(lang=langs, mode=mode)
    if engine == "tesseract":
        if _installed("tesserocr"):
            return po.TesseractOcrOptions(lang=langs, mode=mode)
        return po.TesseractCliOcrOptions(lang=langs, mode=mode)
    if engine == "easyocr":
        return po.EasyOcrOptions(lang=langs, mode=mode)
    return po.RapidOcrOptions(lang=langs[:1], mode=mode)


def _picture_class(pic: Any) -> tuple[str, float]:
    meta = getattr(pic, "meta", None)
    cls_field = getattr(meta, "classification", None) if meta else None
    preds = list(getattr(cls_field, "predictions", None) or [])
    if not preds:  # older docling-core
        for ann in getattr(pic, "annotations", []) or []:
            preds = list(getattr(ann, "predicted_classes", None) or [])
            if preds:
                break
    if not preds:
        return "", 0.0
    best = max(preds, key=lambda p: getattr(p, "confidence", 0.0))
    return getattr(best, "class_name", ""), float(getattr(best, "confidence", 0.0))


class Converter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = OllamaClient(
            cfg.llm.ollama_url, cfg.llm.timeout_seconds, cfg.llm.keep_alive, cfg.llm.num_ctx
        )
        self._docling: dict[tuple[str, bool], Any] = {}

    # ---------------------------------------------------------------- docling
    def _docling_converter(self, engine: str, full_page: bool):
        key = (engine, full_page)
        if key not in self._docling:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.document_converter import DocumentConverter, PdfFormatOption

            opts = PdfPipelineOptions()
            opts.do_ocr = True
            opts.ocr_options = _ocr_options(engine, self.cfg.ocr.languages, full_page)
            opts.do_table_structure = True
            opts.table_structure_options.mode = (
                TableFormerMode.FAST if self.cfg.tables.mode == "fast" else TableFormerMode.ACCURATE
            )
            opts.do_picture_classification = True
            opts.generate_picture_images = True
            opts.images_scale = 2.0
            self._docling[key] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
        return self._docling[key]

    def _run_docling(self, pdf: Path, engine: str, full_page: bool, first: int, last: int, warnings: list[str]):
        from docling.datamodel.base_models import ConversionStatus

        try:
            res = self._docling_converter(engine, full_page).convert(
                str(pdf), page_range=(first, last), raises_on_error=False
            )
        except Exception as e:  # engine-specific failure: fall back to RapidOCR
            if engine == "rapidocr":
                raise
            warnings.append(f"OCR engine {engine} failed on pages {first}-{last} ({e}); used rapidocr")
            return self._run_docling(pdf, "rapidocr", full_page, first, last, warnings)
        if res.status == ConversionStatus.FAILURE:
            errs = "; ".join(str(getattr(e, "error_message", e)) for e in res.errors) or "unknown error"
            raise RuntimeError(f"Docling failed on pages {first}-{last}: {errs}")
        if res.status == ConversionStatus.PARTIAL_SUCCESS:
            errs = "; ".join(str(getattr(e, "error_message", e)) for e in res.errors)
            warnings.append(f"Pages {first}-{last} converted partially: {errs[:300]}")
        return res.document

    # ------------------------------------------------------------- vision llm
    def vision_status(self) -> dict:
        up = self.client.available()
        return {
            "ollama": up,
            "vision_model": self.cfg.llm.vision_model if up and self.client.has_model(self.cfg.llm.vision_model) else "",
            "page_model": self.cfg.llm.page_model if up and self.client.has_model(self.cfg.llm.page_model) else "",
        }

    # ----------------------------------------------------------------- main
    def convert(
        self,
        pdf: Path,
        md_path: Path,
        assets_dir: Path,
        describe_figures: bool | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> ConversionResult:
        t0 = time.monotonic()
        say = progress or (lambda msg: log.info(msg))
        cfg = self.cfg
        warnings: list[str] = []
        pages = inspect_pdf(pdf)
        if not pages:
            raise RuntimeError("PDF has no pages")
        engine = pick_ocr_engine(cfg)
        status = self.vision_status()
        page_model = status["page_model"] if cfg.ocr.engine in ("auto", "vlm") else ""
        if cfg.ocr.engine == "vlm" and not page_model:
            warnings.append(
                f"OCR engine 'vlm' requested but model {cfg.llm.page_model} is not available in Ollama; used {engine}"
            )
        want_figures = cfg.figures.describe if describe_figures is None else describe_figures
        figure_model = status["vision_model"] if want_figures else ""

        # 1. Docling over runs of text pages / pages that need full-page OCR.
        doc_for_page: dict[int, Any] = {}
        ocr_pages: list[int] = []
        for full_page, first, last in page_runs(pages, cfg.ocr.redo_existing_text_layer):
            say(f"layout/tables/OCR pages {first}-{last} of {len(pages)} ({'OCR ' + engine if full_page else 'text layer'})")
            doc = self._run_docling(pdf, engine, full_page, first, last, warnings)
            for n in range(first, last + 1):
                doc_for_page[n] = doc
            if full_page:
                ocr_pages.extend(range(first, last + 1))

        docs = list({id(d): d for d in doc_for_page.values()}.values())
        page_md: dict[int, str] = {}

        # 2. Scanned pages: transcribe with the vision model, cross-checked against classical OCR.
        vlm_pages: list[int] = []
        if page_model and ocr_pages:
            for i, n in enumerate(ocr_pages, 1):
                say(f"vision-model OCR page {n} ({i}/{len(ocr_pages)}) with {page_model}")
                baseline = self._serialize_page(doc_for_page[n], n, {})
                try:
                    png = render_page_png(pdf, n, dpi=cfg.ocr.vlm_dpi)
                    cand = vision.transcribe_page(self.client, page_model, png, cfg.ocr.languages)
                except OllamaError as e:
                    warnings.append(f"page {n}: vision model failed ({e}); used {engine} OCR")
                    continue
                if vision.looks_degenerate(cand):
                    warnings.append(f"page {n}: vision-model output looked repetitive; used {engine} OCR")
                    continue
                if cfg.ocr.vlm_crosscheck and len(vision._tokens(baseline)) >= 15:
                    r = vision.recall(baseline, cand)
                    if r < cfg.ocr.min_crosscheck_recall:
                        warnings.append(
                            f"page {n}: vision-model text disagreed with {engine} OCR (overlap {r:.0%}); used {engine} OCR"
                        )
                        continue
                page_md[n] = cand
                vlm_pages.append(n)

        # 3. Figures on the remaining pages.
        blocks, n_figs, n_described = self._figures(
            docs, set(page_md), pdf, md_path, assets_dir, figure_model, say, warnings
        )

        # 4. Assemble page by page.
        parts: list[str] = []
        for p in pages:
            n = p.number
            body = page_md.get(n)
            if body is None:
                doc = doc_for_page[n]
                body = self._serialize_page(doc, n, blocks.get(id(doc), {}))
            parts.append(f"<!-- page {n} -->\n\n{body.strip()}".rstrip())
        tables = sum(
            1 for d in docs for t in d.tables
            if t.prov and t.prov[0].page_no not in page_md
        )
        title = self._title(docs, pdf)
        return ConversionResult(
            markdown="\n\n".join(parts) + "\n",
            title=title,
            pages=pages,
            ocr_pages=ocr_pages,
            vlm_pages=vlm_pages,
            tables=tables,
            figures=n_figs,
            figures_described=n_described,
            ocr_engine=engine if ocr_pages else "",
            vision_model=figure_model or page_model,
            warnings=warnings,
            seconds=round(time.monotonic() - t0, 1),
        )

    # -------------------------------------------------------------- helpers
    def _serialize_page(self, doc: Any, page_no: int, blocks: dict[str, str]) -> str:
        from docling_core.transforms.serializer.markdown import (
            MarkdownDocSerializer,
            MarkdownParams,
            MarkdownPictureSerializer,
        )
        from docling_core.transforms.serializer.common import create_ser_result
        from docling_core.types.doc import ContentLayer, ImageRefMode

        class PictureSerializer(MarkdownPictureSerializer):
            def serialize(self, *, item, doc_serializer, doc, **kwargs):
                if item.self_ref in doc_serializer.get_excluded_refs(**kwargs):
                    return create_ser_result(text="", span_source=item)  # not on this page
                text = blocks.get(item.self_ref, "")
                if text and "{caption}" in text:
                    cap = doc_serializer.serialize_captions(item=item, **kwargs).text.strip()
                    text = re.sub(r"\n{3,}", "\n\n", text.replace("{caption}", cap)).strip()
                return create_ser_result(text=text, span_source=item)

        layers = {ContentLayer.BODY}
        if self.cfg.layout.include_headers_footers:
            layers.add(ContentLayer.FURNITURE)
        params = MarkdownParams(
            pages={page_no},
            layers=layers,
            image_mode=ImageRefMode.PLACEHOLDER,
            escape_underscores=False,
            escape_html=False,
            compact_tables=True,
            include_picture_classification=False,
        )
        ser = MarkdownDocSerializer(doc=doc, params=params, picture_serializer=PictureSerializer())
        return ser.serialize().text

    def _figures(self, docs, skip_pages, pdf, md_path, assets_dir, model, say, warnings):
        cfg = self.cfg.figures
        # Keyed by document, then picture ref: every Docling run numbers its pictures from 0.
        blocks: dict[int, dict[str, str]] = {}
        n_figs = n_described = 0
        counter = 0
        for doc in docs:
            for pic in doc.pictures:
                if not pic.prov:
                    continue
                prov = pic.prov[0]
                page_no = prov.page_no
                if page_no in skip_pages:
                    continue
                size = doc.pages[page_no].size
                bbox = prov.bbox
                area = abs((bbox.r - bbox.l) * (bbox.t - bbox.b)) / max(size.width * size.height, 1.0)
                if area < cfg.min_area:
                    continue
                cls_name, conf = _picture_class(pic)
                if cls_name == "full_page_image" or cls_name == "page_thumbnail":
                    cls_name = "other"
                counter += 1
                label = KIND_LABEL.get(cls_name, cls_name.replace("_", " ") if cls_name else "figure")
                image = pic.get_image(doc)
                link = ""
                if image is not None and cfg.save_images:
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    img_path = assets_dir / f"p{page_no:03d}-fig{counter:02d}.png"
                    image.save(img_path)
                    rel = Path(os.path.relpath(img_path, md_path.parent)).as_posix()
                    link = f"![Figure {counter}](<{rel}>)"
                if cls_name in cfg.marker_only:
                    blocks.setdefault(id(doc), {})[pic.self_ref] = "\n\n".join(
                        x for x in (f"[{label.capitalize()}]", "{caption}", link if cls_name in ("signature", "stamp") else "") if x
                    )
                    continue
                n_figs += 1
                header = f"**[Figure {counter} | page {page_no} | {label}]**"
                desc_md = ""
                if model and image is not None:
                    say(f"describing figure {counter} (page {page_no}, {label}) with {model}")
                    buf = io.BytesIO()
                    image.convert("RGB").save(buf, format="PNG")
                    caption = pic.caption_text(doc) if hasattr(pic, "caption_text") else ""
                    try:
                        d = vision.describe_figure(self.client, model, buf.getvalue(), cls_name or None, caption)
                        desc_md = self._render_description(d, model)
                        if d.title:
                            header = f"**[Figure {counter} | page {page_no} | {label}] {d.title}**"
                        n_described += 1
                    except OllamaError as e:
                        warnings.append(f"figure {counter} (page {page_no}): description failed ({e})")
                if not desc_md:
                    desc_md = (
                        "_(No text description: the local vision model was not available. "
                        "Open the image if this figure matters.)_"
                    )
                blocks.setdefault(id(doc), {})[pic.self_ref] = "\n\n".join(x for x in (header, "{caption}", link, desc_md) if x)
        return blocks, n_figs, n_described

    @staticmethod
    def _render_description(d: vision.FigureDescription, model: str) -> str:
        out = []
        if d.summary:
            out.append(d.summary)
        if d.data_table:
            out.append(d.data_table if vision.is_markdown_table(d.data_table) else f"Data: {d.data_table}")
        if d.text_in_image:
            out.append(f"Text in figure: {d.text_in_image}")
        out.append(f"_(Described by local vision model `{model}`; verify critical values against the image.)_")
        return "\n\n".join(out)

    @staticmethod
    def _title(docs, pdf: Path) -> str:
        from docling_core.types.doc import DocItemLabel

        for doc in docs[:1]:
            for item in doc.texts[:15]:
                text = item.text.strip()
                if item.label in (DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER) and text:
                    # OCR sometimes drops the spaces in large headings ("NOTICEOFLEASE...")
                    if " " in text or len(text) <= 12:
                        return text
                    break
        return pdf_title(pdf) or pdf.stem.replace("-", " ").replace("_", " ")
