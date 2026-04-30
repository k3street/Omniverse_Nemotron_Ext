#!/usr/bin/env python3
"""
scrape_isaac_docs.py — crawl NVIDIA Isaac Sim docs and load them into the FTS index.

Usage:
    python scripts/scrape_isaac_docs.py                        # both versions
    python scripts/scrape_isaac_docs.py --version 5.1.0        # one version
    python scripts/scrape_isaac_docs.py --dry-run              # count URLs, don't fetch
    python scripts/scrape_isaac_docs.py --limit 50             # cap pages per version
    python scripts/scrape_isaac_docs.py --delay 0.5            # seconds between requests
    python scripts/scrape_isaac_docs.py --reset                # delete + re-index

Crawl strategy:
  1. Fetch sitemap.xml for each configured doc root.
  2. Extract page URLs.
  3. For each URL: fetch HTML, extract main content, pass to DocumentIndexer.
  4. DocumentIndexer chunks by paragraph (≥50 chars) and inserts into FTS5.
"""
import sys
import os
import time
import urllib.request
import urllib.error
import urllib.parse
import argparse
import logging
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import List, Tuple, Optional

# Ensure service package is importable from repo root
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from service.isaac_assist_service.retrieval.indexer import DocumentIndexer
from service.isaac_assist_service.retrieval.storage.fts_store import FTSStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Sources ──────────────────────────────────────────────────────────────────

SOURCES = [
    {
        "source_id": "nvidia_isaac_sim_5_1",
        "version": "5.1.0",
        "base_url": "https://docs.isaacsim.omniverse.nvidia.com/5.1.0/",
        "sitemap_url": "https://docs.isaacsim.omniverse.nvidia.com/5.1.0/sitemap.xml",
    },
    {
        "source_id": "nvidia_isaac_sim_6_0",
        "version": "6.0.0",
        "base_url": "https://docs.isaacsim.omniverse.nvidia.com/6.0.0/",
        "sitemap_url": "https://docs.isaacsim.omniverse.nvidia.com/6.0.0/sitemap.xml",
    },
]

_USER_AGENT = (
    "Mozilla/5.0 (compatible; IsaacAssistScraper/1.0; "
    "+https://www.10things.tech)"
)

# ── HTML → text extraction ───────────────────────────────────────────────────

# Tags whose content we extract
_CONTENT_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "dt", "dd", "td", "th",
    "pre", "code",
}
# Tags whose subtrees we skip entirely (navigation, boilerplate)
_SKIP_TAGS = {
    "nav", "header", "footer", "script", "style",
    "noscript", "aside", "form", "button",
}


class _ContentExtractor(HTMLParser):
    """Extract human-readable text from a doc page, skipping boilerplate."""

    def __init__(self):
        super().__init__()
        self._skip_depth = 0       # how deep we are inside a _SKIP_TAG subtree
        self._in_content = False
        self._chunks: List[str] = []
        self._buf: List[str] = []
        self._current_heading: str = "General"

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _CONTENT_TAGS:
            self._in_content = True
            if tag in ("h1", "h2", "h3"):
                # Flush previous buffer before a new section heading
                self._flush()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in _CONTENT_TAGS:
            self._flush()
            self._in_content = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_content:
            text = data.strip()
            if text:
                self._buf.append(text)

    def _flush(self):
        if self._buf:
            chunk = " ".join(self._buf).strip()
            if len(chunk) >= 30:
                self._chunks.append(chunk)
        self._buf = []

    def get_chunks(self) -> List[str]:
        self._flush()
        return self._chunks


def _extract_text_chunks(html: str) -> List[str]:
    parser = _ContentExtractor()
    parser.feed(html)
    return parser.get_chunks()


