"""
L0 tests for the KnowledgeBase (JSONL-backed experiential memory).
All tests use tmp_path via the knowledge_base fixture.
"""
import json
import pytest

pytestmark = pytest.mark.l0


class TestAddAndGetEntries:

    def test_add_entry_returns_true(self, knowledge_base):
        ok = knowledge_base.add_entry("5.1", "How to X?", "Do Y.", source="audit")
        assert ok is True

    def test_get_entries_returns_what_was_added(self, knowledge_base):
        knowledge_base.add_entry("5.1", "Q1", "A1", source="audit")
        knowledge_base.add_entry("5.1", "Q2", "A2", source="audit")
        entries = knowledge_base.get_entries("5.1")
        assert len(entries) == 2
        assert entries[0]["instruction"] == "Q1"
        assert entries[1]["instruction"] == "Q2"

    def test_separate_versions(self, knowledge_base):
        knowledge_base.add_entry("5.0", "Old Q", "Old A")
        knowledge_base.add_entry("5.1", "New Q", "New A")
        assert len(knowledge_base.get_entries("5.0")) == 1
        assert len(knowledge_base.get_entries("5.1")) == 1

    def test_empty_version(self, knowledge_base):
        entries = knowledge_base.get_entries("nonexistent")
        assert entries == []

    def test_contribute_data_opt_out(self, knowledge_base, monkeypatch):
        """approved_patch source should be skipped when contribute_data is False."""
        from service.isaac_assist_service.config import config
        monkeypatch.setattr(config, "contribute_data", False)
        ok = knowledge_base.add_entry("5.1", "Q", "A", source="approved_patch")
        assert ok is False
        assert len(knowledge_base.get_entries("5.1")) == 0

    def test_contribute_data_opt_in(self, knowledge_base, monkeypatch):
        from service.isaac_assist_service.config import config
        monkeypatch.setattr(config, "contribute_data", True)
        ok = knowledge_base.add_entry("5.1", "Q", "A", source="approved_patch")
        assert ok is True
        assert len(knowledge_base.get_entries("5.1")) == 1

    def test_auto_error_source_always_writes(self, knowledge_base, monkeypatch):
        """auto_error_learning should write regardless of contribute_data."""
        from service.isaac_assist_service.config import config
        monkeypatch.setattr(config, "contribute_data", False)
        ok = knowledge_base.add_entry("5.1", "Q", "A", source="auto_error_learning")
        assert ok is True


class TestErrorDeduplication:

    def test_add_error_deduplicates(self, knowledge_base):
        ok1 = knowledge_base.add_error("5.1", "Q", "A", error_output="TypeError: int is bad")
        ok2 = knowledge_base.add_error("5.1", "Q2", "A2", error_output="TypeError: int is bad")
        assert ok1 is True
        assert ok2 is False  # duplicate
        entries = knowledge_base.get_entries("5.1")
        assert len(entries) == 1

    def test_different_errors_both_stored(self, knowledge_base):
        knowledge_base.add_error("5.1", "Q", "A", error_output="TypeError: X")
        knowledge_base.add_error("5.1", "Q", "A", error_output="ValueError: Y")
        entries = knowledge_base.get_entries("5.1")
        assert len(entries) == 2

    def test_is_known_error(self, knowledge_base):
        knowledge_base.add_error("5.1", "Q", "A", error_output="KeyError: 'foo'")
        assert knowledge_base.is_known_error("5.1", "KeyError: 'foo'") is True
        assert knowledge_base.is_known_error("5.1", "ValueError: 'bar'") is False


class TestSuccessDeduplication:

    def test_add_success_deduplicates(self, knowledge_base):
        ok1 = knowledge_base.add_success("5.1", "create cube", "code1")
        ok2 = knowledge_base.add_success("5.1", "create cube", "code2")
        assert ok1 is True
        assert ok2 is False
        entries = knowledge_base.get_entries("5.1")
        assert len(entries) == 1

    def test_different_messages_both_stored(self, knowledge_base):
        knowledge_base.add_success("5.1", "create cube", "code1")
        knowledge_base.add_success("5.1", "delete prim", "code2")
        entries = knowledge_base.get_entries("5.1")
        assert len(entries) == 2


