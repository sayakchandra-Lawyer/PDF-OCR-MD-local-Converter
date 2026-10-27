"""Safe, readable, cross-platform file names."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

# Characters Windows/macOS reject in file names, plus a few that are awkward in
# Markdown links and shell commands.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f()\[\]{}#%;,\'`!$^=+~]')
_WS = re.compile(r"\s+")
_DASHES = re.compile(r"-{2,}")
# "-_-", "_-", "-." etc. collapse to the stronger separator.
_MIXED_SEP = re.compile(r"-*([_.])-*")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def clean_stem(stem: str, max_len: int = 120) -> str:
    """Turn an arbitrary file stem into a tidy, portable one.

    "Lease Agreement (Final) v2"  -> "Lease-Agreement-Final-v2"
    "2025_03_14 Notice  - Kolkata" -> "2025_03_14-Notice-Kolkata"
    Non-Latin scripts (Hindi, Bengali, ...) are preserved.
    """
    s = unicodedata.normalize("NFC", stem)
    s = _BAD_CHARS.sub(" ", s)
    s = _WS.sub("-", s.strip())
    s = s.replace("&", "and")
    s = _DASHES.sub("-", s)
    s = _MIXED_SEP.sub(r"\1", s)
    s = s.strip("-_. ")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-_. ")
    if not s:
        s = "document"
    if s.upper() in _WINDOWS_RESERVED:
        s += "_file"
    return s


def render_name(template: str, stem: str, project: str, today: date | None = None) -> str:
    today = today or date.today()
    try:
        raw = template.format(stem=stem, date=today.isoformat(), project=project)
    except (KeyError, IndexError, ValueError):
        raw = stem
    return clean_stem(raw)
