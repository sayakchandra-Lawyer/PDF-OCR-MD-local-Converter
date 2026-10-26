"""Environment checks (`pdf2md doctor`) and hardware-based model suggestions."""

from __future__ import annotations

import ctypes
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from pdf2md.config import CONFIG_PATH, Config


def total_ram_gb() -> float:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout
            return int(out.strip()) / 2**30
        if os.name == "nt":
            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MemStatus()
            st.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return st.ullTotalPhys / 2**30
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 2**20
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def suggested_vision_model() -> str:
    ram = total_ram_gb()
    if ram and ram < 12:
        return "qwen3-vl:2b-instruct"
    if ram and ram < 15:
        return "qwen3-vl:4b-instruct"
    return "qwen3-vl:8b-instruct"


def run_doctor(cfg: Config) -> int:
    from pdf2md.autostart import status as autostart_status
    from pdf2md.converter import pick_ocr_engine
    from pdf2md.ollama import OllamaClient

    problems = 0

    def line(ok: bool | None, text: str) -> None:
        nonlocal problems
        mark = {True: "OK ", False: "!! ", None: "-- "}[ok]
        if ok is False:
            problems += 1
        print(f"  {mark} {text}")

    print(f"pdf2md doctor  ({platform.platform()}, Python {platform.python_version()}, RAM {total_ram_gb():.0f} GB)")
    print(f"config: {CONFIG_PATH} {'(exists)' if CONFIG_PATH.exists() else '(defaults - run pdf2md setup)'}")

    print("\nConversion engine")
    for mod, label in (("docling", "Docling"), ("onnxruntime", "onnxruntime (RapidOCR)"), ("watchdog", "watchdog"), ("mcp", "MCP SDK")):
        line(importlib.util.find_spec(mod) is not None, label)
    if sys.platform == "darwin":
        line(importlib.util.find_spec("ocrmac") is not None, "ocrmac (Apple Vision OCR)")
    line(None if not shutil.which("tesseract") else True, f"tesseract CLI: {shutil.which('tesseract') or 'not installed (optional)'}")
    print(f"  ->  classical OCR engine in use: {pick_ocr_engine(cfg)}; languages: {cfg.ocr.languages}")

    print("\nLocal vision model (Ollama)")
    client = OllamaClient(cfg.llm.ollama_url)
    ver = client.version()
    line(ver is not None, f"Ollama at {cfg.llm.ollama_url}: {'version ' + ver if ver else 'not reachable - install/start Ollama'}")
    if ver:
        line(client.has_model(cfg.llm.vision_model), f"figure model {cfg.llm.vision_model}"
             + ("" if client.has_model(cfg.llm.vision_model) else f"  ->  pdf2md models pull"))
        if cfg.llm.page_model != cfg.llm.vision_model:
            line(client.has_model(cfg.llm.page_model), f"scanned-page model {cfg.llm.page_model}")

    print("\nWatched folders")
    parents, projects = cfg.watch_roots()
    if not parents and not projects:
        line(False, "none configured  ->  pdf2md add-folder <folder>")
    for p in [*parents, *projects]:
        ok = p.is_dir() and os.access(p, os.R_OK | os.W_OK)
        line(ok, f"{p}" + ("" if ok else "  (missing or not writable - on macOS grant Full Disk Access to Python)"))

    print("\nBackground watcher")
    for s in autostart_status().splitlines():
        print(f"      {s}")

    print("\nClaude integration")
    from pdf2md.claude_setup import desktop_config_paths

    for path in desktop_config_paths():
        has = False
        if path.exists():
            try:
                import json

                has = "pdf2md" in json.loads(path.read_text(encoding="utf-8") or "{}").get("mcpServers", {})
            except ValueError:
                pass
        line(has or None, f"Claude Desktop MCP entry in {path}" + ("" if has else " (not set - pdf2md claude install-mcp)"))

    print(f"\n{'All good.' if problems == 0 else f'{problems} problem(s) found.'}")
    return 0 if problems == 0 else 1
