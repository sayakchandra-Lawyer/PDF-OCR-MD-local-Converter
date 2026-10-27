"""The Claude read hook must stay stdlib-only and behave predictably."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pdf2md.config import Config
from pdf2md.core.layout import ProjectLayout
from pdf2md.core.manifest import transaction

HOOK = Path(__file__).resolve().parents[1] / "src" / "pdf2md" / "claude_plugin" / "hooks" / "pdf_read_guard.py"


def run_hook(payload: dict) -> dict | None:
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else None


def make_project(tmp_path: Path, status: str = "done", wait: int = 1) -> ProjectLayout:
    root = tmp_path / "P01_Test"
    root.mkdir()
    lay = ProjectLayout.for_root(root, Config().layout)
    lay.ensure()
    (lay.pdf_dir / "Deed.pdf").write_bytes(b"%PDF-1.4")
    (lay.md_dir / "Deed.md").write_text("# Deed\n", encoding="utf-8")
    with transaction(lay) as m:
        m.upsert("PDF/Deed.pdf", aliases=["Sale Deed.pdf"], md="MD/Deed.md", status=status, pages=4)
    marker = json.loads(lay.marker_path.read_text())
    marker["hook"]["wait_seconds"] = wait
    lay.marker_path.write_text(json.dumps(marker))
    return lay


def read(path: Path, **extra) -> dict:
    return {"tool_name": "Read", "tool_input": {"file_path": str(path), **extra}}


def test_redirects_converted_pdf(tmp_path):
    lay = make_project(tmp_path)
    out = run_hook(read(lay.pdf_dir / "Deed.pdf"))
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert str(lay.md_dir / "Deed.md") in hso["permissionDecisionReason"]


def test_redirects_original_name(tmp_path):
    lay = make_project(tmp_path)
    out = run_hook(read(lay.root / "Sale Deed.pdf"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_allows_pages_and_non_pdf_and_unmanaged(tmp_path):
    lay = make_project(tmp_path)
    assert run_hook(read(lay.pdf_dir / "Deed.pdf", pages="2")) is None
    assert run_hook(read(lay.md_dir / "Deed.md")) is None
    other = tmp_path / "elsewhere.pdf"
    other.write_bytes(b"%PDF")
    assert run_hook(read(other)) is None
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}) is None


def test_failed_conversion_falls_back_to_pdf(tmp_path):
    lay = make_project(tmp_path, status="failed")
    assert run_hook(read(lay.pdf_dir / "Deed.pdf")) is None


def test_in_progress_waits_then_explains(tmp_path):
    lay = make_project(tmp_path, status="converting", wait=1)
    out = run_hook(read(lay.pdf_dir / "Deed.pdf"))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "still converting" in reason and "pages" in reason
