"""
Scribd Document Downloader
==========================

A Selenium-based utility that loads a Scribd embed and saves it as a PDF.

Key behaviors:
1. Converts a Scribd document URL to the embed/content URL.
2. Opens the document in headless Chrome.
3. Removes UI overlays without stripping layout classes needed for rendering.
4. Loads pages in bounded batches so memory stays flat on long documents.
5. Prints each Scribd page to exactly one PDF sheet through the Chrome
   DevTools Protocol, spooling sheets to disk.
6. Merges the spooled sheets into the final PDF with pypdf.
"""

import argparse
import base64
import os
import re
import sys
import tempfile
import time
from io import BytesIO
from urllib.parse import unquote, urlparse

from pypdf import PdfReader, PdfWriter
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait


CSS_PIXELS_PER_INCH = 96.0
DOCUMENT_READY_TIMEOUT_SECONDS = 60
PRINT_ATTEMPTS_PER_PAGE = 2

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INTERRUPTED = 130


def env_int(name, default, minimum=None):
    """Read a positive integer from the environment, ignoring bad values."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw.strip())
    except ValueError:
        print(f"Warning: {name}={raw!r} is not an integer; using {default}.")
        return default

    if minimum is not None and value < minimum:
        print(f"Warning: {name}={value} is below {minimum}; using {minimum}.")
        return minimum

    return value


def env_flag(name, default):
    """Read a boolean flag from the environment."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    return raw.strip().lower() not in {"0", "false", "no", "off"}


class ExportSettings:
    """Runtime tunables, resolved from environment defaults and CLI flags."""

    def __init__(
        self,
        cdp_timeout_seconds,
        page_load_timeout_seconds,
        export_batch_size,
        headless,
    ):
        self.cdp_timeout_seconds = cdp_timeout_seconds
        self.page_load_timeout_seconds = page_load_timeout_seconds
        self.export_batch_size = export_batch_size
        self.headless = headless

    @classmethod
    def from_args(cls, args):
        return cls(
            cdp_timeout_seconds=(
                args.timeout
                if args.timeout is not None
                else env_int("SCRIBD_CDP_TIMEOUT", 600, minimum=30)
            ),
            page_load_timeout_seconds=(
                args.page_timeout
                if args.page_timeout is not None
                else env_int("SCRIBD_PAGE_LOAD_TIMEOUT", 120, minimum=10)
            ),
            export_batch_size=(
                args.batch_size
                if args.batch_size is not None
                else env_int("SCRIBD_EXPORT_BATCH_SIZE", 8, minimum=1)
            ),
            headless=(
                False if args.no_headless else env_flag("SCRIBD_HEADLESS", True)
            ),
        )


def build_chrome_options(runtime_profile_dir, headless):
    """Create Chrome options for reliable headless PDF generation."""
    options = Options()

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1600,2200")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument(f"--user-data-dir={runtime_profile_dir}")
    options.add_argument("--force-color-profile=srgb")
    options.add_argument("--hide-scrollbars")
    return options


def extract_document_id(url):
    """
    Extract the numeric Scribd document id from a user-supplied string.

    Accepts document, doc, presentation, and embed URLs on scribd.com (with or
    without a subdomain and with either scheme), as well as a bare numeric id.

    Args:
        url: URL or numeric document id.

    Returns:
        The document id as a string, or None when nothing usable is found.
    """
    if url is None:
        return None

    candidate = url.strip()
    if not candidate:
        return None

    if candidate.isdigit():
        return candidate

    if "//" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    host = parsed.netloc.split("@")[-1].split(":")[0].lower()

    if host != "scribd.com" and not host.endswith(".scribd.com"):
        return None

    match = re.search(
        r"^/(?:document|doc|presentation|embeds)/(\d+)(?:/|$)",
        parsed.path,
    )
    return match.group(1) if match else None


def build_embed_url(document_id):
    """Build the embeddable content URL for a Scribd document id."""
    return f"https://www.scribd.com/embeds/{document_id}/content"


def sanitize_filename(name, fallback):
    """
    Turn an arbitrary URL segment into a safe single-path-component filename.

    Strips directory separators, control characters, characters that are
    illegal on Windows, and trailing dots or spaces. Falls back when nothing
    usable survives.
    """
    cleaned = unquote(name or "").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', "", cleaned)
    cleaned = cleaned.strip().strip(".").strip()

    if cleaned.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        cleaned = f"{cleaned}_"

    if not cleaned:
        return fallback

    return cleaned[:180]


