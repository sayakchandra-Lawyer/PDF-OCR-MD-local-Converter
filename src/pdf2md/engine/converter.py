"""PDF -> Markdown conversion: Docling (layout, tables, OCR) + local vision LLM.

Pipeline per document:
  1. pdfinspect   - classify every page (text layer / scanned)
  2. Docling      - layout, tables and OCR, per run of similar pages
  3. vision model - re-transcribe scanned pages, cross-checked against step 2
  4. figures      - save, classify and describe charts / images
  5. markdown     - assemble page by page with <!-- page N --> markers
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pdf2md.config import Config
from pdf2md.engine import vision
from pdf2md.engine.figures import build_figure_blocks
from pdf2md.engine.markdown import document_title, serialize_page
from pdf2md.engine.ocr import ocr_options, pick_ocr_engine
from pdf2md.engine.ollama import OllamaClient, OllamaError
from pdf2md.engine.pdfinspect import PageInfo, inspect_pdf, page_runs, render_page_png

log = logging.getLogger("pdf2md")


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
            opts.ocr_options = ocr_options(engine, self.cfg.ocr.languages, full_page)
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

    def _serialize(self, doc: Any, page_no: int, blocks: dict[str, str]) -> str:
        return serialize_page(doc, page_no, blocks, self.cfg.layout.include_headers_footers)

    def _vision_transcribe(self, pdf: Path, ocr_pages, doc_for_page, model, engine, say, warnings) -> dict[int, str]:
        """Vision-model text for scanned pages; a page keeps its classical OCR
        when the model fails, loops, or disagrees with it."""
        cfg = self.cfg.ocr
        page_md: dict[int, str] = {}
        for i, n in enumerate(ocr_pages, 1):
            say(f"vision-model OCR page {n} ({i}/{len(ocr_pages)}) with {model}")
            baseline = self._serialize(doc_for_page[n], n, {})
            try:
                png = render_page_png(pdf, n, dpi=cfg.vlm_dpi)
                cand = vision.transcribe_page(self.client, model, png, cfg.languages)
            except OllamaError as e:
                warnings.append(f"page {n}: vision model failed ({e}); used {engine} OCR")
                continue
            if vision.looks_degenerate(cand):
                warnings.append(f"page {n}: vision-model output looked repetitive; used {engine} OCR")
                continue
            if cfg.vlm_crosscheck and len(vision.tokens(baseline)) >= 15:
                r = vision.recall(baseline, cand)
                if r < cfg.min_crosscheck_recall:
                    warnings.append(
                        f"page {n}: vision-model text disagreed with {engine} OCR (overlap {r:.0%}); used {engine} OCR"
                    )
                    continue
            page_md[n] = cand
        return page_md

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

        # 1-2. Docling over runs of text pages / pages that need full-page OCR.
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

        # 3. Scanned pages through the vision model.
        page_md: dict[int, str] = {}
        if page_model and ocr_pages:
            page_md = self._vision_transcribe(pdf, ocr_pages, doc_for_page, page_model, engine, say, warnings)

        # 4. Figures on the remaining pages.
        figs = build_figure_blocks(
            docs, set(page_md), md_path, assets_dir, cfg.figures, self.client, figure_model, say, warnings
        )

        # 5. Assemble page by page.
        parts: list[str] = []
        for p in pages:
            n = p.number
            body = page_md.get(n)
            if body is None:
                doc = doc_for_page[n]
                body = self._serialize(doc, n, figs.for_doc(doc))
            parts.append(f"<!-- page {n} -->\n\n{body.strip()}".rstrip())
        tables = sum(1 for d in docs for t in d.tables if t.prov and t.prov[0].page_no not in page_md)
        return ConversionResult(
            markdown="\n\n".join(parts) + "\n",
            title=document_title(docs, pdf),
            pages=pages,
            ocr_pages=ocr_pages,
            vlm_pages=sorted(page_md),
            tables=tables,
            figures=figs.figures,
            figures_described=figs.described,
            ocr_engine=engine if ocr_pages else "",
            vision_model=figure_model or page_model,
            warnings=warnings,
            seconds=round(time.monotonic() - t0, 1),
        )