class TestErrorRetrieval:

    def test_get_error_learnings_keyword_match(self, knowledge_base):
        knowledge_base.add_error(
            "5.1",
            "Error: When asked about robot physics\nError: TypeError: physics mismatch",
            "Fix: use convexHull",
            error_output="TypeError: physics mismatch",
        )
        knowledge_base.add_error(
            "5.1",
            "Error: When asked about material\nError: ValueError: bad color",
            "Fix: use Vec3f",
            error_output="ValueError: bad color",
        )
        results = knowledge_base.get_error_learnings("5.1", "physics robot")
        assert len(results) >= 1
        # Physics-related entry should rank higher
        assert "physics" in results[0]["instruction"].lower()

    def test_get_error_learnings_no_match(self, knowledge_base):
        knowledge_base.add_error("5.1", "Error: X", "Fix", error_output="X")
        results = knowledge_base.get_error_learnings("5.1", "zzzzunrelated")
        assert len(results) == 0


class TestSuccessRetrieval:

    def test_get_success_learnings(self, knowledge_base):
        knowledge_base.add_success("5.1", "create a red cube", "code for cube")
        knowledge_base.add_success("5.1", "import franka robot", "code for robot")
        results = knowledge_base.get_success_learnings("5.1", "cube")
        assert len(results) >= 1


class TestFormatting:

    def test_format_error_learnings_empty(self, knowledge_base):
        text = knowledge_base.format_error_learnings([])
        assert text == ""

    def test_format_error_learnings_nonempty(self, knowledge_base):
        learnings = [
            {"instruction": "Q", "response": "A"},
        ]
        text = knowledge_base.format_error_learnings(learnings)
        assert "KNOWN ERRORS" in text
        assert "ERROR 1" in text

    def test_format_success_learnings_empty(self, knowledge_base):
        assert knowledge_base.format_success_learnings([]) == ""

    def test_format_success_learnings_nonempty(self, knowledge_base):
        learnings = [{"instruction": "create cube", "response": "code"}]
        text = knowledge_base.format_success_learnings(learnings)
        assert "PROVEN WORKING" in text


class TestCompaction:

    def test_compact_deduplicates_errors(self, knowledge_base):
        # Add duplicate errors (bypassing add_error dedup for testing)
        for i in range(5):
            knowledge_base.add_entry(
                "5.1",
                "Error: same error",
                "Fix",
                source="auto_error_learning",
            )
        result = knowledge_base.compact("5.1")
        assert result["before"] == 5
        assert result["after"] < 5  # deduplicated

    def test_compact_empty_version(self, knowledge_base):
        result = knowledge_base.compact("empty_ver")
        assert result == {"before": 0, "after": 0}

    def test_compact_resets_cache(self, knowledge_base):
        # Use instruction with "Error:" prefix so _load_error_sigs can parse it
        knowledge_base.add_error(
            "5.1",
            "Error: ErrorX happened",
            "Fix it",
            error_output="ErrorX happened",
        )
        assert knowledge_base.is_known_error("5.1", "ErrorX happened") is True
        knowledge_base.compact("5.1")
        # Cache should be reset; it re-loads from the compacted file
        assert knowledge_base.is_known_error("5.1", "ErrorX happened") is True

    def test_compact_respects_limits(self, knowledge_base):
        for i in range(30):
            knowledge_base.add_entry("5.1", f"Other entry {i}", "R", source="audit")
        result = knowledge_base.compact("5.1", max_other=10)
        assert result["after"] <= 10


class TestGetSupportedVersions:

    def test_lists_versions(self, knowledge_base):
        knowledge_base.add_entry("5.0", "Q", "A")
        knowledge_base.add_entry("5.1", "Q", "A")
        versions = knowledge_base.get_supported_versions()
        assert "5.0" in versions
        assert "5.1" in versions

    def test_empty_returns_empty_list(self, knowledge_base):
        assert knowledge_base.get_supported_versions() == []


class TestVersionSanitization:

    def test_special_chars_stripped(self, knowledge_base):
        knowledge_base.add_entry("5.1/beta@rc1", "Q", "A")
        # Should still work (special chars stripped from filename)
        entries = knowledge_base.get_entries("5.1/beta@rc1")
        assert len(entries) == 1

    def test_empty_version_uses_default(self, knowledge_base):
        knowledge_base.add_entry("", "Q", "A")
        entries = knowledge_base.get_entries("")
        assert len(entries) == 1
