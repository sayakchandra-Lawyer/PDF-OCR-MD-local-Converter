"""Charts, diagrams and images: save them, classify them and describe them with
the local vision model, producing a Markdown block per picture."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pdf2md.config import FiguresConfig
from pdf2md.engine import vision
from pdf2md.engine.ollama import OllamaClient, OllamaError

KIND_LABEL = {
    "bar_chart": "bar chart", "line_chart": "line chart", "pie_chart": "pie chart",
    "scatter_plot": "scatter plot", "flow_chart": "flow chart", "table": "table image",
    "table_image": "table image", "geographical_map": "map", "photograph": "photo",
    "screenshot_from_computer": "screenshot", "screenshot_from_manual": "screenshot",
    "engineering_drawing": "drawing", "stamp": "stamp / seal", "signature": "signature",
}

# Inserted by the page serializer: the picture's caption from the PDF.
CAPTION_SLOT = "{caption}"


@dataclass
class FigureBlocks:
    # Keyed by id(document), then picture ref: every Docling run numbers its pictures from 0.
    blocks: dict[int, dict[str, str]] = field(default_factory=dict)
    figures: int = 0
    described: int = 0

    def for_doc(self, doc: Any) -> dict[str, str]:
        return self.blocks.get(id(doc), {})

    def _set(self, doc: Any, ref: str, text: str) -> None:
        self.blocks.setdefault(id(doc), {})[ref] = text


def picture_class(pic: Any) -> tuple[str, float]:
    """Best Docling picture-classifier label and its confidence."""
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


def render_description(d: vision.FigureDescription, model: str) -> str:
    out = []
    if d.summary:
        out.append(d.summary)
    if d.data_table:
        out.append(d.data_table if vision.is_markdown_table(d.data_table) else f"Data: {d.data_table}")
    if d.text_in_image:
        out.append(f"Text in figure: {d.text_in_image}")
    out.append(f"_(Described by local vision model `{model}`; verify critical values against the image.)_")
    return "\n\n".join(out)


def build_figure_blocks(
    docs: list[Any],
    skip_pages: set[int],
    md_path: Path,
    assets_dir: Path,
    cfg: FiguresConfig,
    client: OllamaClient,
    model: str,
    say: Callable[[str], None],
    warnings: list[str],
) -> FigureBlocks:
    """Markdown for every relevant picture in `docs`, skipping pages already
    transcribed by the vision model. `model` empty = no descriptions."""
    out = FigureBlocks()
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
            cls_name, _ = picture_class(pic)
            if cls_name in ("full_page_image", "page_thumbnail"):
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
                keep_image = cls_name in ("signature", "stamp")
                parts = (f"[{label.capitalize()}]", CAPTION_SLOT, link if keep_image else "")
                out._set(doc, pic.self_ref, "\n\n".join(x for x in parts if x))
                continue
            out.figures += 1
            header = f"**[Figure {counter} | page {page_no} | {label}]**"
            desc_md = ""
            if model and image is not None:
                say(f"describing figure {counter} (page {page_no}, {label}) with {model}")
                buf = io.BytesIO()
                image.convert("RGB").save(buf, format="PNG")
                caption = pic.caption_text(doc) if hasattr(pic, "caption_text") else ""
                try:
                    d = vision.describe_figure(client, model, buf.getvalue(), cls_name or None, caption)
                    desc_md = render_description(d, model)
                    if d.title:
                        header = f"**[Figure {counter} | page {page_no} | {label}] {d.title}**"
                    out.described += 1
                except OllamaError as e:
                    warnings.append(f"figure {counter} (page {page_no}): description failed ({e})")
            if not desc_md:
                desc_md = (
                    "_(No text description: the local vision model was not available. "
                    "Open the image if this figure matters.)_"
                )
            out._set(doc, pic.self_ref, "\n\n".join(x for x in (header, CAPTION_SLOT, link, desc_md) if x))
    return out
