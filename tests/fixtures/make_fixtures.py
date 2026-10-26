"""Generate small sample PDFs used by the test-suite and for trying the tool.

    python tests/fixtures/make_fixtures.py [output_dir]

Creates:
  * digital_report.pdf  - text layer + table + bar chart (2 pages)
  * scanned_letter.pdf  - image-only page (simulated scan) with a table
  * mixed.pdf           - digital + scanned + digital pages (5 pages)
"""

from __future__ import annotations

import io
import random
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
REVENUE = [120, 135, 150, 172]
COSTS = [80, 85, 92, 99]


def _chart_png() -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6, 3.5), dpi=150)
    x = range(len(QUARTERS))
    ax.bar([i - 0.2 for i in x], REVENUE, width=0.4, label="Revenue")
    ax.bar([i + 0.2 for i in x], COSTS, width=0.4, label="Costs")
    ax.set_xticks(list(x), QUARTERS)
    ax.set_ylabel("INR crore")
    ax.set_title("Revenue vs Costs FY2025")
    ax.legend()
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def make_digital(path: Path) -> None:
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Annual Operations Report FY2025", styles["Title"]),
        Paragraph("1. Summary", styles["Heading1"]),
        Paragraph(
            "Revenue grew steadily through the year while costs were contained. "
            "This report summarises quarterly performance and key obligations.",
            styles["BodyText"],
        ),
        Spacer(1, 0.4 * cm),
        Paragraph("2. Quarterly figures", styles["Heading1"]),
    ]
    data = [["Quarter", "Revenue (INR cr)", "Costs (INR cr)", "Margin %"]]
    for q, r, c in zip(QUARTERS, REVENUE, COSTS):
        data.append([q, str(r), str(c), f"{(r - c) / r * 100:.1f}"])
    table = Table(data)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    story += [table, PageBreak()]
    story += [
        Paragraph("3. Trend chart", styles["Heading1"]),
        RLImage(_chart_png(), width=15 * cm, height=8.75 * cm),
        Paragraph("Figure 1: Revenue vs Costs by quarter.", styles["Italic"]),
        Paragraph("4. Obligations", styles["Heading1"]),
        Paragraph(
            "The lessee shall pay rent on or before the 5th day of each month. "
            "Late payment attracts interest at 18% per annum.",
            styles["BodyText"],
        ),
    ]
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def make_scanned(path: Path) -> None:
    w, h = 1654, 2339  # A4 at 200 DPI
    img = Image.new("L", (w, h), 250)
    d = ImageDraw.Draw(img)
    big, body = _font(44), _font(30)
    y = 150
    d.text((150, y), "NOTICE OF LEASE RENEWAL", font=big, fill=10)
    y += 110
    lines = [
        "Date: 14 March 2025",
        "To: Sunrise Traders Pvt Ltd, Kolkata",
        "",
        "This is to inform you that the lease for Unit 4B expires on",
        "31 March 2025. The landlord proposes renewal on the terms below.",
    ]
    for line in lines:
        d.text((150, y), line, font=body, fill=20)
        y += 50
    y += 40
    cols = [150, 650, 1100, 1500]
    rows = [["Term", "Current", "Proposed"], ["Monthly rent", "45,000", "49,500"],
            ["Deposit", "1,35,000", "1,50,000"], ["Lock-in", "12 months", "24 months"]]
    for r, row in enumerate(rows):
        top = y + r * 70
        for c in range(3):
            d.rectangle([cols[c], top, cols[c + 1], top + 70], outline=0, width=2)
            d.text((cols[c] + 15, top + 18), row[c], font=body, fill=0)
    y += len(rows) * 70 + 80
    d.text((150, y), "Please confirm acceptance within 15 days.", font=body, fill=20)
    # simulate a scan: slight rotation, blur and speckle noise
    img = img.rotate(0.6, expand=False, fillcolor=250).filter(ImageFilter.GaussianBlur(0.7))
    px = img.load()
    rnd = random.Random(7)
    for _ in range(6000):
        px[rnd.randrange(w), rnd.randrange(h)] = rnd.randrange(80, 200)
    img.convert("RGB").save(path, "PDF", resolution=200.0)


def make_mixed(path: Path, digital: Path, scanned: Path) -> None:
    """digital (2 pages) + scanned (1 page) + digital (2 pages): two figures in two text runs."""
    import pypdfium2 as pdfium

    out = pdfium.PdfDocument.new()
    for src in (digital, scanned, digital):
        doc = pdfium.PdfDocument(str(src))
        out.import_pages(doc)
        doc.close()
    out.save(str(path))
    out.close()


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    out.mkdir(parents=True, exist_ok=True)
    make_digital(out / "digital_report.pdf")
    make_scanned(out / "scanned_letter.pdf")
    make_mixed(out / "mixed.pdf", out / "digital_report.pdf", out / "scanned_letter.pdf")
    print(f"Fixtures written to {out}")


if __name__ == "__main__":
    main()
