"""Fast unit tests (no models needed)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pdf2md.config import Config, dump_config, load_config
from pdf2md.layout import ProjectLayout, find_project, guess_project, resolve_user_path
from pdf2md.manifest import Manifest, transaction
from pdf2md.mcp_server import select_pages
from pdf2md.naming import clean_stem, render_name
from pdf2md.pdfinspect import PageInfo, classify, page_runs
from pdf2md.service import Service, page_ranges
from pdf2md import vision


# ------------------------------------------------------------------ naming
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Lease Agreement (Final) v2", "Lease-Agreement-Final-v2"),
        ("2025_03_14 Notice  - Kolkata", "2025_03_14-Notice-Kolkata"),
        ("  weird:name?*  ", "weird-name"),
        ("Profit & Loss", "Profit-and-Loss"),
        ("CON", "CON_file"),
        ("", "document"),
        ("नोटिस पत्र", "नोटिस-पत्र"),
        ("W.P. (C) 1234/2024", "W.P.C-1234-2024"),
    ],
)
def test_clean_stem(raw, expected):
    assert clean_stem(raw) == expected


def test_render_name_template():
    assert render_name("{date}_{stem}", "My File", "P01", today=date(2026, 1, 2)) == "2026-01-02_My-File"
    assert render_name("{bogus}", "My File", "P01") == "My-File"


# ------------------------------------------------------------------ layout
def _cfg(parent: Path) -> Config:
    cfg = Config()
    cfg.watch.project_parents = [str(parent)]
    return cfg


def test_layout_plain_and_context(tmp_path):
    plain = tmp_path / "P01_A"
    ctx = tmp_path / "P02_B"
    plain.mkdir()
    (ctx / "01_Context").mkdir(parents=True)
    cfg = Config()
    a = ProjectLayout.for_root(plain, cfg.layout)
    b = ProjectLayout.for_root(ctx, cfg.layout)
    assert a.pdf_dir == plain / "PDF" and a.md_dir == plain / "MD"
    assert b.pdf_dir == ctx / "01_Context" / "PDF" and b.md_dir == ctx / "01_Context" / "MD"
    assert a.md_path_for(plain / "PDF" / "x.pdf") == plain / "MD" / "x.md"
    assert a.md_path_for(plain / "Sub Dir" / "my doc.pdf") == plain / "MD" / "Sub Dir" / "my-doc.md"
    assert b.md_path_for(ctx / "01_Context" / "PDF" / "y.pdf") == ctx / "01_Context" / "MD" / "y.md"


def test_should_move_and_ignore(tmp_path):
    root = tmp_path / "P01"
    root.mkdir()
    lay = ProjectLayout.for_root(root, Config().layout)
    assert lay.should_move(root / "a.pdf")
    assert not lay.should_move(root / "PDF" / "a.pdf")
    assert not lay.should_move(root / "03_Outputs" / "a.pdf")
    assert lay.is_ignored(root / "MD" / "x.pdf")
    assert lay.is_ignored(root / ".hidden" / "x.pdf")
    assert lay.is_ignored(root / "04_Archive" / "x.pdf")
    assert lay.is_ignored(root / "~$lock.pdf")
    assert not lay.is_ignored(root / "Research" / "x.pdf")


def test_find_project_and_guess(tmp_path):
    parent = tmp_path / "Claude Projects"
    proj = parent / "P07_Thing"
    (proj / "sub").mkdir(parents=True)
    cfg = _cfg(parent)
    assert find_project(proj / "sub" / "a.pdf", cfg).root == proj.resolve()
    assert find_project(parent / "loose.pdf", cfg) is None
    outside = tmp_path / "elsewhere" / "PDF"
    outside.mkdir(parents=True)
    assert guess_project(outside / "a.pdf", cfg).root == (tmp_path / "elsewhere").resolve()


def test_resolve_cowork_vm_path(tmp_path):
    parent = tmp_path / "Projects"
    pdf = parent / "P03_X" / "PDF" / "a.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    cfg = _cfg(parent)
    assert resolve_user_path("/sessions/zz/mnt/P03_X/PDF/a.pdf", cfg) == pdf
    assert resolve_user_path("/sessions/zz/mnt/Projects/P03_X/PDF/a.pdf", cfg) == pdf
    assert resolve_user_path("/sessions/zz/mnt/nope/b.pdf", cfg) is None


# ---------------------------------------------------------------- manifest
def test_manifest_find_and_index(tmp_path):
    root = tmp_path / "P01"
    root.mkdir()
    lay = ProjectLayout.for_root(root, Config().layout)
    lay.ensure()
    with transaction(lay) as m:
        m.upsert("PDF/Lease-Deed.pdf", aliases=["Lease Deed.pdf"], md="MD/Lease-Deed.md", status="done", pages=3)
    m = Manifest.load(lay)
    assert m.find("Lease Deed.pdf")[0] == "PDF/Lease-Deed.pdf"
    assert m.find("lease-deed")[0] == "PDF/Lease-Deed.pdf"
    assert m.find("/sessions/x/mnt/P01/PDF/Lease-Deed.pdf")[0] == "PDF/Lease-Deed.pdf"
    assert m.find("other.pdf") is None
    index = lay.index_path.read_text(encoding="utf-8")
    assert "[Lease-Deed.md](<Lease-Deed.md>)" in index and "| 3 |" in index
    marker = json.loads(lay.marker_path.read_text())
    assert marker["md_dir"] == "MD" and marker["manifest"] == "MD/.pdf2md/manifest.json"


def test_claude_md_block_is_idempotent(tmp_path):
    root = tmp_path / "P01"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# My notes\n\nKeep this.\n", encoding="utf-8")
    svc = Service(Config())
    lay = ProjectLayout.for_root(root, svc.cfg.layout)
    svc.ensure_project(lay)
    svc.ensure_project(lay)
    text = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# My notes") and "Keep this." in text
    assert text.count("<!-- pdf2md:start -->") == 1


# ------------------------------------------------------------------ inspect
def test_classify_and_runs():
    assert classify(500, 0.1) == "text"
    assert classify(3, 1.0) == "scanned"
    assert classify(800, 0.95) == "scanned_text_layer"
    pages = [PageInfo(1, 500, 0, "text"), PageInfo(2, 0, 1, "scanned"), PageInfo(3, 0, 1, "scanned"),
             PageInfo(4, 900, 0.9, "scanned_text_layer"), PageInfo(5, 400, 0, "text")]
    assert page_runs(pages, True) == [(False, 1, 1), (True, 2, 4), (False, 5, 5)]
    assert page_runs(pages, False) == [(False, 1, 1), (True, 2, 3), (False, 4, 5)]
    assert page_ranges([1, 2, 3, 5, 7, 8]) == "1-3, 5, 7-8"


# ------------------------------------------------------------------- vision
def test_parse_figure_json_variants():
    d = vision.parse_figure_json('```json\n{"kind":"pie_chart","title":"Share","summary":"s","data_table":"","text_in_image":""}\n```')
    assert d.kind == "pie_chart" and d.title == "Share"
    d = vision.parse_figure_json('Sure! {"kind":"bar_chart","title":"T","summary":"x"} hope that helps')
    assert d.kind == "bar_chart" and d.data_table == ""
    d = vision.parse_figure_json("just prose", fallback_kind="diagram")
    assert d.kind == "diagram" and d.summary == "just prose"


def test_degenerate_and_recall():
    assert vision.looks_degenerate("the same line\n" * 30)
    assert not vision.looks_degenerate("| a | b |\n|---|---|\n" + "| 1 | 2 |\n" * 3)
    assert vision.recall("Monthly rent 45,000 deposit", "monthly RENT is 45,000 and deposit") == 1.0
    assert vision.recall("alpha beta gamma delta", "alpha") == 0.25
    assert vision.is_markdown_table("| a | b |\n|---|---|\n| 1 | 2 |")
    assert not vision.is_markdown_table("a, b\n1, 2")


def test_select_pages():
    md = "---\nfm\n---\n# T\n\n<!-- page 1 -->\none\n\n<!-- page 2 -->\ntwo\n\n<!-- page 3 -->\nthree\n"
    out = select_pages(md, "2-3")
    assert "# T" in out and "two" in out and "three" in out and "one" not in out
    assert select_pages(md, "") == md


# ------------------------------------------------------------------- config
def test_config_roundtrip(tmp_path):
    cfg = Config()
    cfg.watch.project_parents = ["~/Claude Projects", 'C:\\Users\\me\\"Proj"']
    cfg.ocr.languages = ["en", "hi"]
    cfg.figures.min_area = 0.05
    path = tmp_path / "config.toml"
    path.write_text(dump_config(cfg), encoding="utf-8")
    loaded = load_config(path)
    assert loaded.watch.project_parents == cfg.watch.project_parents
    assert loaded.ocr.languages == ["en", "hi"] and loaded.figures.min_area == 0.05