def default_output_filename(url, document_id):
    """Build the default output filename from the URL's last path segment."""
    fallback = f"scribd-{document_id}"
    candidate = (url or "").strip()

    if candidate.isdigit() or not candidate:
        return f"{fallback}.pdf"

    if "//" not in candidate:
        candidate = f"https://{candidate}"

    path = urlparse(candidate).path.rstrip("/")
    last_segment = path.split("/")[-1] if path else ""

    if last_segment.isdigit():
        last_segment = ""

    return f"{sanitize_filename(last_segment, fallback)}.pdf"


def parse_page_selection(spec, total_pages):
    """
    Parse a page selection such as "1-20", "3", or "1-5,12,40-60".

    Args:
        spec: Selection string, or None for every page.
        total_pages: Number of pages in the document.

    Returns:
        Sorted list of 1-based page numbers.

    Raises:
        ValueError: If the selection is malformed or out of range.
    """
    if spec is None:
        return list(range(1, total_pages + 1))

    selected = set()

    for part in spec.split(","):
        chunk = part.strip()
        if not chunk:
            continue

        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", chunk)
        if not match:
            raise ValueError(f"Invalid page selection: {chunk!r}")

        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start

        if start < 1 or end < start:
            raise ValueError(f"Invalid page range: {chunk!r}")

        if start > total_pages:
            raise ValueError(
                f"Page range {chunk!r} starts past the last page ({total_pages})."
            )

        selected.update(range(start, min(end, total_pages) + 1))

    if not selected:
        raise ValueError("Page selection is empty.")

    return sorted(selected)


