"""Logging set-up shared by the commands."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from pdf2md.config import LOG_DIR


def setup_logging(to_file: bool = False, verbose: bool = False) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if to_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(LOG_DIR / "watcher.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )
    for noisy in ("docling", "httpx", "rapidocr", "RapidOCR", "watchdog", "huggingface_hub", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
