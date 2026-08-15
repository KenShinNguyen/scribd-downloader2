<p align="center">
  <img src="assets/scribd.svg" alt="Scribd" width="200">
</p>

<h1 align="center">Scribd Downloader</h1>

<p align="center">
  <b>Download Scribd documents as PDF for free - Fast, automated, and runs in background!</b>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
  </a>
  <a href="https://pypi.org/project/selenium/">
    <img src="https://img.shields.io/badge/Selenium-4.0+-green?style=for-the-badge&logo=selenium&logoColor=white" alt="Selenium 4.0+">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-orange?style=for-the-badge" alt="MIT License">
  </a>
</p>

<p align="center">
  <a href="https://buymeacoffee.com/mrsami">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee">
  </a>
  <a href="https://github.com/sponsors/fullstackusama">
    <img src="https://img.shields.io/badge/Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white" alt="GitHub Sponsors">
  </a>
  <a href="https://github.com/fullstackusama/scribd-downloader/stargazers">
    <img src="https://img.shields.io/github/stars/fullstackusama/scribd-downloader?style=for-the-badge&logo=github" alt="GitHub Stars">
  </a>
</p>

---

## Features

- **One-click download** - Just paste the Scribd URL and get your PDF
- **Real CLI** - Pass the URL as an argument, choose the output path, or export a page range
- **Flexible URL input** - `/document/...`, legacy `/doc/...`, `/presentation/...`, `/embeds/...`, or a bare document id
- **Runs in background** - Headless Chrome, no browser window pops up
- **No scrolling required** - Loads Scribd page data directly instead of simulating page-by-page scrolling
- **Clean PDFs** - No cookie banners, toolbars, or watermarks
- **Bounded-memory export** - Keeps only a configurable batch of fully loaded pages in Chrome at once
- **Disk-spooled merge** - Writes each rendered page to temporary storage before combining the final PDF with `pypdf`
- **Large document support** - Verified with image-heavy documents containing up to 2,552 pages
- **Better math rendering** - Preserves Scribd layout classes needed by equations and SVG content
- **Exact pagination** - Validates that every Scribd page produces exactly one PDF sheet
- **Dynamic page size** - Detects each rendered page's dimensions instead of forcing one fixed sheet size
- **Auto filename** - PDF named after the document URL automatically, with unsafe characters stripped
- **No accidental overwrites** - Refuses to clobber an existing file unless you pass `--force`
- **No login required** - Works with publicly viewable documents, without a Scribd account

---

## Requirements

- Python 3.10 or higher
- Google Chrome browser installed
- Chrome WebDriver (auto-managed by Selenium)

---

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/fullstackusama/scribd-downloader.git
   cd scribd-downloader
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

---

## Usage

Pass the URL directly:

```bash
python scribd-downloader.py https://www.scribd.com/document/123456789/Document-Title
```

Or run it with no arguments and paste the URL when prompted:

```bash
python scribd-downloader.py
Input link Scribd: https://www.scribd.com/document/123456789/Document-Title
```

Legacy `/doc/...` links, `/presentation/...` links, `/embeds/...` links, and a bare
document id (`123456789`) all work too.

### Options

| Flag | Description |
|------|-------------|
| `-o`, `--output PATH` | Output PDF path, or an existing directory (default: derived from the URL) |
| `-f`, `--force` | Overwrite the output file if it already exists |
| `-p`, `--pages SPEC` | Pages to export, e.g. `1-20` or `1-5,12,40-60` (default: all) |
| `--batch-size N` | Pages kept fully loaded in Chrome at once (default: `8`) |
| `--timeout SECONDS` | ChromeDriver command timeout for printing (default: `600`) |
| `--page-timeout SECONDS` | Per-batch page and image load timeout (default: `120`) |
| `--no-headless` | Show the browser window while debugging rendering |

Examples:

```bash
# Export only the first 20 pages to a chosen path
python scribd-downloader.py 123456789 --pages 1-20 -o ~/Documents/sample.pdf

# Lower memory use on a very long, image-heavy document
python scribd-downloader.py <url> --batch-size 4 --page-timeout 180
```

The exit code is `0` on success, `1` on failure, and `130` when cancelled with Ctrl+C.

### What happens during a run

The script will:

- Open the document in headless Chrome
- Load document pages directly in bounded batches
- Release each batch from Chrome after printing to control memory use
- Remove unwanted elements (toolbars, cookie banners)
- Spool individual pages to temporary storage and merge the final PDF
- Save the PDF to the chosen path, or to the current directory by default

---

## Example Output

