"""
L0 unit tests for:
  - FTSStore (insert, search, count_chunks, delete_source)
  - DocumentIndexer (index_document)
  - HTML content extraction in scrape_isaac_docs.py
"""
import os
import sys
import sqlite3
import pytest

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO)


# ---------------------------------------------------------------------------
# Fixtures — in-memory / tmp-path FTS store so tests don't touch the real DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db_path(tmp_path):
    return str(tmp_path / "test_rag.db")


@pytest.fixture()
def fts_store(tmp_db_path, monkeypatch):
    """FTSStore backed by a temp SQLite file."""
    import service.isaac_assist_service.retrieval.storage.fts_store as _mod

    monkeypatch.setattr(_mod, "DB_PATH", tmp_db_path)
    from service.isaac_assist_service.retrieval.storage.fts_store import FTSStore

    store = FTSStore()
    yield store
    store.conn.close()


@pytest.fixture()
def indexer(tmp_db_path, monkeypatch):
    """DocumentIndexer wired to the same tmp DB."""
    import service.isaac_assist_service.retrieval.storage.fts_store as _mod

    monkeypatch.setattr(_mod, "DB_PATH", tmp_db_path)
    from service.isaac_assist_service.retrieval.indexer import DocumentIndexer

    return DocumentIndexer()


# ---------------------------------------------------------------------------
# FTSStore tests
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestFTSStoreInsertAndSearch:
    def test_insert_and_count(self, fts_store):
        fts_store.insert_chunk(
            source_id="test_src",
            content="PhysX rigid body simulation in Isaac Sim",
            section_path="Physics",
            url="https://example.com/physics",
            version_scope="5.1.0",
            trust_tier=1,
        )
        assert fts_store.count_chunks() == 1

    def test_count_by_source(self, fts_store):
        fts_store.insert_chunk("src_a", "RigidBodyAPI documentation", "API", "http://a", "5.1.0", 1)
        fts_store.insert_chunk("src_b", "CollisionAPI documentation", "API", "http://b", "5.1.0", 1)
        assert fts_store.count_chunks("src_a") == 1
        assert fts_store.count_chunks("src_b") == 1
        assert fts_store.count_chunks() == 2

    def test_search_returns_match(self, fts_store):
        fts_store.insert_chunk(
            "docs", "RigidBodyAPI enables physics simulation",
            "API", "http://docs/rigid", "5.1.0", 1,
        )
        results = fts_store.search("RigidBodyAPI", limit=5)
        assert len(results) >= 1
        assert any("RigidBodyAPI" in r["content"] for r in results)

    def test_search_no_match_returns_empty(self, fts_store):
        fts_store.insert_chunk("docs", "Only about cameras", "Sensor", "http://x", "5.1.0", 1)
        results = fts_store.search("nonexistenttoken12345xyz")
        assert results == []

    def test_search_version_scoped(self, fts_store):
        fts_store.insert_chunk("docs", "articulation joints tutorial", "Motion", "http://51", "5.1.0", 1)
        fts_store.insert_chunk("docs", "articulation joints tutorial", "Motion", "http://60", "6.0.0", 1)

        r51 = fts_store.search("articulation", limit=10, version_scope="5.1.0")
        r60 = fts_store.search("articulation", limit=10, version_scope="6.0.0")
        # Both should return something, but the version_scope filter must not mix them up
        assert all(r["version_scope"] in ("5.1.0", "all") for r in r51)
        assert all(r["version_scope"] in ("6.0.0", "all") for r in r60)

    def test_delete_source_removes_chunks(self, fts_store):
        fts_store.insert_chunk("src_delete", "content to remove", "General", "http://del", "5.1.0", 1)
        fts_store.insert_chunk("src_keep", "content to keep", "General", "http://keep", "5.1.0", 1)

        deleted = fts_store.delete_source("src_delete")
        assert deleted == 1
        assert fts_store.count_chunks("src_delete") == 0
        assert fts_store.count_chunks("src_keep") == 1

    def test_delete_nonexistent_source_returns_zero(self, fts_store):
        assert fts_store.delete_source("ghost_source") == 0

    def test_empty_query_returns_empty(self, fts_store):
        fts_store.insert_chunk("docs", "something in the index", "Gen", "http://x", "5.1.0", 1)
        # Empty query after stripping non-alphanum should return []
        assert fts_store.search("   ") == []

    def test_invalid_fts_query_does_not_crash(self, fts_store):
        fts_store.insert_chunk("docs", "valid content", "Gen", "http://x", "5.1.0", 1)
        # FTS special chars should not raise exceptions
        result = fts_store.search("AND OR NOT")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# DocumentIndexer tests
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestDocumentIndexer:
    def test_index_document_returns_chunk_count(self, indexer):
        doc = (
            "First paragraph with enough text to pass the minimum length filter.\n\n"
            "Second paragraph about rigid body physics simulation in Isaac Sim."
        )
        count = indexer.index_document("src", doc, "http://example.com", "5.1.0")
        assert count == 2

    def test_index_document_skips_short_chunks(self, indexer):
        doc = "Hi\n\nThis paragraph is long enough to be indexed by the system.\n\nbye"
        count = indexer.index_document("src", doc, "http://example.com", "5.1.0")
        assert count == 1  # only the long paragraph

    def test_index_document_extracts_heading(self, indexer, fts_store, tmp_db_path, monkeypatch):
        import service.isaac_assist_service.retrieval.storage.fts_store as _mod

        monkeypatch.setattr(_mod, "DB_PATH", tmp_db_path)
        from service.isaac_assist_service.retrieval.storage.fts_store import FTSStore
        store = FTSStore()

        # Heading line + content must be ≥50 chars to pass the indexer minimum
        doc = "# Physics Setup\nLearn how to configure RigidBodyAPI for dynamic simulation in Isaac Sim."
        indexer.index_document("src", doc, "http://x", "5.1.0")

        results = store.search("Physics", limit=5)
        # The heading is extracted from the first line of the chunk
        assert any(r.get("section_path") == "Physics Setup" for r in results)
        store.conn.close()

    def test_index_empty_doc_indexes_nothing(self, indexer):
        count = indexer.index_document("src", "", "http://empty.com", "5.1.0")
        assert count == 0

    def test_index_document_persists_to_fts(self, indexer, fts_store, tmp_db_path, monkeypatch):
        import service.isaac_assist_service.retrieval.storage.fts_store as _mod

        monkeypatch.setattr(_mod, "DB_PATH", tmp_db_path)
        from service.isaac_assist_service.retrieval.storage.fts_store import FTSStore
        store = FTSStore()

        indexer.index_document(
            "nvidia_isaac_sim_5_1",
            "Sensor placement and LiDAR configuration in Isaac Sim.",
            "http://docs/sensors",
            "5.1.0",
        )
        assert store.count_chunks("nvidia_isaac_sim_5_1") >= 1
        store.conn.close()