def chunked(items, size):
    """Yield successive lists of at most ``size`` items."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def configure_command_timeout(driver, timeout_seconds):
    """
    Increase the Selenium HTTP timeout used for ChromeDriver commands.

    Large image-heavy documents can spend minutes inside Page.printToPDF before
    ChromeDriver responds, so the default 120 second timeout is too small.
    """
    executor = getattr(driver, "command_executor", None)
    if executor is None:
        return

    client_config = getattr(executor, "client_config", None)
    if client_config is None:
        client_config = getattr(executor, "_client_config", None)

    if client_config is not None:
        client_config.timeout = timeout_seconds


def hide_cookie_dialogs(driver):
    """Dismiss and remove common cookie, consent, and privacy banners."""
    driver.execute_script(
        """
        const closeButtonSelectors = [
            '[class*="cookie"] [class*="close"]',
            '[class*="cookie"] [class*="dismiss"]',
            '[class*="cookie"] button[aria-label*="close"]',
            '[class*="cookie"] button[aria-label*="Close"]',
            '[class*="consent"] [class*="close"]',
            '[class*="consent"] [class*="dismiss"]',
            '[class*="banner"] [class*="close"]',
            '[class*="banner"] [class*="dismiss"]',
            '[class*="notice"] [class*="close"]',
            '[class*="notice"] [class*="dismiss"]',
            'button[class*="close"]',
            'button[aria-label="Close"]',
            'button[aria-label="close"]',
            'button[aria-label="Dismiss"]',
            '[data-dismiss]',
            '[role="button"][class*="close"]'
        ];

        closeButtonSelectors.forEach((selector) => {
            try {
                document.querySelectorAll(selector).forEach((button) => button.click());
            } catch (error) {}
        });

        const cookieSelectors = [
            '[class*="cookie"]',
            '[class*="Cookie"]',
            '[class*="consent"]',
            '[class*="Consent"]',
            '[class*="gdpr"]',
            '[class*="GDPR"]',
            '[id*="cookie"]',
            '[id*="Cookie"]',
            '[id*="consent"]',
            '[id*="gdpr"]',
            '[class*="privacy-notice"]',
            '[class*="Privacy"]',
            '[class*="cookie-banner"]',
            '[class*="cookie-notice"]',
            '[class*="cookie-popup"]',
            '[class*="cookie-modal"]',
            '[class*="CookieConsent"]',
            '[class*="notice-banner"]',
            '.cc-window',
            '.cc-banner',
            '#onetrust-consent-sdk',
            '#onetrust-banner-sdk',
            '.evidon-banner',
            '.truste_box_overlay',
            '[class*="osano-cm"]',
            '[id*="osano"]'
        ];

        cookieSelectors.forEach((selector) => {
            try {
                document.querySelectorAll(selector).forEach((element) => element.remove());
            } catch (error) {}
        });

        document.querySelectorAll('body *').forEach((element) => {
            try {
                const style = getComputedStyle(element);
                if (style.position !== 'fixed' && style.position !== 'sticky') {
                    return;
                }

                if (element.getBoundingClientRect().top >= 100) {
                    return;
                }

                const text = (element.innerText || '').toLowerCase();
                if (
                    text.includes('cookie') ||
                    text.includes('privacy') ||
                    text.includes('consent') ||
                    text.includes('analytics') ||
                    text.includes('advertising') ||
                    text.includes('personalization')
                ) {
                    element.remove();
                }
            } catch (error) {}
        });
        """
    )


def prepare_document_for_print(driver):
    """
    Remove UI chrome and make the scroll containers printable.

    Removing the .document_scroller class entirely can break descendant CSS
    needed by math- and font-heavy documents, so we keep the class and only
    override the few layout properties that interfere with print.
    """
    result = driver.execute_script(
        """
        const removed = { toolbarTop: false, toolbarBottom: false, containers: 0 };

        const toolbarTop = document.querySelector('.toolbar_top');
        if (toolbarTop) {
            toolbarTop.remove();
            removed.toolbarTop = true;
        }

        const toolbarBottom = document.querySelector('.toolbar_bottom');
        if (toolbarBottom) {
            toolbarBottom.remove();
            removed.toolbarBottom = true;
        }

        document.querySelectorAll('.document_scroller').forEach((element) => {
            element.setAttribute('data-scribd-print-root', 'true');
            element.style.position = 'static';
            element.style.top = 'auto';
            element.style.bottom = 'auto';
            element.style.left = 'auto';
            element.style.right = 'auto';
            element.style.overflow = 'visible';
            element.style.maxHeight = 'none';
            element.style.height = 'auto';
            element.style.margin = '0';
            element.style.padding = '0';
            removed.containers += 1;
        });

        return removed;
        """
    )

    if result["toolbarTop"]:
        print("Top toolbar removed.")
    if result["toolbarBottom"]:
        print("Bottom toolbar removed.")

    print(f"Adjusted {result['containers']} scroll containers for print.")


def inject_print_styles(driver):
    """Install conservative print CSS without hiding Scribd document content."""
    driver.execute_script(
        """
        const existing = document.getElementById('scribd-print-styles');
        if (existing) {
            existing.remove();
        }

        const style = document.createElement('style');
        style.id = 'scribd-print-styles';
        style.textContent = `
            [class*="cookie"],
            [class*="Cookie"],
            [class*="consent"],
            [class*="Consent"],
            [class*="gdpr"],
            [class*="privacy-notice"],
            [class*="notice-banner"],
            [id*="cookie"],
            [id*="consent"],
            [class*="osano-cm"],
            [id*="osano"] {
                display: none !important;
                visibility: hidden !important;
                opacity: 0 !important;
                height: 0 !important;
                overflow: hidden !important;
            }

            [data-scribd-print-root="true"],
            .document_scroller {
                position: static !important;
                top: auto !important;
                right: auto !important;
                bottom: auto !important;
                left: auto !important;
                overflow: visible !important;
                height: auto !important;
                max-height: none !important;
                margin: 0 !important;
                padding: 0 !important;
            }

            @media print {
                html,
                body {
                    margin: 0 !important;
                    padding: 0 !important;
                    -webkit-print-color-adjust: exact !important;
                    print-color-adjust: exact !important;
                }

                .toolbar_top,
                .toolbar_bottom {
                    display: none !important;
                }

                [data-scribd-print-root="true"],
                .document_scroller {
                    position: static !important;
                    top: auto !important;
                    right: auto !important;
                    bottom: auto !important;
                    left: auto !important;
                    overflow: visible !important;
                    height: auto !important;
                    max-height: none !important;
                    margin: 0 !important;
                    padding: 0 !important;
                }

                mjx-container,
                .MathJax,
                .katex,
                math,
                svg {
                    visibility: visible !important;
                    overflow: visible !important;
                }
            }
        `;

        document.head.appendChild(style);
        """
    )

    print("Print CSS injected.")


def count_document_pages(driver):
    """Return the number of printable Scribd page containers."""
    return driver.execute_script(
        "return document.querySelectorAll('.outer_page').length;"
    )


def load_page_batch(driver, page_numbers, settings):
    """Load one bounded batch of page DOM and image assets."""
    timeout_seconds = settings.page_load_timeout_seconds
    configure_command_timeout(driver, timeout_seconds + 10)
    driver.set_script_timeout(timeout_seconds + 10)

    try:
        result = driver.execute_async_script(
            """
            const pageNumbers = arguments[0];
            const timeoutMs = arguments[1];
            const done = arguments[arguments.length - 1];
            const manager = window.docManager;

            if (!manager || !manager.pages) {
                done({supported: false});
                return;
            }

            const states = pageNumbers.map((pageNum) => ({
                pageNum,
                page: manager.pages[pageNum],
                error: null,
                imagesTurnedOn: false,
                reloaded: false
            }));
            const startedAt = Date.now();
            const RELOAD_AFTER_MS = 3000;

            function startLoad(state) {
                try {
                    if (!state.page.innerPageElem) {
                        state.page.load();
                    }
                } catch (error) {
                    state.error = String(error);
                }
            }

            for (const state of states) {
                if (!state.page) {
                    state.error = 'page object missing';
                    continue;
                }

                if (!state.page.loadHasStarted) {
                    startLoad(state);
                }
            }

            function pendingReport() {
                return states
                    .filter((state) => (
                        state.error ||
                        !state.page ||
                        !state.page.innerPageElem ||
                        Array.from(
                            state.page.innerPageElem.querySelectorAll('img')
                        ).some((image) => !image.complete)
                    ))
                    .map((state) => ({
                        pageNum: state.pageNum,
                        reason: state.error || 'page or image load timed out'
                    }));
            }

            const timer = setInterval(() => {
                let ready = 0;

                for (const state of states) {
                    if (state.error) {
                        ready += 1;
                        continue;
                    }

                    const page = state.page;
                    if (!page.innerPageElem) {
                        // A page released by an earlier batch keeps
                        // loadHasStarted set, so re-arm the load once instead
                        // of waiting out the whole timeout.
                        if (
                            !state.reloaded &&
                            Date.now() - startedAt >= RELOAD_AFTER_MS
                        ) {
                            state.reloaded = true;
                            startLoad(state);
                        }
                        continue;
                    }

                    try {
                        page.display();
                        if (!state.imagesTurnedOn) {
                            page.turnOnImages();
                            state.imagesTurnedOn = true;
                        }
                    } catch (error) {
                        state.error = String(error);
                        ready += 1;
                        continue;
                    }

                    const images = Array.from(
                        page.innerPageElem.querySelectorAll('img')
                    );

                    if (images.every((image) => image.complete)) {
                        ready += 1;
                    }
                }

                if (ready === states.length || Date.now() - startedAt >= timeoutMs) {
                    clearInterval(timer);
                    done({supported: true, failed: pendingReport()});
                }
            }, 50);
            """,
            list(page_numbers),
            timeout_seconds * 1000,
        )
    finally:
        # printToPDF needs the larger CDP budget, not the page-load budget.
        configure_command_timeout(driver, settings.cdp_timeout_seconds)

    if not result.get("supported"):
        raise RuntimeError(
            "Scribd direct page loader is unavailable. The embed layout may "
            "have changed, or the document may not be publicly viewable."
        )

    if result["failed"]:
        details = ", ".join(
            f"{item['pageNum']} ({item['reason']})" for item in result["failed"]
        )
        raise RuntimeError(f"Failed to load Scribd page(s): {details}")


def release_page_batch(driver, page_numbers):
    """Release printed page DOM and image resources from Chrome."""
    try:
        driver.execute_script(
            """
            const manager = window.docManager;
            if (!manager || !manager.pages) {
                return;
            }

            for (const pageNum of arguments[0]) {
                const page = manager.pages[pageNum];
                if (!page) {
                    continue;
                }

                try {
                    page.remove();
                } catch (error) {
                    const container = document.getElementById(`outer_page_${pageNum}`);
                    if (container) {
                        const inner = container.querySelector('.newpage');
                        if (inner) {
                            inner.remove();
                        }
                    }
                }
            }
            """,
            list(page_numbers),
        )
        driver.execute_cdp_cmd("HeapProfiler.collectGarbage", {})
    except WebDriverException:
        # Releasing memory is best effort; never fail the export over it.
        pass


def isolate_page_for_print(driver, page_number):
    """
    Show only the requested page and size the print sheet to match it.

    Returns:
        Dict with the page's pixel width and height, or None when the page
        container is missing.
    """
    return driver.execute_script(
        """
        const pageNumber = arguments[0];

        const pages = Array.from(document.querySelectorAll('.outer_page'));
        const target =
            document.getElementById('outer_page_' + pageNumber) ||
            pages[pageNumber - 1];

        if (!target) {
            return null;
        }

        const oldStyle = document.getElementById('isolated-page-print-style');
        if (oldStyle) {
            oldStyle.remove();
        }

        // Restore every page before measuring, so a previous isolation pass
        // cannot leak into this page's geometry.
        pages.forEach((page) => {
            page.style.removeProperty('display');
            page.style.removeProperty('visibility');
            page.style.removeProperty('position');
            page.style.removeProperty('top');
            page.style.removeProperty('left');
            page.style.removeProperty('right');
            page.style.removeProperty('bottom');
            page.style.removeProperty('margin');
            page.style.removeProperty('break-after');
            page.style.removeProperty('page-break-after');
            page.style.removeProperty('break-before');
            page.style.removeProperty('page-break-before');
            page.removeAttribute('data-export-target');
        });

        const rect = target.getBoundingClientRect();
        const width = Math.ceil(rect.width);
        const height = Math.ceil(rect.height);

        target.setAttribute('data-export-target', 'true');

        const style = document.createElement('style');
        style.id = 'isolated-page-print-style';
        style.textContent = `
            @page {
                size: ${width}px ${height}px;
                margin: 0;
            }

            @media print {
                html,
                body {
                    width: ${width}px !important;
                    height: ${height}px !important;
                    min-width: ${width}px !important;
                    min-height: ${height}px !important;
                    max-width: ${width}px !important;
                    max-height: ${height}px !important;
                    margin: 0 !important;
                    padding: 0 !important;
                    overflow: hidden !important;
                    -webkit-print-color-adjust: exact !important;
                    print-color-adjust: exact !important;
                }

                .outer_page {
                    display: none !important;
                }

                .outer_page[data-export-target="true"] {
                    display: block !important;
                    visibility: visible !important;
                    position: absolute !important;
                    top: 0 !important;
                    left: 0 !important;
                    right: auto !important;
                    bottom: auto !important;
                    width: ${width}px !important;
                    height: ${height}px !important;
                    min-width: 0 !important;
                    min-height: 0 !important;
                    max-width: none !important;
                    max-height: none !important;
                    margin: 0 !important;
                    padding: 0 !important;
                    transform: none !important;
                    break-before: auto !important;
                    break-after: auto !important;
                    break-inside: auto !important;
                    page-break-before: auto !important;
                    page-break-after: auto !important;
                    page-break-inside: auto !important;
                    overflow: hidden !important;
                }
            }
        `;

        document.head.appendChild(style);

        return {width, height};
        """,
        page_number,
    )


def print_page_to_pdf_bytes(driver, width_inches, height_inches):
    """Print the currently isolated page and return the PDF bytes."""
    last_error = None

    for attempt in range(1, PRINT_ATTEMPTS_PER_PAGE + 1):
        try:
            result = driver.execute_cdp_cmd(
                "Page.printToPDF",
                {
                    "landscape": False,
                    "displayHeaderFooter": False,
                    "printBackground": True,
                    "scale": 1,
                    "paperWidth": width_inches,
                    "paperHeight": height_inches,
                    "marginTop": 0,
                    "marginBottom": 0,
                    "marginLeft": 0,
                    "marginRight": 0,
                    # Honour the @page rule injected by isolate_page_for_print.
                    "preferCSSPageSize": True,
                    "pageRanges": "1",
                    "transferMode": "ReturnAsBase64",
                },
            )
            return base64.b64decode(result["data"])
        except WebDriverException as error:
            last_error = error
            if attempt < PRINT_ATTEMPTS_PER_PAGE:
                print(f"    Print attempt {attempt} failed; retrying...")
                time.sleep(1)

    raise last_error


def export_single_page(driver, page_number, total_pages, spool_dir):
    """
    Export one Scribd page to a single-sheet PDF in the spool directory.

    Returns:
        Path to the spooled PDF, or None when the page could not be measured.
    """
    page_info = isolate_page_for_print(driver, page_number)

    if not page_info:
        print(f"  Skipping page {page_number}: element missing")
        return None

    width_px = int(page_info["width"])
    height_px = int(page_info["height"])

    if width_px <= 0 or height_px <= 0:
        print(
            f"  Skipping page {page_number}: "
            f"invalid geometry {width_px}x{height_px}"
        )
        return None

    width_inches = width_px / CSS_PIXELS_PER_INCH
    height_inches = height_px / CSS_PIXELS_PER_INCH

    print(
        f"  Page {page_number}/{total_pages} {width_px}x{height_px}px "
        f'-> {width_inches:.3f}"x{height_inches:.3f}"'
    )

    pdf_bytes = print_page_to_pdf_bytes(driver, width_inches, height_inches)

    sheet_count = len(PdfReader(BytesIO(pdf_bytes)).pages)
    if sheet_count != 1:
        raise RuntimeError(
            f"Document page {page_number} produced {sheet_count} PDF sheets; "
            "expected exactly 1."
        )

    page_path = os.path.join(spool_dir, f"page-{page_number:08d}.pdf")
    with open(page_path, "wb") as page_handle:
        page_handle.write(pdf_bytes)

    print("    OK: exactly 1 PDF sheet")
    return page_path


def merge_spooled_pages(page_files, output_path):
    """Combine the spooled single-page PDFs into the final document."""
    print(f"Merging {len(page_files)} disk-spooled PDF pages...")

    writer = PdfWriter()
    try:
        for page_path in page_files:
            writer.add_page(PdfReader(page_path).pages[0])

        with open(output_path, "wb") as output_handle:
            writer.write(output_handle)
    finally:
        writer.close()


def export_pages(driver, page_numbers, total_pages, output_path, settings):
    """Export the selected pages in bounded batches and merge them to disk."""
    configure_command_timeout(driver, settings.cdp_timeout_seconds)
    driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "print"})

    print(
        f"Exporting {len(page_numbers)} document pages in bounded batches "
        f"of {settings.export_batch_size}..."
    )

    page_files = []

    with tempfile.TemporaryDirectory(prefix="scribd-pdf-pages-") as spool_dir:
        for batch in chunked(page_numbers, settings.export_batch_size):
            print(f"  Loading page batch {batch[0]}-{batch[-1]}/{total_pages}...")
            load_page_batch(driver, batch, settings)

            try:
                for page_number in batch:
                    page_path = export_single_page(
                        driver,
                        page_number,
                        total_pages,
                        spool_dir,
                    )
                    if page_path:
                        page_files.append(page_path)
            finally:
                release_page_batch(driver, batch)

        if not page_files:
            raise RuntimeError("No valid document pages were exported.")

        merge_spooled_pages(page_files, output_path)

    return os.path.abspath(output_path)


def resolve_output_path(args, source_url, document_id):
    """
    Decide where the PDF goes and make sure writing there is safe.

    Raises:
        RuntimeError: If the target exists and --force was not supplied.
    """
    if args.output:
        output_path = os.path.abspath(os.path.expanduser(args.output))
        if os.path.isdir(output_path):
            output_path = os.path.join(
                output_path,
                default_output_filename(source_url, document_id),
            )
    else:
        output_path = os.path.abspath(
            default_output_filename(source_url, document_id)
        )

    if os.path.exists(output_path) and not args.force:
        raise RuntimeError(
            f"{output_path} already exists. Use --force to overwrite it, or "
            "pass a different path with --output."
        )

    parent_dir = os.path.dirname(output_path)
    if parent_dir and not os.path.isdir(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    return output_path


def build_argument_parser():
    """Build the command line interface."""
    parser = argparse.ArgumentParser(
        prog="scribd-downloader.py",
        description="Save a publicly viewable Scribd document as a PDF.",
        epilog=(
            "Only download documents you have the right to access. "
            "Environment variables SCRIBD_CDP_TIMEOUT, "
            "SCRIBD_PAGE_LOAD_TIMEOUT, SCRIBD_EXPORT_BATCH_SIZE and "
            "SCRIBD_HEADLESS still work as defaults; CLI flags win."
        ),
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="Scribd document URL or numeric document id (prompted if omitted)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output PDF path or existing directory (default: derived from URL)",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists",
    )
    parser.add_argument(
        "-p",
        "--pages",
        help='Pages to export, e.g. "1-20" or "1-5,12,40-60" (default: all)',
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Pages kept fully loaded in Chrome at once (default: 8)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="ChromeDriver command timeout in seconds for printing (default: 600)",
    )
    parser.add_argument(
        "--page-timeout",
        type=int,
        help="Per-batch page and image load timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show the browser window (useful when debugging rendering)",
    )
    return parser


def run(args):
    """Run one export. Returns a process exit code."""
    input_url = args.url or input("Input link Scribd: ").strip()
    document_id = extract_document_id(input_url)

    if not document_id:
        print("Error: Please provide a valid Scribd document URL or id.")
        print("Example: https://www.scribd.com/document/123456789/Document-Title")
        print("Example: https://www.scribd.com/doc/123456789/Document-Title")
        return EXIT_FAILURE

    embed_url = build_embed_url(document_id)
    settings = ExportSettings.from_args(args)

    try:
        output_path = resolve_output_path(args, input_url, document_id)
    except (RuntimeError, OSError) as error:
        print(f"Error: {error}")
        return EXIT_FAILURE

    print(f"Link embed: {embed_url}")
    print(f"Output file: {output_path}")

    with tempfile.TemporaryDirectory(
        prefix="scribd-chrome-profile-"
    ) as runtime_profile_dir:
        driver = None

        try:
            print("\nStarting Chrome browser...")
            driver = webdriver.Chrome(
                options=build_chrome_options(
                    runtime_profile_dir,
                    settings.headless,
                )
            )

            driver.get(embed_url)

            try:
                WebDriverWait(driver, DOCUMENT_READY_TIMEOUT_SECONDS).until(
                    expected_conditions.presence_of_element_located(
                        (By.CSS_SELECTOR, ".outer_page")
                    )
                )
            except TimeoutException:
                raise RuntimeError(
                    "No printable document pages were detected. The document "
                    "may be private, removed, or preview-only."
                ) from None

            hide_cookie_dialogs(driver)
            print("Cookie dialogs hidden.")

            total_pages = count_document_pages(driver)
            if total_pages <= 0:
                raise RuntimeError("No printable document pages were detected.")

            prepare_document_for_print(driver)
            inject_print_styles(driver)

            try:
                page_numbers = parse_page_selection(args.pages, total_pages)
            except ValueError as error:
                print(f"Error: {error}")
                return EXIT_FAILURE

            print(f"\nSaving PDF as: {output_path}")
            print("  Export mode: Individual document pages")
            print("  Margins: None")
            print("  Headers/Footers: Disabled")
            print(f"  Document pages: {total_pages}")
            print(f"  Selected pages: {len(page_numbers)}")
            print(
                "  ChromeDriver command timeout: "
                f"{settings.cdp_timeout_seconds}s"
            )

            driver.execute_script("window.scrollTo(0, 0)")

            started_at = time.monotonic()
            saved_path = export_pages(
                driver,
                page_numbers,
                total_pages,
                output_path,
                settings,
            )
            elapsed = time.monotonic() - started_at

            size_mb = os.path.getsize(saved_path) / (1024 * 1024)
            print(f"PDF saved successfully to: {saved_path}")
            print(f"  {size_mb:.2f} MB in {elapsed:.1f}s")
            return EXIT_OK

        except (RuntimeError, OSError, WebDriverException) as error:
            print(f"Export failed: {error}")
            return EXIT_FAILURE

        finally:
            if driver is not None:
                try:
                    driver.quit()
                    print("Browser closed.")
                except WebDriverException:
                    pass


def main(argv=None):
    """Parse arguments and run the exporter."""
    args = build_argument_parser().parse_args(argv)

    try:
        return run(args)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
