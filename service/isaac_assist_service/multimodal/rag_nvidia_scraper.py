"""Phase 95 — RAG: real NVIDIA docs scraping.

Provides the scraper structure, URL frontier, page parser interface, chunking
strategy, dedup logic, and mock HTTP layer needed for offline testing.  Actual
network access is handled by the live `NVIDIAScraper` via dependency injection
of a real HTTP client; tests use `MockHTTPClient`.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 95.
"""
from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 95
PHASE_TITLE = "RAG: real NVIDIA docs scraping"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 95",
    }


# ---------------------------------------------------------------------------
# Source catalogue
# ---------------------------------------------------------------------------


@dataclass
class ScraperSource:
    """Configuration for a single documentation source to scrape.

    Attributes:
        name:          Logical identifier (used as the ``source`` field in
                       :class:`RAGChunk`).
        base_url:      Root URL of the documentation site.
        allowed_paths: URL path prefixes that are in-scope for crawling.
                       Paths are matched as prefixes, e.g. ``"/isaacsim"``
                       matches ``"/isaacsim/latest/foo"``.
        crawl_depth:   Maximum link-following depth from ``base_url``.
        respect_robots: Whether the crawler should honour ``robots.txt``
                        (enforced externally; flag is advisory).
    """

    name: str
    base_url: str
    allowed_paths: List[str]
    crawl_depth: int = 3
    respect_robots: bool = True


#: Canonical NVIDIA documentation sources for Isaac Sim / Isaac Lab / GR00T /
#: cuRobo and OmniGraph.
NVIDIA_SOURCES: List[ScraperSource] = [
    ScraperSource(
        name="isaac_sim_docs",
        base_url="https://docs.omniverse.nvidia.com/isaacsim/latest",
        allowed_paths=["/isaacsim/latest"],
    ),
    ScraperSource(
        name="isaac_lab_docs",
        base_url="https://isaac-sim.github.io/IsaacLab",
        allowed_paths=["/IsaacLab"],
    ),
    ScraperSource(
        name="groot_n1_docs",
        base_url="https://nvidia.github.io/Isaac-GR00T",
        allowed_paths=["/Isaac-GR00T"],
    ),
    ScraperSource(
        name="curobo_docs",
        base_url="https://curobo.org/",
        allowed_paths=["/"],
    ),
    ScraperSource(
        name="omnigraph_docs",
        base_url="https://docs.omniverse.nvidia.com/extensions/latest/ext_omnigraph",
        allowed_paths=["/extensions/latest/ext_omnigraph"],
    ),
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class PageRecord:
    """Scraped page data before chunking.

    Attributes:
        url:           Canonical URL of the page.
        title:         Document title extracted from ``<title>`` tag.
        body_text:     Plain text extracted from the HTML body.
        scraped_at:    ISO-8601 UTC timestamp of when the page was fetched.
        content_hash:  SHA-256 hex digest of ``body_text``, used for dedup.
    """

    url: str
    title: str
    body_text: str
    scraped_at: str
    content_hash: str


@dataclass
class RAGChunk:
    """A text chunk ready for embedding and retrieval.

    Attributes:
        source:     Name of the :class:`ScraperSource` that produced this chunk.
        url:        Page URL the chunk originates from.
        chunk_idx:  Zero-based index of this chunk within the page.
        text:       Chunk text content.
        chunk_size: Actual character length of ``text``.
        embedding:  Optional pre-computed embedding vector.
    """

    source: str
    url: str
    chunk_idx: int
    text: str
    chunk_size: int
    embedding: Optional[List[float]] = None


# ---------------------------------------------------------------------------
# URL frontier
# ---------------------------------------------------------------------------


class URLFrontier:
    """FIFO work-queue for BFS-style web crawling with depth tracking.

    Tracks seen URLs to avoid revisiting and provides a simple add/pop
    interface.

    Args:
        max_depth: URLs enqueued beyond this depth are silently dropped.
    """

    def __init__(self, max_depth: int = 3) -> None:
        self._max_depth = max_depth
        self._queue: deque[Tuple[str, int]] = deque()
        self._seen: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, url: str, depth: int = 0) -> None:
        """Enqueue *url* at *depth* if not already seen and within max depth."""
        if depth > self._max_depth:
            return
        if url in self._seen:
            return
        self._queue.append((url, depth))

    def pop(self) -> Optional[Tuple[str, int]]:
        """Dequeue and return the next ``(url, depth)`` pair, or ``None``."""
        if self._queue:
            return self._queue.popleft()
        return None

    def seen(self, url: str) -> bool:
        """Return ``True`` if *url* has been marked seen."""
        return url in self._seen

    def mark_seen(self, url: str) -> None:
        """Mark *url* as visited so it won't be enqueued again."""
        self._seen.add(url)

    def pending_count(self) -> int:
        """Return the number of URLs currently in the queue."""
        return len(self._queue)