# ---------------------------------------------------------------------------
# Scraper HTML extraction tests  (no network calls — pure parsing)
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestHTMLContentExtractor:
    @pytest.fixture()
    def extractor(self):
        from scripts.scrape_isaac_docs import _extract_text_chunks
        return _extract_text_chunks

    def test_extracts_paragraphs(self, extractor):
        html = "<html><body><p>Rigid body simulation example used throughout Isaac Sim physics setup.</p></body></html>"
        chunks = extractor(html)
        assert any("Rigid body" in c for c in chunks)

    def test_skips_nav_content(self, extractor):
        html = (
            "<html><body>"
            "<nav><p>Skip this nav text because it belongs to navigation.</p></nav>"
            "<p>Main article content about rigid body physics in Isaac Sim simulation.</p>"
            "</body></html>"
        )
        chunks = extractor(html)
        assert not any("Skip this nav text" in c for c in chunks)
        assert any("Main article content" in c for c in chunks)

    def test_skips_footer(self, extractor):
        html = (
            "<html><body>"
            "<footer><p>Footer nav text with copyright notices and links here.</p></footer>"
            "<p>Article body text about sensors in Isaac Sim robot simulations.</p>"
            "</body></html>"
        )
        chunks = extractor(html)
        assert not any("Footer nav" in c for c in chunks)

    def test_extracts_headings(self, extractor):
        html = (
            "<html><body>"
            "<h2>Physics API Reference for Isaac Sim Articulation</h2>"
            "<p>Detailed physics documentation for RigidBodyAPI and CollisionAPI configuration.</p>"
            "</body></html>"
        )
        chunks = extractor(html)
        assert any("Physics API" in c for c in chunks)

    def test_skips_script_content(self, extractor):
        html = (
            "<html><body>"
            "<script>var x = 1; // analytics tracking code</script>"
            "<p>Real content about physics simulation and articulation in Isaac Sim.</p>"
            "</body></html>"
        )
        chunks = extractor(html)
        assert not any("var x" in c for c in chunks)
        assert any("Real content" in c for c in chunks)

    def test_empty_html_returns_empty(self, extractor):
        assert extractor("") == []

    def test_short_content_filtered_out(self, extractor):
        html = (
            "<html><body>"
            "<p>Hi</p>"
            "<p>This sentence is certainly long enough to be included in the FTS results.</p>"
            "</body></html>"
        )
        chunks = extractor(html)
        assert not any(c == "Hi" for c in chunks)
        assert any("long enough" in c for c in chunks)


# ---------------------------------------------------------------------------
# Sitemap parsing tests
# ---------------------------------------------------------------------------

@pytest.mark.l0
class TestSitemapParser:
    @pytest.fixture()
    def parse(self):
        from scripts.scrape_isaac_docs import _parse_sitemap
        return _parse_sitemap

    def test_parse_urlset(self, parse):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/page1.html</loc></url>
  <url><loc>https://docs.example.com/page2.html</loc></url>
</urlset>"""
        urls = parse(xml)
        assert "https://docs.example.com/page1.html" in urls
        assert "https://docs.example.com/page2.html" in urls

    def test_parse_sitemap_index(self, parse):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://docs.example.com/sitemap-pages.xml</loc></sitemap>
</sitemapindex>"""
        urls = parse(xml)
        assert "https://docs.example.com/sitemap-pages.xml" in urls

    def test_invalid_xml_returns_empty(self, parse):
        result = parse("this is not xml at all!!")
        assert result == []

    def test_empty_sitemap_returns_empty(self, parse):
        xml = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>"""
        assert parse(xml) == []
