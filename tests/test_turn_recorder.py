"""
L0 tests for TurnRecorder — the fine-tune flywheel instrumentation layer.
Covers record creation, redaction, stats, export formats, feedback linking,
and quality filtering.  All file I/O uses the tmp_path fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# turn_recorder lives on a separate phase branch — guard import so this
# test module collects on master-derived branches.
turn_recorder = pytest.importorskip(
    "service.isaac_assist_service.finetune.turn_recorder",
    reason="turn_recorder module lives on a different phase branch",
)
TurnRecorder = turn_recorder.TurnRecorder
_SECRET_PATTERNS = turn_recorder._SECRET_PATTERNS
_EXTERNAL_PATH_RE = turn_recorder._EXTERNAL_PATH_RE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    session_id: str = "sess-1",
    turn_id: int = 0,
    user_message: str = "Create a cube at the origin",
    context: dict | None = None,
    intent: str = "scene_create",
    tool_calls: list | None = None,
    assistant_message: str = "Done. I created a cube at /World/Cube.",
    feedback: dict | None = None,
) -> dict:
    """Build kwargs for TurnRecorder.record_turn()."""
    return dict(
        session_id=session_id,
        turn_id=turn_id,
        user_message=user_message,
        context=context or {"selected_prim": "/World/Cube", "stage_summary": "1 prim"},
        intent=intent,
        tool_calls=tool_calls or [
            {
                "tool": "create_prim",
                "arguments": {"prim_path": "/World/Cube", "prim_type": "Cube"},
                "result": {"type": "code_patch", "code": "..."},
            }
        ],
        assistant_message=assistant_message,
        feedback=feedback,
    )


def _write_sample_turns(recorder: TurnRecorder, count: int = 3) -> None:
    """Write several sample turns to the recorder."""
    for i in range(count):
        recorder.record_turn(**_make_record(turn_id=i))


# ---------------------------------------------------------------------------
# Record creation
# ---------------------------------------------------------------------------

class TestRecordCreation:
    """Verify that record_turn writes well-formed JSONL."""

    def test_creates_jsonl_file(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        path = rec.record_turn(**_make_record())
        assert path.exists()
        assert path.suffix == ".jsonl"

    def test_record_contains_required_fields(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record())

        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])

        assert "session_id" in record
        assert "turn_id" in record
        assert "timestamp" in record
        assert record["timestamp"].endswith("Z")
        assert "input" in record
        assert "user_message" in record["input"]
        assert "intent" in record
        assert "output" in record
        assert "tool_calls" in record["output"]
        assert "assistant_message" in record["output"]
        assert "feedback" in record

    def test_multiple_turns_append(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(turn_id=0))
        rec.record_turn(**_make_record(turn_id=1))

        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        assert len(lines) == 2

    def test_context_fields_captured(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            context={"selected_prim": "/World/Robot", "stage_summary": "5 prims"},
        ))

        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert record["input"]["selected_prim"] == "/World/Robot"
        assert record["input"]["stage_context"] == "5 prims"

    def test_none_feedback_is_preserved(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(feedback=None))

        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert record["feedback"] is None


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

class TestRedaction:
    """Verify that _redact strips secrets and external paths."""

    def test_openai_api_key_stripped(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            user_message="My key is sk-abc123456789012345678901234567890123",
        ))
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "sk-abc" not in record["input"]["user_message"]
        assert "<REDACTED_SECRET>" in record["input"]["user_message"]

    def test_google_api_key_stripped(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            user_message="Use AIzaSyA123456789012345678901234567890123",
        ))
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "AIzaSy" not in record["input"]["user_message"]

    def test_bearer_token_stripped(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            user_message="Token: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.something",
        ))
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "eyJhbGci" not in record["input"]["user_message"]

    def test_github_pat_stripped(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            user_message="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij1234",
        ))
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "ghp_" not in record["input"]["user_message"]

    def test_external_path_replaced(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            user_message="Load from /home/user/secret_project/model.usd",
        ))
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "/home/user" not in record["input"]["user_message"]
        assert "<REDACTED_PATH>" in record["input"]["user_message"]

    def test_workspace_path_not_stripped(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            user_message="Save to workspace/finetune_data/output.jsonl",
        ))
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "workspace/finetune_data/output.jsonl" in record["input"]["user_message"]

    def test_redact_file(self, tmp_path: Path):
        """Test the redact_file method on an existing JSONL."""
        # Write a file with a secret
        src = tmp_path / "raw.jsonl"
        src.write_text(json.dumps({
            "user_message": "Key: sk-aaaa1111bbbb2222cccc3333dddd4444eeee5555",
            "path": "/home/user/data.txt",
        }) + "\n")

        rec = TurnRecorder(output_dir=str(tmp_path))
        result = rec.redact_file(str(src))
        assert result["status"] == "success"
        assert result["record_count"] == 1

        out = Path(result["output_path"])
        record = json.loads(out.read_text().strip())
        assert "sk-" not in record["user_message"]
        assert "/home/user" not in record["path"]

    def test_redact_file_custom_output(self, tmp_path: Path):
        src = tmp_path / "input.jsonl"
        src.write_text(json.dumps({"msg": "clean data"}) + "\n")

        out = tmp_path / "custom_out.jsonl"
        rec = TurnRecorder(output_dir=str(tmp_path))
        result = rec.redact_file(str(src), output_path=str(out))
        assert result["status"] == "success"
        assert out.exists()

    def test_redact_file_not_found(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        result = rec.redact_file(str(tmp_path / "nope.jsonl"))
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    """Verify get_stats computes correct aggregates."""

    def test_empty_stats(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        stats = rec.get_stats()
        assert stats["total_turns"] == 0
        assert stats["approval_rate"] == 0.0

    def test_stats_total_turns(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        _write_sample_turns(rec, count=5)
        stats = rec.get_stats()
        assert stats["total_turns"] == 5

    def test_stats_approval_rate(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        # 2 approved, 1 rejected
        rec.record_turn(**_make_record(turn_id=0, feedback={"approved": True}))
        rec.record_turn(**_make_record(turn_id=1, feedback={"approved": True}))
        rec.record_turn(**_make_record(turn_id=2, feedback={"approved": False}))

        stats = rec.get_stats()
        assert stats["total_turns"] == 3
        assert abs(stats["approval_rate"] - 2 / 3) < 0.01

    def test_stats_tool_distribution(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            turn_id=0,
            tool_calls=[
                {"tool": "create_prim", "arguments": {}, "result": {"type": "code_patch"}},
                {"tool": "set_attribute", "arguments": {}, "result": {"type": "code_patch"}},
            ],
        ))
        rec.record_turn(**_make_record(
            turn_id=1,
            tool_calls=[
                {"tool": "create_prim", "arguments": {}, "result": {"type": "code_patch"}},
            ],
        ))

        stats = rec.get_stats()
        assert stats["tool_distribution"]["create_prim"] == 2
        assert stats["tool_distribution"]["set_attribute"] == 1

    def test_stats_error_rate(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            turn_id=0,
            tool_calls=[
                {"tool": "create_prim", "arguments": {}, "result": {"type": "code_patch"}},
                {"tool": "run_usd_script", "arguments": {}, "result": {"type": "error", "error": "fail"}},
            ],
        ))

        stats = rec.get_stats()
        assert stats["error_rate"] == 0.5  # 1 error out of 2 tool calls

    def test_stats_date_range(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(turn_id=0))
        rec.record_turn(**_make_record(turn_id=1))

        stats = rec.get_stats()
        assert stats["date_range"]["earliest"] is not None
        assert stats["date_range"]["latest"] is not None
        assert stats["date_range"]["earliest"] <= stats["date_range"]["latest"]

    def test_stats_rejection_correction_pairs(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            turn_id=0,
            feedback={"approved": False, "correction": "Use Sphere instead"},
        ))
        rec.record_turn(**_make_record(
            turn_id=1,
            feedback={"approved": False},  # rejected but no correction
        ))
        rec.record_turn(**_make_record(
            turn_id=2,
            feedback={"approved": True},
        ))

        stats = rec.get_stats()
        assert stats["rejection_correction_pairs"] == 1

    def test_stats_deduplicates_by_latest(self, tmp_path: Path):
        """When a turn is updated (feedback linked), stats should use latest version."""
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(turn_id=0))
        # Simulate feedback update: same session_id + turn_id written again
        rec.record_turn(**_make_record(turn_id=0, feedback={"approved": True}))

        stats = rec.get_stats()
        assert stats["total_turns"] == 1  # deduplicated


# ---------------------------------------------------------------------------
# Feedback linking
# ---------------------------------------------------------------------------

class TestFeedbackLinking:
    """Verify record_feedback finds and updates existing turns."""

    def test_link_feedback_success(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(session_id="s1", turn_id=0))

        result = rec.record_feedback("s1", 0, approved=True)
        assert result["status"] == "linked"
        assert result["approved"] is True

    def test_link_feedback_not_found(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        result = rec.record_feedback("nonexistent", 99, approved=False)
        assert result["status"] == "not_found"

    def test_link_feedback_with_correction(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(session_id="s2", turn_id=3))

        result = rec.record_feedback(
            "s2", 3, approved=False, edited=True, correction="Use Sphere"
        )
        assert result["status"] == "linked"
        assert result["edited"] is True

        # Verify the updated record was appended
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        assert len(lines) == 2  # original + feedback-updated
        updated = json.loads(lines[-1])
        assert updated["feedback"]["approved"] is False
        assert updated["feedback"]["correction"] == "Use Sphere"
        assert "feedback_timestamp" in updated["feedback"]

    def test_feedback_reflects_in_stats(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(session_id="s1", turn_id=0))
        rec.record_feedback("s1", 0, approved=True)

        stats = rec.get_stats()
        # Should deduplicate to 1 turn with approved feedback
        assert stats["total_turns"] == 1
        assert stats["approval_rate"] == 1.0


# ---------------------------------------------------------------------------
# Export formats
# ---------------------------------------------------------------------------

class TestExportFormats:
    """Verify each export format produces valid structure."""

    @pytest.fixture()
    def recorder_with_data(self, tmp_path: Path) -> TurnRecorder:
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            turn_id=0,
            feedback={"approved": True},
        ))
        rec.record_turn(**_make_record(
            turn_id=1,
            feedback={"approved": True},
        ))
        return rec

    def test_export_openai(self, recorder_with_data: TurnRecorder, tmp_path: Path):
        result = recorder_with_data.export("openai", min_quality="approved")
        assert result["status"] == "success"
        assert result["record_count"] == 2
        assert result["format"] == "openai"

        path = Path(result["path"])
        assert path.exists()
        lines = path.read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "messages" in record
        roles = [m["role"] for m in record["messages"]]
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles

    def test_export_openai_includes_tool_calls(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(
            turn_id=0,
            tool_calls=[
                {"tool": "create_prim", "arguments": {"prim_path": "/World/Cube"}, "result": {"type": "code_patch"}},
            ],
            feedback={"approved": True},
        ))
        result = rec.export("openai", min_quality="approved")
        lines = Path(result["path"]).read_text().strip().splitlines()
        record = json.loads(lines[0])
        roles = [m["role"] for m in record["messages"]]
        assert "tool" in roles

    def test_export_anthropic(self, recorder_with_data: TurnRecorder):
        result = recorder_with_data.export("anthropic", min_quality="approved")
        assert result["status"] == "success"
        assert result["record_count"] == 2

        lines = Path(result["path"]).read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "system" in record
        assert "messages" in record
        roles = [m["role"] for m in record["messages"]]
        assert "user" in roles
        assert "assistant" in roles

    def test_export_ollama(self, recorder_with_data: TurnRecorder):
        result = recorder_with_data.export("ollama", min_quality="approved")
        assert result["status"] == "success"
        assert result["record_count"] == 2

        lines = Path(result["path"]).read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "conversations" in record
        froms = [c["from"] for c in record["conversations"]]
        assert "system" in froms
        assert "human" in froms
        assert "gpt" in froms

    def test_export_alpaca(self, recorder_with_data: TurnRecorder):
        result = recorder_with_data.export("alpaca", min_quality="approved")
        assert result["status"] == "success"
        assert result["record_count"] == 2

        lines = Path(result["path"]).read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert "instruction" in record
        assert "input" in record
        assert "output" in record
        assert "system" in record

    def test_export_unknown_format(self, recorder_with_data: TurnRecorder):
        result = recorder_with_data.export("pytorch_native", min_quality="all")
        assert result["status"] == "error"
        assert "Unknown format" in result["message"]

    def test_export_custom_output_path(self, recorder_with_data: TurnRecorder, tmp_path: Path):
        out = str(tmp_path / "custom" / "export.jsonl")
        result = recorder_with_data.export("openai", min_quality="approved", output_path=out)
        assert result["status"] == "success"
        assert Path(out).exists()

    def test_export_empty_after_filter(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        # Record turn without feedback
        rec.record_turn(**_make_record(turn_id=0))
        result = rec.export("openai", min_quality="approved")
        assert result["status"] == "empty"
        assert result["record_count"] == 0


# ---------------------------------------------------------------------------
# Quality filtering
# ---------------------------------------------------------------------------

class TestQualityFiltering:
    """Verify that quality filters work correctly."""

    def test_filter_all(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(turn_id=0))
        rec.record_turn(**_make_record(turn_id=1, feedback={"approved": True}))
        rec.record_turn(**_make_record(turn_id=2, feedback={"approved": False}))

        result = rec.export("openai", min_quality="all")
        assert result["record_count"] == 3

    def test_filter_approved(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        rec.record_turn(**_make_record(turn_id=0))  # no feedback
        rec.record_turn(**_make_record(turn_id=1, feedback={"approved": True}))
        rec.record_turn(**_make_record(turn_id=2, feedback={"approved": False}))

        result = rec.export("openai", min_quality="approved")
        assert result["record_count"] == 1

    def test_filter_approved_successful(self, tmp_path: Path):
        rec = TurnRecorder(output_dir=str(tmp_path))
        # Approved + no errors
        rec.record_turn(**_make_record(
            turn_id=0,
            feedback={"approved": True},
            tool_calls=[{"tool": "create_prim", "arguments": {}, "result": {"type": "code_patch"}}],
        ))
        # Approved but has error
        rec.record_turn(**_make_record(
            turn_id=1,
            feedback={"approved": True},
            tool_calls=[{"tool": "run_usd_script", "arguments": {}, "result": {"type": "error"}}],
        ))
        # Not approved
        rec.record_turn(**_make_record(
            turn_id=2,
            feedback={"approved": False},
        ))

        result = rec.export("openai", min_quality="approved_successful")
        assert result["record_count"] == 1


# ---------------------------------------------------------------------------
# DATA handler integration (via tool_executor dispatch)
# ---------------------------------------------------------------------------

class TestDataHandlerRegistration:
    """Verify the 4 finetune tools are registered in DATA_HANDLERS."""

    def test_record_feedback_registered(self):
        from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
        assert "record_feedback" in DATA_HANDLERS
        assert DATA_HANDLERS["record_feedback"] is not None

    def test_export_finetune_data_registered(self):
        from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
        assert "export_finetune_data" in DATA_HANDLERS
        assert DATA_HANDLERS["export_finetune_data"] is not None

    def test_finetune_stats_registered(self):
        from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
        assert "finetune_stats" in DATA_HANDLERS
        assert DATA_HANDLERS["finetune_stats"] is not None

    def test_redact_finetune_data_registered(self):
        from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
        assert "redact_finetune_data" in DATA_HANDLERS
        assert DATA_HANDLERS["redact_finetune_data"] is not None


class TestDataHandlerExecution:
    """Verify that finetune handlers execute correctly through the dispatch layer."""

    @pytest.mark.asyncio
    async def test_finetune_stats_handler(self, monkeypatch):
        """finetune_stats should return stats dict without errors."""
        from service.isaac_assist_service.chat.tools.tool_executor import (
            DATA_HANDLERS,
            _turn_recorder,
        )
        import service.isaac_assist_service.chat.tools.tool_executor as te

        # Point recorder to a temp dir
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            old_recorder = te._turn_recorder
            te._turn_recorder = TurnRecorder(output_dir=td)
            try:
                handler = DATA_HANDLERS["finetune_stats"]
                result = await handler({})
                assert isinstance(result, dict)
                assert "total_turns" in result
                assert "approval_rate" in result
                assert "tool_distribution" in result
            finally:
                te._turn_recorder = old_recorder

    @pytest.mark.asyncio
    async def test_record_feedback_handler_not_found(self, monkeypatch):
        """record_feedback for a nonexistent turn should return not_found."""
        from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
        import service.isaac_assist_service.chat.tools.tool_executor as te
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            old_recorder = te._turn_recorder
            te._turn_recorder = TurnRecorder(output_dir=td)
            try:
                handler = DATA_HANDLERS["record_feedback"]
                result = await handler({
                    "session_id": "ghost",
                    "turn_id": 999,
                    "approved": True,
                })
                assert result["status"] == "not_found"
            finally:
                te._turn_recorder = old_recorder

    @pytest.mark.asyncio
    async def test_redact_handler_missing_file(self, monkeypatch):
        """redact_finetune_data for a missing file should return error."""
        from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
        import service.isaac_assist_service.chat.tools.tool_executor as te
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            old_recorder = te._turn_recorder
            te._turn_recorder = TurnRecorder(output_dir=td)
            try:
                handler = DATA_HANDLERS["redact_finetune_data"]
                result = await handler({"input_path": "/nonexistent/path.jsonl"})
                assert result["status"] == "error"
            finally:
                te._turn_recorder = old_recorder


# ---------------------------------------------------------------------------
# Schema registration
# ---------------------------------------------------------------------------

class TestSchemaRegistration:
    """Verify the 4 finetune tool schemas are in ISAAC_SIM_TOOLS."""

    def test_schemas_present(self):
        from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
        names = [t["function"]["name"] for t in ISAAC_SIM_TOOLS]
        assert "record_feedback" in names
        assert "export_finetune_data" in names
        assert "finetune_stats" in names
        assert "redact_finetune_data" in names

    def test_record_feedback_schema_valid(self):
        from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == "record_feedback")
        fn = tool["function"]
        assert tool["type"] == "function"
        assert "description" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        required = params["required"]
        assert "session_id" in required
        assert "turn_id" in required
        assert "approved" in required

    def test_export_finetune_data_schema_has_enum(self):
        from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
        tool = next(t for t in ISAAC_SIM_TOOLS if t["function"]["name"] == "export_finetune_data")
        fmt_prop = tool["function"]["parameters"]["properties"]["format"]
        assert "enum" in fmt_prop
        assert set(fmt_prop["enum"]) == {"openai", "anthropic", "ollama", "alpaca"}
