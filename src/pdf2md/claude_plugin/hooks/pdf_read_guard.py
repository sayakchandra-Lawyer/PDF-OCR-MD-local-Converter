#!/usr/bin/env python3
"""Claude PreToolUse hook: redirect `Read` of a PDF to its local Markdown twin.

Standard library only - it runs inside Cowork's VM or wherever Claude Code runs.

* PDF already converted by pdf2md      -> block the read, point Claude to the .md
* conversion queued / in progress      -> wait (up to hook.wait_seconds in .pdf2md.json)
* conversion failed / folder unmanaged -> let the read through
* Read with an explicit `pages` range  -> let it through (deliberate visual check)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path, PurePosixPath

MARKER = ".pdf2md.json"
FRESH_SECONDS = 15 * 60  # a PDF this new may simply not have been picked up yet


def find_marker(pdf: Path):
    for d in list(pdf.parents)[:8]:
        m = d / MARKER
        if m.is_file():
            try:
                return d, json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None, None
    return None, None


def lookup(root: Path, marker: dict, pdf: Path):
    try:
        manifest = json.loads((root / marker.get("manifest", "MD/.pdf2md/manifest.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    docs = manifest.get("documents", {})
    try:
        rel = pdf.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = pdf.name
    if rel in docs:
        return docs[rel]
    name = pdf.name.lower()
    by_name = None
    for key, entry in docs.items():
        aliases = [a.lower() for a in entry.get("aliases", [])]
        if rel.lower() in aliases:
            return entry
        if by_name is None and (PurePosixPath(key).name.lower() == name or any(PurePosixPath(a).name == name for a in aliases)):
            by_name = entry
    return by_name


def is_fresh(pdf: Path) -> bool:
    try:
        st = pdf.stat()
    except OSError:
        return False
    return time.time() - max(st.st_mtime, st.st_ctime) < FRESH_SECONDS


def deny(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if data.get("tool_name") != "Read":
        return 0
    tool_input = data.get("tool_input") or {}
    raw = str(tool_input.get("file_path") or "")
    if not raw.lower().endswith(".pdf") or tool_input.get("pages") or os.environ.get("PDF2MD_ALLOW_PDF") == "1":
        return 0
    pdf = Path(raw)
    if not pdf.is_absolute():
        pdf = Path(data.get("cwd") or os.getcwd()) / pdf
    root, marker = find_marker(pdf)
    if not root:
        return 0
    wait = float((marker.get("hook") or {}).get("wait_seconds", 150))
    deadline = time.time() + wait
    while True:
        entry = lookup(root, marker, pdf)
        status = (entry or {}).get("status")
        md = root / entry["md"] if entry and entry.get("md") else None
        if status == "done" and md and md.is_file():
            pages = entry.get("pages", "?")
            return deny(
                f"pdf2md has already converted this PDF to Markdown locally. Read `{md}` instead of the PDF "
                f"({pages} pages; same text, tables as Markdown tables, charts/figures described with their data, "
                "page boundaries marked `<!-- page N -->`). For long documents, grep the .md or read only the "
                "relevant pages. Only if you must visually check one specific page of the original (e.g. a "
                "signature, stamp or chart), Read the PDF again with the `pages` parameter."
            )
        if status in ("failed", "missing"):
            return 0
        if entry is None and not is_fresh(pdf):
            return 0  # not handled by the watcher (yet); don't get in the way
        if time.time() >= deadline:
            where = f" It will be written to `{md}`." if md else ""
            return deny(
                f"pdf2md is still converting this PDF to Markdown (status: {status or 'waiting for the watcher'})."
                f"{where} Tell the user, wait a minute (e.g. `sleep 60`) and try again, or check "
                f"`{root / marker.get('index', 'MD/INDEX.md')}`. If an answer is needed right now, Read only the "
                "specific pages you need with the `pages` parameter."
            )
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