def _fetch(url: str, timeout: int = 20) -> Optional[str]:
    """Fetch URL, return decoded body or None on error."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.getheader("Content-Type", "")
            charset = "utf-8"
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].split(";")[0].strip()
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        logger.warning(f"HTTP {e.code} fetching {url}")
    except urllib.error.URLError as e:
        logger.warning(f"URL error fetching {url}: {e.reason}")
    except Exception as e:
        logger.warning(f"Error fetching {url}: {e}")
    return None


# ── Sitemap parsing ──────────────────────────────────────────────────────────

def _parse_sitemap(xml_text: str) -> List[str]:
    """Extract <loc> URLs from a sitemap.xml (or sitemap index)."""
    urls: List[str] = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        # Handle sitemap index (<sitemap><loc>...)
        for elem in root.findall("sm:sitemap/sm:loc", ns):
            urls.append(elem.text.strip())
        # Handle direct urlset (<url><loc>...)
        for elem in root.findall("sm:url/sm:loc", ns):
            urls.append(elem.text.strip())
    except ET.ParseError as e:
        logger.warning(f"Failed to parse sitemap XML: {e}")
    return urls


def _collect_page_urls(sitemap_url: str, base_url: str,
                       delay: float = 0.5) -> List[str]:
    """
    Walk the sitemap (following sitemap-index → child sitemaps → page URLs).
    Returns only HTML page URLs belonging to base_url.
    """
    logger.info(f"Fetching sitemap: {sitemap_url}")
    xml = _fetch(sitemap_url)
    if not xml:
        logger.warning("Failed to fetch sitemap; falling back to index page crawl.")
        return [base_url]

    first_urls = _parse_sitemap(xml)

    # Detect sitemap index (URLs point to other sitemaps, not pages)
    page_urls: List[str] = []
    for u in first_urls:
        if u.endswith(".xml"):
            # Child sitemap — fetch and parse
            time.sleep(delay)
            child_xml = _fetch(u)
            if child_xml:
                page_urls.extend(_parse_sitemap(child_xml))
        else:
            page_urls.append(u)

    # Filter to only pages under our base_url (no external links)
    page_urls = [
        u for u in page_urls
        if u.startswith(base_url) and u.endswith(".html")
    ]
    logger.info(f"Found {len(page_urls)} page URLs under {base_url}")
    return page_urls


# ── Main crawl ───────────────────────────────────────────────────────────────

def crawl_source(
    source: dict,
    indexer: DocumentIndexer,
    store: FTSStore,
    limit: int = 0,
    delay: float = 0.5,
    dry_run: bool = False,
    reset: bool = False,
) -> Tuple[int, int]:
    """
    Crawl one source, index pages into FTS.
    Returns (pages_crawled, chunks_indexed).
    """
    source_id = source["source_id"]
    version = source["version"]
    base_url = source["base_url"]
    sitemap_url = source["sitemap_url"]

    if reset:
        deleted = store.delete_source(source_id)
        logger.info(f"[{source_id}] Reset: deleted {deleted} existing chunks.")

    page_urls = _collect_page_urls(sitemap_url, base_url, delay=delay)

    if limit > 0:
        page_urls = page_urls[:limit]
        logger.info(f"[{source_id}] Limiting to {limit} pages.")

    if dry_run:
        logger.info(f"[{source_id}] Dry run — would crawl {len(page_urls)} pages.")
        return 0, 0

    pages_crawled = 0
    chunks_total = 0
    for i, url in enumerate(page_urls, 1):
        logger.info(f"[{source_id}] [{i}/{len(page_urls)}] {url}")
        html = _fetch(url)
        if html is None:
            continue

        text_chunks = _extract_text_chunks(html)
        raw_text = "\n\n".join(text_chunks)
        if len(raw_text) < 100:
            logger.debug(f"  Skipping thin page ({len(raw_text)} chars)")
            continue

        n = indexer.index_document(
            source_id=source_id,
            raw_text=raw_text,
            url=url,
            version=version,
        )
        chunks_total += n
        pages_crawled += 1
        time.sleep(delay)

    logger.info(
        f"[{source_id}] Done: {pages_crawled} pages, {chunks_total} chunks indexed."
    )
    return pages_crawled, chunks_total


def main():
    parser = argparse.ArgumentParser(description="Crawl Isaac Sim docs into FTS index.")
    parser.add_argument("--version", choices=["5.1.0", "6.0.0"],
                        help="Crawl only this version (default: both)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max pages per version (0 = unlimited)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between requests (default: 0.5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count URLs without fetching or indexing")
    parser.add_argument("--reset", action="store_true",
                        help="Delete existing FTS entries before re-indexing")
    args = parser.parse_args()

    indexer = DocumentIndexer()
    store = FTSStore()

    sources = SOURCES
    if args.version:
        sources = [s for s in SOURCES if s["version"] == args.version]

    total_pages = 0
    total_chunks = 0
    for source in sources:
        pages, chunks = crawl_source(
            source=source,
            indexer=indexer,
            store=store,
            limit=args.limit,
            delay=args.delay,
            dry_run=args.dry_run,
            reset=args.reset,
        )
        total_pages += pages
        total_chunks += chunks

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Crawl complete: "
          f"{total_pages} pages, {total_chunks} FTS chunks.")


if __name__ == "__main__":
    main()

