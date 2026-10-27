"""Local vision-model helpers: transcribe scanned pages and describe figures."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass

from pdf2md.engine.ollama import OllamaClient, OllamaError

LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "mr": "Marathi", "ta": "Tamil",
    "te": "Telugu", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi",
    "or": "Odia", "ur": "Urdu", "as": "Assamese", "fr": "French", "de": "German",
    "es": "Spanish", "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "ru": "Russian",
    "ar": "Arabic", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
}

PAGE_PROMPT = """You are a meticulous OCR engine. Transcribe ALL content of this scanned document page into GitHub-flavoured Markdown.

Rules:
- Keep the natural reading order. Use # / ## headings only for real headings. Keep numbered and bulleted lists, clause numbers and indentation levels.
- Reproduce every table as a Markdown table with the same rows and columns. Keep every row. Repeat a merged cell's value in each cell it spans. Copy numbers, dates, amounts and reference numbers exactly as printed (e.g. 1,35,000).
- Do not summarise, translate, correct, reorder or omit anything. Keep the original spelling.
- Charts / graphs: write "[Chart: <type> - <title>]" followed by a Markdown table of the values you can read.
- Photos, logos, signatures, stamps / seals: write a short bracketed note, e.g. [Signature], [Stamp: "<text on the stamp>"], [Logo: <name>]. Handwriting: transcribe it and mark it as (handwritten). Unreadable text: [illegible].
- Ignore scanner noise and page edges.
- Output only the Markdown for this page: no commentary, no code fences.
Expected language(s): {languages}."""

FIGURE_KINDS = [
    "bar_chart", "line_chart", "pie_chart", "scatter_plot", "area_chart", "other_chart",
    "table_image", "flow_chart", "diagram", "map", "photo", "screenshot", "logo",
    "signature", "stamp", "handwriting", "other",
]

FIGURE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": FIGURE_KINDS},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "data_table": {"type": "string"},
        "text_in_image": {"type": "string"},
    },
    "required": ["kind", "title", "summary", "data_table", "text_in_image"],
}

FIGURE_PROMPT = """This image is a figure extracted from a PDF document{hint}{caption}.
Describe it so that someone who cannot see it can fully use its information. Return JSON with:
- "kind": one of {kinds}.
- "title": the figure's title (or a short descriptive title if none).
- "summary": 2-6 factual sentences: what it shows, axes and units, series / legend entries, and the key values, trends, comparisons or steps. No speculation.
- "data_table": for charts and table images, a GitHub Markdown table of ALL values you can read (first column = category / x value, one column per series, units in headers). For flow charts or diagrams, a Markdown table of nodes/steps and connections. Otherwise "".
- "text_in_image": all other text printed in the image, verbatim (labels, notes, annotations), or "".
Read numbers carefully; if a value has to be estimated from the axis, prefix it with "~"."""


@dataclass
class FigureDescription:
    kind: str
    title: str
    summary: str
    data_table: str
    text_in_image: str


def language_list(codes: list[str]) -> str:
    return ", ".join(LANGUAGE_NAMES.get(c.lower().split("-")[0], c) for c in codes) or "English"


def strip_fences(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", t, re.S)
    return m.group(1).strip() if m else t


def looks_degenerate(text: str) -> bool:
    """Detect the repetition loops small vision models sometimes fall into."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 12:
        common, count = Counter(lines).most_common(1)[0]
        if count >= max(8, len(lines) // 3) and not set(common) <= set("|-: "):
            return True
    words = text.split()
    if len(words) >= 60:
        grams = Counter(tuple(words[i:i + 6]) for i in range(len(words) - 5))
        if grams and grams.most_common(1)[0][1] >= max(10, len(words) // 25):
            return True
    return False


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", text.lower()) if len(t) > 1 or t.isdigit()}


def recall(baseline: str, candidate: str) -> float:
    """Share of the classical-OCR vocabulary that the candidate also contains."""
    base = tokens(baseline)
    if not base:
        return 1.0
    return len(base & tokens(candidate)) / len(base)


def transcribe_page(client: OllamaClient, model: str, png: bytes, languages: list[str]) -> str:
    prompt = PAGE_PROMPT.format(languages=language_list(languages))
    if model.startswith("glm-ocr"):
        prompt = "Text Recognition:"
    return strip_fences(client.chat(model, prompt, images=[png], max_tokens=8192))


def describe_figure(
    client: OllamaClient,
    model: str,
    png: bytes,
    hint: str | None = None,
    caption: str | None = None,
) -> FigureDescription:
    prompt = FIGURE_PROMPT.format(
        hint=f" (a layout model classified it as: {hint.replace('_', ' ')})" if hint else "",
        caption=f'. Its caption reads: "{caption.strip()}"' if caption and caption.strip() else "",
        kinds=", ".join(FIGURE_KINDS),
    )
    try:
        raw = client.chat(model, prompt, images=[png], json_schema=FIGURE_SCHEMA, max_tokens=3000)
    except OllamaError as e:
        # Some model/server combinations reject structured output; retry free-form.
        if "format" not in str(e).lower() and "schema" not in str(e).lower():
            raise
        raw = client.chat(model, prompt + "\nReturn only the JSON object.", images=[png], max_tokens=3000)
    return parse_figure_json(raw, fallback_kind=hint or "other")


def parse_figure_json(raw: str, fallback_kind: str = "other") -> FigureDescription:
    text = strip_fences(raw)
    data = None
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except ValueError:
                data = None
    if not isinstance(data, dict):
        return FigureDescription(fallback_kind, "", text.strip(), "", "")
    get = lambda k: str(data.get(k) or "").strip()  # noqa: E731
    return FigureDescription(
        kind=get("kind") or fallback_kind,
        title=get("title"),
        summary=get("summary"),
        data_table=get("data_table"),
        text_in_image=get("text_in_image"),
    )


def is_markdown_table(text: str) -> bool:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return len(lines) >= 2 and lines[0].startswith("|") and bool(re.match(r"^\|?\s*:?-{2,}", lines[1]))
