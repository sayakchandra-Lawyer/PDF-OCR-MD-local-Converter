"""Docling document -> Markdown for one page, with our own figure blocks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pdf2md.engine.figures import CAPTION_SLOT
from pdf2md.engine.pdfinspect import pdf_title


def serialize_page(doc: Any, page_no: int, blocks: dict[str, str], include_headers_footers: bool = False) -> str:
    from docling_core.transforms.serializer.common import create_ser_result
    from docling_core.transforms.serializer.markdown import (
        MarkdownDocSerializer,
        MarkdownParams,
        MarkdownPictureSerializer,
    )
    from docling_core.types.doc import ContentLayer, ImageRefMode

    class PictureSerializer(MarkdownPictureSerializer):
        def serialize(self, *, item, doc_serializer, doc, **kwargs):
            if item.self_ref in doc_serializer.get_excluded_refs(**kwargs):
                return create_ser_result(text="", span_source=item)  # not on this page
            text = blocks.get(item.self_ref, "")
            if text and CAPTION_SLOT in text:
                cap = doc_serializer.serialize_captions(item=item, **kwargs).text.strip()
                text = re.sub(r"\n{3,}", "\n\n", text.replace(CAPTION_SLOT, cap)).strip()
            return create_ser_result(text=text, span_source=item)

    layers = {ContentLayer.BODY}
    if include_headers_footers:
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


def document_title(docs: list[Any], pdf: Path) -> str:
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