# ---------------------------------------------------------------------------
# Mock HTTP client
# ---------------------------------------------------------------------------


class MockHTTPClient:
    """Deterministic HTTP client for testing without network access.

    Args:
        pages: Mapping of URL → HTML body string.  URLs not present in the
               mapping return a 404 response.
    """

    def __init__(self, pages: Optional[Dict[str, str]] = None) -> None:
        self._pages: Dict[str, str] = pages or {}

    def get(self, url: str) -> Tuple[int, str]:
        """Return ``(status_code, body)`` for *url*.

        Returns ``(200, html_body)`` when the URL is known, otherwise
        ``(404, "")``.
        """
        if url in self._pages:
            return 200, self._pages[url]
        return 404, ""


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------


class NVIDIAScraper:
    """BFS crawler that converts NVIDIA documentation pages into
    :class:`PageRecord` objects.

    Designed for testability: pass a :class:`MockHTTPClient` to avoid real
    HTTP calls.  In production, replace ``http`` with a real HTTP client that
    exposes the same ``.get(url)`` interface.

    Args:
        source:           Which documentation source to crawl.
        http:             HTTP client; defaults to ``MockHTTPClient()`` (empty
                          pages dict — useful for testing the empty-frontier
                          path).
        chunk_size_chars: Target character length for text chunks.
    """

    # Regex patterns compiled once at class level
    _RE_TAG = re.compile(r"<[^>]+>")
    _RE_WS = re.compile(r"\s+")
    _RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
    _RE_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)

    def __init__(
        self,
        source: ScraperSource,
        http: Optional[MockHTTPClient] = None,
        chunk_size_chars: int = 800,
    ) -> None:
        self._source = source
        self._http = http if http is not None else MockHTTPClient()
        self._chunk_size = chunk_size_chars

    # ------------------------------------------------------------------
    # URL filtering
    # ------------------------------------------------------------------

    def is_allowed(self, url: str) -> bool:
        """Return ``True`` when *url* is within the source's allowed scope.

        A URL is allowed when:
        1. Its host matches the base_url host.
        2. Its path starts with at least one of ``source.allowed_paths``.
        """
        try:
            parsed = urlparse(url)
            base_parsed = urlparse(self._source.base_url)
        except ValueError:
            return False

        if parsed.netloc != base_parsed.netloc:
            return False

        for allowed in self._source.allowed_paths:
            if parsed.path.startswith(allowed):
                return True
        return False

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    def extract_text_from_html(self, html: str) -> str:
        """Strip HTML tags and collapse whitespace, returning plain text."""
        text = self._RE_TAG.sub(" ", html)
        text = self._RE_WS.sub(" ", text)
        return text.strip()

    def extract_title_from_html(self, html: str) -> str:
        """Extract the contents of the ``<title>`` tag, or return ``""``."""
        m = self._RE_TITLE.search(html)
        if m:
            return self._RE_WS.sub(" ", m.group(1)).strip()
        return ""

    def extract_links_from_html(self, html: str, base_url: str) -> List[str]:
        """Return absolute URLs found in ``href`` attributes.

        Relative URLs are resolved against *base_url*.  Fragment-only
        references (``#...``) are skipped.
        """
        links: List[str] = []
        for href in self._RE_HREF.findall(html):
            href = href.strip()
            if not href or href.startswith("#"):
                continue
            absolute = urljoin(base_url, href)
            # Drop fragment to avoid double-visiting the same page
            absolute = absolute.split("#")[0]
            links.append(absolute)
        return links

    # ------------------------------------------------------------------
    # Text chunking
    # ------------------------------------------------------------------

    def chunk_text(self, text: str, source: str, url: str) -> List[RAGChunk]:
        """Split *text* into :class:`RAGChunk` objects of at most
        ``chunk_size_chars`` characters.

        Splits are made at word boundaries (spaces) to avoid cutting words in
        half.  The last chunk may be shorter than the target size.  Returns a
        single chunk when *text* is shorter than the target.

        Args:
            text:   Plain text to chunk.
            source: Source name written into each chunk.
            url:    Page URL written into each chunk.

        Returns:
            Non-empty list of :class:`RAGChunk` objects.
        """
        if not text:
            return [RAGChunk(source=source, url=url, chunk_idx=0, text="", chunk_size=0)]

        words = text.split(" ")
        chunks: List[RAGChunk] = []
        current_parts: List[str] = []
        current_len = 0

        for word in words:
            # +1 for the space separator (except the first word)
            word_cost = len(word) + (1 if current_parts else 0)

            if current_parts and current_len + word_cost > self._chunk_size:
                # Flush current chunk
                chunk_text = " ".join(current_parts)
                chunks.append(
                    RAGChunk(
                        source=source,
                        url=url,
                        chunk_idx=len(chunks),
                        text=chunk_text,
                        chunk_size=len(chunk_text),
                    )
                )
                current_parts = [word]
                current_len = len(word)
            else:
                current_parts.append(word)
                current_len += word_cost

        # Flush remainder
        if current_parts:
            chunk_text = " ".join(current_parts)
            chunks.append(
                RAGChunk(
                    source=source,
                    url=url,
                    chunk_idx=len(chunks),
                    text=chunk_text,
                    chunk_size=len(chunk_text),
                )
            )

        return chunks

    # ------------------------------------------------------------------
    # Crawl
    # ------------------------------------------------------------------

    def scrape(self, max_pages: int = 20) -> List[PageRecord]:
        """BFS crawl of the source, returning up to *max_pages* records.

        Uses :class:`URLFrontier` internally.  Fetches pages via the injected
        HTTP client; skips 4xx/5xx responses silently.

        Args:
            max_pages: Hard cap on the number of pages fetched.  Prevents
                       runaway crawls during testing.

        Returns:
            List of :class:`PageRecord` objects, one per successfully fetched
            page.
        """
        frontier = URLFrontier(max_depth=self._source.crawl_depth)
        frontier.add(self._source.base_url, depth=0)

        records: List[PageRecord] = []

        while len(records) < max_pages:
            item = frontier.pop()
            if item is None:
                break
            url, depth = item

            if frontier.seen(url):
                continue
            frontier.mark_seen(url)

            if not self.is_allowed(url):
                continue

            status, body = self._http.get(url)
            if status != 200:
                continue

            title = self.extract_title_from_html(body)
            body_text = self.extract_text_from_html(body)
            content_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
            scraped_at = datetime.now(timezone.utc).isoformat()

            records.append(
                PageRecord(
                    url=url,
                    title=title,
                    body_text=body_text,
                    scraped_at=scraped_at,
                    content_hash=content_hash,
                )
            )

            # Enqueue child links
            if depth < self._source.crawl_depth:
                for link in self.extract_links_from_html(body, url):
                    if not frontier.seen(link):
                        frontier.add(link, depth=depth + 1)

        return records

    # ------------------------------------------------------------------
    # Chunk conversion
    # ------------------------------------------------------------------

    def to_rag_chunks(self, records: List[PageRecord]) -> List[RAGChunk]:
        """Convert a list of :class:`PageRecord` objects to :class:`RAGChunk`.

        Args:
            records: Pages to chunk.

        Returns:
            Flat list of all chunks from all records, in order.
        """
        chunks: List[RAGChunk] = []
        for record in records:
            page_chunks = self.chunk_text(
                record.body_text,
                source=self._source.name,
                url=record.url,
            )
            # Re-index chunk_idx to be page-local (already is from chunk_text)
            chunks.extend(page_chunks)
        return chunks


# ---------------------------------------------------------------------------
# Deduplication utility
# ---------------------------------------------------------------------------


def dedupe_pages(pages: List[PageRecord]) -> List[PageRecord]:
    """Return *pages* with duplicate content removed.

    Two pages are considered duplicates when they share the same
    ``content_hash``.  The first occurrence of each hash is retained; later
    duplicates are dropped.  Input order is preserved.

    Args:
        pages: List of :class:`PageRecord` objects (may contain duplicates).

    Returns:
        Deduplicated list.
    """
    seen_hashes: set[str] = set()
    result: List[PageRecord] = []
    for page in pages:
        if page.content_hash not in seen_hashes:
            seen_hashes.add(page.content_hash)
            result.append(page)
    return result
