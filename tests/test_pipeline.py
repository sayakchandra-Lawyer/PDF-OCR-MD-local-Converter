"""End-to-end conversion with the real Docling models and a fake Ollama server.

Slow on first run (downloads the Docling layout/table models, ~500 MB).
Skip with:  pytest -m "not slow"
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures.make_fixtures import make_digital, make_mixed, make_scanned  # noqa: E402
from mock_ollama import MockOllama  # noqa: E402

from pdf2md.config import Config  # noqa: E402
from pdf2md.service import Service  # noqa: E402

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    d = tmp_path_factory.mktemp("fx")
    make_digital(d / "digital_report.pdf")
    make_scanned(d / "scanned_letter.pdf")
    make_mixed(d / "mixed.pdf", d / "digital_report.pdf", d / "scanned_letter.pdf")
    return d


def _project(tmp_path: Path, fixtures: Path, *names: tuple[str, str]) -> tuple[Path, Config]:
    parent = tmp_path / "Projects"
    proj = parent / "P01_Test"
    proj.mkdir(parents=True)
    for src, dst in names:
        shutil.copy(fixtures / src, proj / dst)
    cfg = Config()
    cfg.watch.project_parents = [str(parent)]
    cfg.llm.ollama_url = "http://127.0.0.1:9"  # nothing listens here
    return proj, cfg


def test_digital_pdf_without_llm(tmp_path, fixtures):
    proj, cfg = _project(tmp_path, fixtures, ("digital_report.pdf", "Annual Report (FY25).pdf"))
    results = Service(cfg).scan_all()
    assert [r.status for r in results] == ["converted"]
    md = (proj / "MD" / "Annual-Report-FY25.md").read_text(encoding="utf-8")
    assert (proj / "PDF" / "Annual-Report-FY25.pdf").exists()
    assert not (proj / "Annual Report (FY25).pdf").exists()
    assert 'original_filename: "Annual Report (FY25).pdf"' in md
    assert "<!-- page 1 -->" in md and "<!-- page 2 -->" in md
    assert "| Q4 | 172 | 99 | 42.4 |" in md
    # the figure is on page 2 only
    page1 = md.split("<!-- page 2 -->")[0]
    assert "[Figure 1" not in page1 and "[Figure 1 | page 2 | bar chart]" in md
    assert list((proj / "MD" / "assets").rglob("*.png"))
    # second scan is served from cache
    assert [r.status for r in Service(cfg).scan_all()] == ["cached"]


def test_scanned_pdf_and_figures_with_vision_model(tmp_path, fixtures):
    proj, cfg = _project(
        tmp_path, fixtures, ("scanned_letter.pdf", "Lease Notice.pdf"), ("digital_report.pdf", "report.pdf")
    )
    with MockOllama() as mock:
        cfg.llm.ollama_url = mock.url
        results = Service(cfg).scan_all()
    assert sorted(r.status for r in results) == ["converted", "converted"]
    scan = (proj / "MD" / "Lease-Notice.md").read_text(encoding="utf-8")
    assert "vision_ocr_pages: [1]" in scan and "NOTICE OF LEASE RENEWAL" in scan
    assert "| Lock-in | 12 months | 24 months |" in scan
    report = (proj / "MD" / "report.md").read_text(encoding="utf-8")
    assert "Revenue vs Costs FY2025**" in report and "| Q4 | 172 | 99 |" in report
    assert "Described by local vision model" in report


def test_crosscheck_rejects_hallucinated_page(tmp_path, fixtures):
    proj, cfg = _project(tmp_path, fixtures, ("scanned_letter.pdf", "scan.pdf"))
    bogus = "# Invoice\n\nThis page lists unrelated items: apples, oranges, bananas, and grapes for sale."
    with MockOllama(page_text=bogus) as mock:
        cfg.llm.ollama_url = mock.url
        (res,) = Service(cfg).scan_all()
    md = (proj / "MD" / "scan.md").read_text(encoding="utf-8")
    assert "apples" not in md and "Monthly rent" in md
    assert any("disagreed" in w for w in res.entry["warnings"])


def test_mixed_digital_and_scanned_pages(tmp_path, fixtures):
    proj, cfg = _project(tmp_path, fixtures, ("mixed.pdf", "bundle.pdf"))
    (res,) = Service(cfg).scan_all()
    assert res.status == "converted"
    md = (proj / "MD" / "bundle.md").read_text(encoding="utf-8")
    assert res.entry["pages"] == 5 and res.entry["ocr_pages"] == [3]
    parts = re.split(r"<!-- page (\d+) -->", md)
    pages = {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}
    assert set(pages) == {1, 2, 3, 4, 5}
    assert "| Q4 | 172 | 99 | 42.4 |" in pages[1] and "| Q4 | 172 | 99 | 42.4 |" in pages[4]
    assert "Monthly rent" in pages[3]
    # each text run numbers its pictures from 0: figures must land on their own pages
    assert "[Figure 1 | page 2 |" in pages[2] and "[Figure 2 | page 5 |" in pages[5]
    assert "[Figure" not in pages[1] + pages[3] + pages[4]
    assert len(list((proj / "MD" / "assets" / "bundle").glob("*.png"))) == 2
