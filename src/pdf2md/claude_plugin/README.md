# PDF-OCR-MD Local Converter

Stop paying tokens every time Claude reads the same PDF. This plugin makes Claude read the **Markdown version** of
your PDFs instead of the PDFs themselves. The Markdown is produced **once, on your own computer**, by the free,
open-source companion app **pdf2md** (part of the same project). It includes:

- **OCR for scanned pages**, using a local vision model cross-checked against classical OCR
- **Tables** rebuilt as Markdown tables
- **Charts and figures** described in text, with a table of their values
- **Page markers** (`<!-- page N -->`) so Claude can cite page numbers

Nothing is uploaded: all conversion happens on your machine.

## What you need

Install the pdf2md app first. It runs in the background, watches your project folders and converts every new PDF
into an `MD/` folder next to a `PDF/` folder:

- **Windows:** one-click installer on the
  [releases page](https://github.com/sayakchandra-Lawyer/PDF-OCR-MD-local-Converter/releases/latest)
- **macOS / Linux:** see the [project README](https://github.com/sayakchandra-Lawyer/PDF-OCR-MD-local-Converter#install)

Without the app the plugin does nothing harmful. Claude simply reads PDFs as usual.

## Install the plugin

- **Claude Desktop / Cowork:** Customize > Plugins, find *PDF-OCR-MD Local Converter* and click Install (or upload the
  plugin zip from the releases page).
- **Claude Code:**

  ```
  /plugin install pdf-ocr-md-local-converter --marketplace sayakchandra-Lawyer/PDF-OCR-MD-local-Converter
  ```

## What the plugin contains

**Skill `pdf-markdown-first`.** Instructions that tell Claude, before reading any PDF, to find the project's
`.pdf2md.json` marker, open the `MD/INDEX.md` listing and read the matching `.md` file. If a conversion is still
running, Claude waits for it. It opens the original PDF only when you ask, or to check one specific page visually.

**Hook `PreToolUse` on the `Read` tool.** It runs `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pdf_read_guard.py` whenever
Claude is about to read a file. For files that are not PDFs, or PDFs outside a pdf2md project, it does nothing. For a
PDF that pdf2md has already converted, it stops the read and points Claude to the `.md` file instead. If the
conversion is still in progress it waits for up to 150 seconds. Reading specific pages of a PDF (the `pages` option)
is always allowed.

## Data and privacy

- The hook only **reads** two small local files: the project's `.pdf2md.json` marker and the pdf2md manifest
  (`MD/.pdf2md/manifest.json`). It uses only the Python standard library.
- The plugin makes **no network requests**. It sends no data anywhere, stores nothing, reads no credentials and
  does not change Claude's settings or permissions.
- It requires `python3` on the machine where Claude runs (present in Cowork's environment and on macOS / Linux).

## License

MIT. See `LICENSE`. Source code: https://github.com/sayakchandra-Lawyer/PDF-OCR-MD-local-Converter