```text
$ python scribd-downloader.py https://www.scribd.com/document/903361807/WorkdaySimpleIntegrations-EIB-31v2
Link embed: https://www.scribd.com/embeds/903361807/content
Output file: C:\Users\...\WorkdaySimpleIntegrations-EIB-31v2.pdf

Starting Chrome browser...
Cookie dialogs hidden.
Top toolbar removed.
Bottom toolbar removed.
Adjusted 1 scroll containers for print.
Print CSS injected.

Saving PDF as: C:\Users\...\WorkdaySimpleIntegrations-EIB-31v2.pdf
  Export mode: Individual document pages
  Margins: None
  Headers/Footers: Disabled
  Document pages: 316
  Selected pages: 316
  ChromeDriver command timeout: 600s
Exporting 316 document pages in bounded batches of 8...
  Loading page batch 1-8/316...
  Page 1/316 1002x1296px -> 10.438"x13.500"
    OK: exactly 1 PDF sheet
  ...
Merging 316 disk-spooled PDF pages...
PDF saved successfully to: C:\Users\...\WorkdaySimpleIntegrations-EIB-31v2.pdf
  228.49 MB in 58.4s
Browser closed.
```

---

## PDF Settings

| Setting | Value |
|---------|-------|
| Page Size | Detected dynamically from Scribd's rendered page |
| Margins | None (0) |
| Headers/Footers | Disabled |
| Background Graphics | Enabled |

---

## How It Works

1. **URL Conversion** - Converts Scribd document URL to embeddable format
2. **Headless Browser** - Opens Chrome in background (invisible)
3. **Batched Page Loading** - Loads a small group of Scribd pages and their images directly without scrolling
4. **Cleanup** - Removes toolbars, cookie banners, and overlays while preserving Scribd layout classes
5. **Per-page Export** - Detects each page's rendered size and prints exactly one PDF sheet through Chrome DevTools Protocol
6. **Memory Release** - Removes the completed batch from Chrome and requests garbage collection
7. **Disk-spooled Merge** - Combines the temporary one-page PDFs into the final document with `pypdf`
8. **Auto Close** - Browser closes automatically after saving

---

## Benchmarks

Reference results from one Windows machine are shown below. Performance varies with network speed, document complexity, CPU, RAM, Chrome version, and storage speed.

| Document | Pages | Total Time | Output Size | Blank Pages | Peak Combined RAM |
|----------|------:|-----------:|------------:|------------:|------------------:|
| CSS Solved Past Papers | 359 | 58.36 seconds | 228.49 MB | 0 | 1.47 GB |
| Manual de Servicio MX-305 | 2,552 | 14 minutes 12 seconds | 237.28 MB | 0 | 2.46 GB |

The batch size was `8` in both tests. Long documents still take time because Chrome must print every page individually, but fully loaded browser content is kept to one batch at a time.

---

## Troubleshooting

### "ChromeDriver not found" error
The script uses Selenium Manager to auto-download ChromeDriver. If you face issues:
```bash
pip install --upgrade selenium
```

### PDF not saving
- Ensure you have write permissions in the current directory
- Check if the Scribd URL is valid and accessible
- For very large documents, increase `SCRIBD_CDP_TIMEOUT` (default: `600`)

### "No printable document pages were detected"
The document is private, removed, or preview-only. Only publicly viewable documents can be exported.

### Blank pages in PDF
- Some documents may have DRM protection
- Try increasing `--page-timeout` if page images load slowly
- If a document still renders incorrectly, try visible mode with `--no-headless`

### Very large documents
- Ensure the drive containing your temporary directory has enough free space for individual page PDFs
- Reduce `--batch-size` if Chrome uses too much memory
- Increase `--page-timeout` when slow image assets time out
- Use `--pages` to export a slice at a time
- Long documents still take time because each page is printed and validated separately

### Large, image-heavy, or math-heavy documents

```bash
python scribd-downloader.py <url> --timeout 900 --page-timeout 180 --batch-size 4
```

The same settings can be supplied through environment variables, which act as
defaults when the matching flag is not passed:

| Variable | Flag | Default |
|----------|------|---------|
| `SCRIBD_CDP_TIMEOUT` | `--timeout` | `600` |
| `SCRIBD_PAGE_LOAD_TIMEOUT` | `--page-timeout` | `120` |
| `SCRIBD_EXPORT_BATCH_SIZE` | `--batch-size` | `8` |
| `SCRIBD_HEADLESS=0` | `--no-headless` | headless on |

---

## Development

Install the development dependencies and run the test suite:

```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover the pure helpers (URL parsing, filename sanitizing, page-range
parsing, output-path resolution, settings precedence) and need neither Chrome
nor network access.

---

## Contributing

Contributions are welcome! Feel free to:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## Support the Project

If you find this tool useful, consider supporting its development:

<p align="center">
  <a href="https://buymeacoffee.com/mrsami">
    <img src="assets/buymeacoffee.svg" alt="Buy Me A Coffee" width="40" height="40">
  </a>
</p>

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Disclaimer

This tool is for educational purposes only. Please respect copyright laws and Scribd's Terms of Service. Only download documents you have the right to access.

---

<p align="center">
  Made with ❤️ by <a href="https://github.com/fullstackusama">Usama Nazir</a>
</p>

<p align="center">
  If you find this useful, please consider giving it a ⭐
</p>
