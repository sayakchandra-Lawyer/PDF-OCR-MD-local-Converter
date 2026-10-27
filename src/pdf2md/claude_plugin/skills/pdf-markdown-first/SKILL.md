---
name: pdf-markdown-first
description: Use BEFORE opening, reading, summarising, quoting, searching or extracting anything from a PDF in the user's folders or projects. The user's PDFs are converted locally to Markdown by pdf2md (OCR for scans, tables as Markdown tables, charts described with data); read the cached .md instead of the PDF to save tokens, and get missing conversions made.
---

# Read PDFs through their local Markdown conversion

The user runs **pdf2md** on their computer. It converts every PDF in their project folders to Markdown,
locally and once, so you never need to spend tokens reading the PDF itself. Follow this workflow every time
a task needs the content of a PDF.

## 1. Find the Markdown twin

- A project folder managed by pdf2md has a `.pdf2md.json` file at its root. It names the folders:
  `pdf_dir` (originals, usually `PDF/` or `01_Context/PDF/`) and `md_dir` (Markdown, usually `MD/` or
  `01_Context/MD/`).
- `<md_dir>/INDEX.md` lists every converted PDF with pages, OCR pages, tables, figures and status. Read it
  first; it is small.
- `<pdf_dir>/<name>.pdf` maps to `<md_dir>/<name>.md`. PDFs dropped loose into the project are moved into
  `<pdf_dir>/` with a cleaned-up name (spaces -> hyphens); the original name is kept in the index and in the
  Markdown front matter (`original_filename`).

## 2. Read the Markdown, not the PDF

- The front matter gives the source, page count, which pages were OCR'd and any conversion warnings.
- `<!-- page N -->` marks where page N starts. Cite page numbers from these markers.
- For long documents, search first (grep for keywords) and read only the pages you need.
- Tables are GitHub Markdown tables. Charts and figures appear as blocks like
  `**[Figure 2 | page 7 | bar chart] Title**` with a summary, a data table and the visible text. These were
  written by a local vision model: for decisive numbers, say they come from a chart reading, and if precision
  matters, verify by reading only that page of the PDF (Read with the `pages` parameter).
- Pages listed in `ocr_pages` were scanned images. OCR can misread digits, names and amounts. For anything
  critical (amounts, dates, case numbers), mention that the source was a scan or visually verify that page.

## 3. If there is no Markdown yet

1. If the `pdf_to_markdown` tool (pdf2md MCP server) is available, call it with the PDF path and
   `include_content=false` when you can read files yourself; then read the `.md` it reports. If it answers
   "converting", call it again with the same path to collect the result.
2. Otherwise the background watcher converts new PDFs automatically, usually within a minute or two (scanned
   PDFs take longer). Check the status column in `INDEX.md`:
   - `queued` / `converting...`: tell the user you are waiting, `sleep 30`, re-check (up to ~5 minutes).
   - not listed at all: the watcher may not have seen it yet; wait briefly and re-check once.
   - `FAILED`: tell the user the error shown, then (only then) read the PDF directly, a few pages at a time.
3. No `.pdf2md.json` above the PDF: the folder is not watched. Suggest the user moves the PDF into a watched
   project folder (or runs `pdf2md add-folder <folder>`), and ask before reading a large PDF directly.

## 4. Rules

- Never edit files in the MD folder by hand. To refresh a conversion, use `pdf_to_markdown(force=true)` or
  ask the user to run `pdf2md convert --force "<pdf>"`.
- Do not move or rename files in the PDF folder; pdf2md keeps the index in sync.
- A PDF attached directly to the chat is already in your context; answer from it, and suggest saving future
  PDFs into the project folder so they are converted once and reused.
