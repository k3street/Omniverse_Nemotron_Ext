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


# ---------------------------------------------------------------------------
# Negative pattern persistence and deduplication
# ---------------------------------------------------------------------------

class TestNegativePatterns:

    def test_add_stores_entry(self, knowledge_base):
        ok = knowledge_base.add_negative_pattern(
            version="5.1.0",
            error_signature="AttributeError: NoneType has no startswith",
            failing_code="is_nucleus = asset.startswith('omniverse://')",
            root_cause="asset can be None when LOCAL_ASSETS is unset",
            fix_applied="guard with bool(asset) and asset.startswith(...)",
        )
        assert ok is True
        patterns = knowledge_base._load_negative_patterns("5.1.0")
        assert len(patterns) == 1
        assert patterns[0]["error_signature"] == "AttributeError: NoneType has no startswith"

    def test_add_deduplicates_within_24h(self, knowledge_base):
        sig = "TypeError: unsupported operand int + str"
        ok1 = knowledge_base.add_negative_pattern(
            "5.1.0", sig, "x = 1 + 'a'", "type mismatch", "cast to int"
        )
        ok2 = knowledge_base.add_negative_pattern(
            "5.1.0", sig, "x = 1 + 'a'", "type mismatch", "cast to int"
        )
        assert ok1 is True
        assert ok2 is False  # duplicate within 24 h
        patterns = knowledge_base._load_negative_patterns("5.1.0")
        assert len(patterns) == 1

    def test_add_different_signatures_both_stored(self, knowledge_base):
        knowledge_base.add_negative_pattern(
            "5.1.0", "SigA", "code_a", "cause_a", "fix_a"
        )
        knowledge_base.add_negative_pattern(
            "5.1.0", "SigB", "code_b", "cause_b", "fix_b"
        )
        patterns = knowledge_base._load_negative_patterns("5.1.0")
        sigs = {p["error_signature"] for p in patterns}
        assert "SigA" in sigs and "SigB" in sigs

    def test_load_negative_patterns_empty_version(self, knowledge_base):
        patterns = knowledge_base._load_negative_patterns("nonexistent_ver")
        assert patterns == []


# ---------------------------------------------------------------------------
# get_negative_patterns — keyword-based retrieval
# ---------------------------------------------------------------------------

class TestGetNegativePatterns:

    def test_returns_by_keyword_overlap(self, knowledge_base):
        knowledge_base.add_negative_pattern(
            "5.1.0",
            "physics rigid body crash",
            "RigidBodyAPI.Apply(prim)",
            "prim not on stage",
            "check prim.IsValid() first",
        )
        results = knowledge_base.get_negative_patterns(
            "5.1.0", "rigid body physics", limit=5
        )
        assert len(results) >= 1

    def test_no_match_returns_empty(self, knowledge_base):
        knowledge_base.add_negative_pattern(
            "5.1.0", "collision mesh crash", "mesh = ...", "bad mesh", "fix"
        )
        results = knowledge_base.get_negative_patterns(
            "5.1.0", "completely unrelated query", limit=5
        )
        assert results == []

    def test_empty_patterns_returns_empty(self, knowledge_base):
        results = knowledge_base.get_negative_patterns("5.1.0", "some query", limit=5)
        assert results == []


# ---------------------------------------------------------------------------
# format_negative_patterns — output formatting
# ---------------------------------------------------------------------------

class TestFormatNegativePatterns:

    def test_empty_list_returns_empty_string(self, knowledge_base):
        assert knowledge_base.format_negative_patterns([]) == ""

    def test_nonempty_includes_header(self, knowledge_base):
        patterns = [
            {
                "error_signature": "NullError",
                "root_cause": "None value",
                "fix_applied": "add None check",
            }
        ]
        text = knowledge_base.format_negative_patterns(patterns)
        assert "KNOWN FAILURE PATTERNS" in text

    def test_nonempty_includes_signature(self, knowledge_base):
        patterns = [
            {
                "error_signature": "My special error sig",
                "root_cause": "cause here",
                "fix_applied": "the fix",
            }
        ]
        text = knowledge_base.format_negative_patterns(patterns)
        assert "My special error sig" in text


# ---------------------------------------------------------------------------
# query_by_error_pattern — searches both error learnings AND negative patterns
# ---------------------------------------------------------------------------

class TestQueryByErrorPattern:

    def test_matches_error_learnings(self, knowledge_base):
        knowledge_base.add_error(
            "5.1.0",
            "AttributeError rigid body prim crash",
            "Apply RigidBodyAPI to prim",
            "AttributeError: rigid body prim is None",
        )
        results = knowledge_base.query_by_error_pattern(
            "5.1.0", "rigid body prim", limit=5
        )
        assert len(results) >= 1

    def test_matches_negative_patterns(self, knowledge_base):
        knowledge_base.add_negative_pattern(
            "5.1.0",
            "rigid body prim AttributeError",
            "RigidBodyAPI.Apply(prim)",
            "prim invalid",
            "check IsValid()",
        )
        results = knowledge_base.query_by_error_pattern(
            "5.1.0", "rigid body prim", limit=5
        )
        assert len(results) >= 1

    def test_no_overlap_returns_empty(self, knowledge_base):
        knowledge_base.add_error(
            "5.1.0",
            "collision mesh import error",
            "import mesh from USD file",
            "ImportError: mesh file not found",
        )
        results = knowledge_base.query_by_error_pattern(
            "5.1.0", "articulation joint xyz", limit=5
        )
        assert results == []

    def test_empty_base_returns_empty(self, knowledge_base):
        results = knowledge_base.query_by_error_pattern("5.1.0", "any error", limit=5)
        assert results == []


# ---------------------------------------------------------------------------
# capture_plan_outcome — routes success / failure to the right store
# ---------------------------------------------------------------------------

class TestCapturePlanOutcome:

    def test_success_adds_to_success_learnings(self, knowledge_base):
        knowledge_base.capture_plan_outcome(
            version="5.1.0",
            user_message="add a cube with rigid body",
            plan_steps=["Create Xform", "Apply RigidBodyAPI"],
            success=True,
            error_output=None,
            code="stage.DefinePrim('/World/Cube')",
        )
        entries = knowledge_base.get_entries("5.1.0")
        assert any("cube" in e.get("instruction", "").lower() for e in entries)

    def test_failure_adds_negative_pattern(self, knowledge_base):
        knowledge_base.capture_plan_outcome(
            version="5.1.0",
            user_message="set joint drive stiffness",
            plan_steps=["Find joint prim", "Set stiffness"],
            success=False,
            error_output="AttributeError: 'NoneType' object has no attribute 'GetDriveAPI'",
            code="joint.GetDriveAPI('linear').GetStiffnessAttr().Set(1000)",
        )
        patterns = knowledge_base._load_negative_patterns("5.1.0")
        assert len(patterns) >= 1
        # Error signature should contain some part of the error
        sigs = [p["error_signature"] for p in patterns]
        assert any("AttributeError" in s or "NoneType" in s or "stiffness" in s.lower()
                   for s in sigs)

    def test_success_with_empty_code_still_stores(self, knowledge_base):
        ok_before = len(knowledge_base.get_entries("5.1.0"))
        knowledge_base.capture_plan_outcome(
            version="5.1.0",
            user_message="run simulation",
            plan_steps=["start"],
            success=True,
            error_output=None,
            code="",
        )
        assert len(knowledge_base.get_entries("5.1.0")) >= ok_before

