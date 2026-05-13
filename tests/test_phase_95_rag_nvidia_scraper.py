"""Phase 95 — RAG NVIDIA scraper tests.

Gate: pytest — scraper class enumerates URLs, dedupes, chunks page text,
produces RAG documents.

14 tests covering:
  - metadata contract
  - NVIDIA_SOURCES catalogue
  - URLFrontier FIFO ordering
  - URLFrontier seen / mark_seen
  - NVIDIAScraper.is_allowed (True / False)
  - extract_text_from_html
  - extract_title_from_html
  - extract_links_from_html
  - chunk_text: multiple chunks on long text
  - chunk_text: single chunk on short text
  - scrape: returns PageRecord list from mocked HTML
  - scrape: respects max_pages cap
  - dedupe_pages: removes duplicate content_hash entries
  - to_rag_chunks: correct chunk structure from records
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Shared import helper
# ---------------------------------------------------------------------------


def _imports():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIA_SOURCES,
        MockHTTPClient,
        NVIDIAScraper,
        PageRecord,
        RAGChunk,
        ScraperSource,
        URLFrontier,
        dedupe_pages,
        get_phase_metadata,
    )
    return (
        NVIDIA_SOURCES,
        MockHTTPClient,
        NVIDIAScraper,
        PageRecord,
        RAGChunk,
        ScraperSource,
        URLFrontier,
        dedupe_pages,
        get_phase_metadata,
    )


# ---------------------------------------------------------------------------
# 1. Metadata contract
# ---------------------------------------------------------------------------


def test_phase_95_metadata():
    *_, get_phase_metadata = _imports()
    md = get_phase_metadata()
    assert md["phase"] == 95
    assert md["status"] == "landed"
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. NVIDIA_SOURCES catalogue has ≥5 entries with valid base_url
# ---------------------------------------------------------------------------


def test_nvidia_sources_count_and_base_url():
    (NVIDIA_SOURCES, *_) = _imports()
    assert len(NVIDIA_SOURCES) >= 5
    for src in NVIDIA_SOURCES:
        assert src.base_url.startswith("http"), (
            f"{src.name} base_url does not start with http: {src.base_url!r}"
        )
        assert src.name  # non-empty name


# ---------------------------------------------------------------------------
# 3. URLFrontier — add/pop FIFO ordering
# ---------------------------------------------------------------------------


def test_url_frontier_fifo_ordering():
    (_, _, _, _, _, _, URLFrontier, *_) = _imports()

    frontier = URLFrontier(max_depth=5)
    urls = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    for u in urls:
        frontier.add(u, depth=0)

    popped = []
    while True:
        item = frontier.pop()
        if item is None:
            break
        popped.append(item[0])

    assert popped == urls  # FIFO order preserved


# ---------------------------------------------------------------------------
# 4. URLFrontier — seen / mark_seen
# ---------------------------------------------------------------------------


def test_url_frontier_seen_mark_seen():
    (_, _, _, _, _, _, URLFrontier, *_) = _imports()

    frontier = URLFrontier()
    url = "https://example.com/page"

    assert not frontier.seen(url)
    frontier.mark_seen(url)
    assert frontier.seen(url)

    # Adding a seen URL does NOT enqueue it again
    frontier.add(url, depth=0)
    assert frontier.pending_count() == 0


# ---------------------------------------------------------------------------
# 5. URLFrontier — max_depth prevents deep enqueuing
# ---------------------------------------------------------------------------


def test_url_frontier_max_depth():
    (_, _, _, _, _, _, URLFrontier, *_) = _imports()

    frontier = URLFrontier(max_depth=2)
    frontier.add("https://example.com/ok", depth=2)   # should enqueue
    frontier.add("https://example.com/deep", depth=3)  # should be dropped

    assert frontier.pending_count() == 1


# ---------------------------------------------------------------------------
# 6. is_allowed — True for same host + matching allowed_path
# ---------------------------------------------------------------------------


def test_is_allowed_true_for_allowed_path():
    (_, _, NVIDIAScraper, _, _, ScraperSource, *_) = _imports()

    source = ScraperSource(
        name="test_src",
        base_url="https://docs.example.com/docs/latest",
        allowed_paths=["/docs/latest"],
    )
    scraper = NVIDIAScraper(source)

    assert scraper.is_allowed("https://docs.example.com/docs/latest/section/page")
    assert scraper.is_allowed("https://docs.example.com/docs/latest")


# ---------------------------------------------------------------------------
# 7. is_allowed — False for foreign domain
# ---------------------------------------------------------------------------


def test_is_allowed_false_for_foreign_domain():
    (_, _, NVIDIAScraper, _, _, ScraperSource, *_) = _imports()

    source = ScraperSource(
        name="test_src",
        base_url="https://docs.example.com/docs/latest",
        allowed_paths=["/docs/latest"],
    )
    scraper = NVIDIAScraper(source)

    assert not scraper.is_allowed("https://evil.example.com/docs/latest/page")
    assert not scraper.is_allowed("https://other.com/totally/different")


# ---------------------------------------------------------------------------
# 8. extract_text_from_html strips tags and collapses whitespace
# ---------------------------------------------------------------------------


def test_extract_text_strips_tags():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIA_SOURCES as _S,
        NVIDIAScraper,
    )
    scraper = NVIDIAScraper(_S[0])

    result = scraper.extract_text_from_html("<p>Hello <b>world</b></p>")
    assert result == "Hello world"


# ---------------------------------------------------------------------------
# 9. extract_title_from_html pulls <title> tag
# ---------------------------------------------------------------------------


def test_extract_title_from_html():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIA_SOURCES as _S,
        NVIDIAScraper,
    )
    scraper = NVIDIAScraper(_S[0])

    html = "<html><head><title>Foo Bar</title></head><body></body></html>"
    assert scraper.extract_title_from_html(html) == "Foo Bar"

    # Missing title returns empty string
    assert scraper.extract_title_from_html("<html><body>no title</body></html>") == ""


# ---------------------------------------------------------------------------
# 10. extract_links_from_html returns href list
# ---------------------------------------------------------------------------


def test_extract_links_from_html():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIA_SOURCES as _S,
        NVIDIAScraper,
    )
    scraper = NVIDIAScraper(_S[0])

    html = (
        '<a href="/page1">p1</a>'
        '<a href="/page2">p2</a>'
        '<a href="https://external.com/x">ext</a>'
        '<a href="#anchor">skip</a>'
    )
    base = "https://docs.example.com"
    links = scraper.extract_links_from_html(html, base)

    assert "https://docs.example.com/page1" in links
    assert "https://docs.example.com/page2" in links
    assert "https://external.com/x" in links
    # Fragment-only href is excluded
    fragment_urls = [l for l in links if "#anchor" in l and l.endswith("#anchor")]
    assert not fragment_urls


# ---------------------------------------------------------------------------
# 11. chunk_text on long text returns multiple chunks ≤ chunk_size + buffer
# ---------------------------------------------------------------------------


def test_chunk_text_long_text_multiple_chunks():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIAScraper,
        NVIDIA_SOURCES as _S,
    )
    scraper = NVIDIAScraper(_S[0], chunk_size_chars=50)

    # 200 chars of text; chunk_size=50, so should produce ≥3 chunks
    long_text = " ".join(["word"] * 50)  # "word word word ..." ~249 chars
    chunks = scraper.chunk_text(long_text, source="test", url="https://x.com/p")

    assert len(chunks) > 1
    # Each chunk's size must not exceed chunk_size + one word overhead
    for chunk in chunks:
        assert chunk.chunk_size <= 60, f"chunk too large: {chunk.chunk_size}"
    # chunk_idx is sequential
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_idx == i


# ---------------------------------------------------------------------------
# 12. chunk_text on short text returns exactly 1 chunk
# ---------------------------------------------------------------------------


def test_chunk_text_short_text_single_chunk():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIAScraper,
        NVIDIA_SOURCES as _S,
    )
    scraper = NVIDIAScraper(_S[0], chunk_size_chars=800)

    short = "Short text."
    chunks = scraper.chunk_text(short, source="test", url="https://x.com/p")

    assert len(chunks) == 1
    assert chunks[0].text == "Short text."
    assert chunks[0].chunk_idx == 0


# ---------------------------------------------------------------------------
# 13. scrape with mocked HTML returns PageRecord list
# ---------------------------------------------------------------------------


def test_scrape_returns_page_records():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIAScraper,
        MockHTTPClient,
        ScraperSource,
    )

    base = "https://docs.example.com/docs"
    source = ScraperSource(
        name="mock_src",
        base_url=base,
        allowed_paths=["/docs"],
        crawl_depth=1,
    )
    html_root = (
        "<html><head><title>Root</title></head>"
        "<body><p>Welcome to docs.</p>"
        f'<a href="{base}/page1">page1</a>'
        "</body></html>"
    )
    html_page1 = (
        "<html><head><title>Page 1</title></head>"
        "<body><p>Content of page one.</p></body></html>"
    )

    http = MockHTTPClient(
        pages={
            base: html_root,
            f"{base}/page1": html_page1,
        }
    )
    scraper = NVIDIAScraper(source, http=http)
    records = scraper.scrape(max_pages=10)

    assert len(records) >= 1
    urls = [r.url for r in records]
    assert base in urls

    # Each record has required fields
    for r in records:
        assert r.url
        assert r.scraped_at
        assert len(r.content_hash) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# 14. scrape respects max_pages cap
# ---------------------------------------------------------------------------


def test_scrape_respects_max_pages():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIAScraper,
        MockHTTPClient,
        ScraperSource,
    )

    base = "https://docs.example.com/deep"
    pages = {
        f"{base}": f"<html><head><title>R</title></head><body>"
        + "".join(f'<a href="{base}/p{i}">p{i}</a>' for i in range(30))
        + "</body></html>",
    }
    # Add 30 child pages
    for i in range(30):
        pages[f"{base}/p{i}"] = (
            f"<html><head><title>P{i}</title></head>"
            f"<body>Content {i}.</body></html>"
        )

    source = ScraperSource(
        name="deep_src",
        base_url=base,
        allowed_paths=["/deep"],
        crawl_depth=2,
    )
    http = MockHTTPClient(pages=pages)
    scraper = NVIDIAScraper(source, http=http)

    records = scraper.scrape(max_pages=5)
    assert len(records) <= 5


# ---------------------------------------------------------------------------
# 15. dedupe_pages removes duplicate content_hash entries
# ---------------------------------------------------------------------------


def test_dedupe_pages_removes_duplicates():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        PageRecord,
        dedupe_pages,
    )
    import hashlib

    def _make_record(url: str, text: str) -> PageRecord:
        return PageRecord(
            url=url,
            title="T",
            body_text=text,
            scraped_at="2026-01-01T00:00:00+00:00",
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
        )

    r1 = _make_record("https://a.com/1", "unique text A")
    r2 = _make_record("https://a.com/2", "unique text B")
    r3 = _make_record("https://a.com/3", "unique text A")  # same body as r1

    deduped = dedupe_pages([r1, r2, r3])
    assert len(deduped) == 2
    assert deduped[0].url == r1.url
    assert deduped[1].url == r2.url


# ---------------------------------------------------------------------------
# 16. to_rag_chunks produces correct chunk structure
# ---------------------------------------------------------------------------


def test_to_rag_chunks_structure():
    from service.isaac_assist_service.multimodal.rag_nvidia_scraper import (
        NVIDIAScraper,
        MockHTTPClient,
        ScraperSource,
        PageRecord,
    )
    import hashlib

    source = ScraperSource(
        name="chunk_test",
        base_url="https://docs.example.com/c",
        allowed_paths=["/c"],
    )
    scraper = NVIDIAScraper(source, chunk_size_chars=50)

    body = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi."
    record = PageRecord(
        url="https://docs.example.com/c/page",
        title="T",
        body_text=body,
        scraped_at="2026-01-01T00:00:00+00:00",
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
    )
    chunks = scraper.to_rag_chunks([record])

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.source == "chunk_test"
        assert chunk.url == "https://docs.example.com/c/page"
        assert chunk.chunk_size == len(chunk.text)
        assert chunk.embedding is None
