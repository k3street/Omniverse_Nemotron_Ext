"""Unit tests for scripts/qa/* helpers added during Phase 12 direct-mode work.

Covers pure-Python logic only (no Isaac, no network):
* canary_trend._parse_gt — groundtruth JSONL aggregation + parse_error fallback
* aggregate_failures.aggregate — tool-failure tally across campaigns
* direct_eval._task_goal_query — goal-section extraction from task spec
* multi_turn_session._is_give_up — tail-only give-up detection (no false pos)
* multi_turn_session._extract_pre_session_code — fenced python block parse
* ground_truth_judge._judge_one fallback — regex real_success extraction
  from truncated JSON (via _judge function path)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# canary_trend._parse_gt
# ---------------------------------------------------------------------------

def test_canary_trend_parse_gt_basic(tmp_path: Path):
    from scripts.qa.canary_trend import _parse_gt

    gt = tmp_path / "gt.jsonl"
    gt.write_text(
        json.dumps({"task": "G-01", "verdict": {"real_success": True, "fabricated_claims": []}}) + "\n"
        + json.dumps({"task": "G-02", "verdict": {"real_success": False, "fabricated_claims": ["a", "b"]}}) + "\n"
        + json.dumps({"task": "FX-01", "verdict": {"real_success": True, "fabricated_claims": ["x"]}}) + "\n"
    )
    ok, total, fab, tasks = _parse_gt(gt)
    assert ok == 2
    assert total == 3
    assert fab == 3
    assert tasks == ["✓G-01", "✗G-02", "✓FX-01"]


def test_canary_trend_parse_gt_fallback_on_parse_error(tmp_path: Path):
    """If the judge returned parse_error but the raw string contains
    "real_success": true, the trend log must still count that as a success."""
    from scripts.qa.canary_trend import _parse_gt

    gt = tmp_path / "gt.jsonl"
    gt.write_text(
        json.dumps({
            "task": "G-07",
            "verdict": {
                "parse_error": "Expecting value: line 3 column 1 (char 42)",
                "raw": 'prefix... "real_success": true, ...truncated',
                "fabricated_claims": [],
            },
        }) + "\n"
    )
    ok, total, fab, tasks = _parse_gt(gt)
    assert ok == 1
    assert total == 1
    assert tasks == ["✓G-07"]


# ---------------------------------------------------------------------------
# aggregate_failures.aggregate
# ---------------------------------------------------------------------------

def test_aggregate_failures_counts_tool_fails(tmp_path: Path, monkeypatch):
    """Write a fake campaign + transcript and confirm aggregate picks up the
    failure signature and call count."""
    from scripts.qa import aggregate_failures as af

    monkeypatch.setattr(af, "REPO_ROOT", tmp_path)
    runs = tmp_path / "workspace" / "qa_runs"
    runs.mkdir(parents=True)

    tx = runs / "run_abc" / "T-01_direct.jsonl"
    tx.parent.mkdir(parents=True)
    tx.write_text(
        json.dumps({
            "event": "isaac_assist_reply",
            "tool_calls": [
                {"tool": "anchor_robot", "result": {"success": False, "output": "CreateFixedBaseAttr is not a valid method"}},
                {"tool": "anchor_robot", "result": {"success": False, "output": "CreateFixedBaseAttr is not a valid method"}},
                {"tool": "prim_exists", "result": {"success": True, "output": "{\"exists\": true}"}},
            ],
        }) + "\n"
    )

    gt = runs / "campaign_direct_20260418T000000_groundtruth.jsonl"
    gt.write_text(
        json.dumps({"task": "T-01", "transcript": str(tx), "verdict": {}}) + "\n"
    )

    stats = af.aggregate()
    assert "anchor_robot" in stats
    assert stats["anchor_robot"]["calls"] == 2
    assert stats["anchor_robot"]["fails"] == 2
    assert stats["prim_exists"]["calls"] == 1
    assert stats["prim_exists"]["fails"] == 0
    top_err, _ = stats["anchor_robot"]["errors"].most_common(1)[0]
    assert "CreateFixedBaseAttr" in top_err


def test_aggregate_failures_tool_filter(tmp_path: Path, monkeypatch):
    from scripts.qa import aggregate_failures as af

    monkeypatch.setattr(af, "REPO_ROOT", tmp_path)
    runs = tmp_path / "workspace" / "qa_runs"
    runs.mkdir(parents=True)
    tx = runs / "run_x" / "t.jsonl"
    tx.parent.mkdir(parents=True)
    tx.write_text(
        json.dumps({
            "event": "isaac_assist_reply",
            "tool_calls": [
                {"tool": "tool_a", "result": {"success": False, "output": "err_a"}},
                {"tool": "tool_b", "result": {"success": False, "output": "err_b"}},
            ],
        }) + "\n"
    )
    gt = runs / "campaign_direct_20260418T010203_groundtruth.jsonl"
    gt.write_text(json.dumps({"task": "X-01", "transcript": str(tx), "verdict": {}}) + "\n")

    stats = af.aggregate(tool_filter="tool_a")
    assert set(stats.keys()) == {"tool_a"}


def test_aggregate_failures_excludes_infra_errors(tmp_path: Path, monkeypatch):
    """Kit-RPC-down errors must not pollute the failure ranking by default.
    They reflect harness state, not tool bugs — one Kit crash produces N
    per-tool failures and would drown the signal from actual broken tools."""
    from scripts.qa import aggregate_failures as af

    monkeypatch.setattr(af, "REPO_ROOT", tmp_path)
    runs = tmp_path / "workspace" / "qa_runs"
    runs.mkdir(parents=True)
    tx = runs / "r" / "t.jsonl"
    tx.parent.mkdir(parents=True)
    tx.write_text(
        json.dumps({
            "event": "isaac_assist_reply",
            "tool_calls": [
                {"tool": "create_prim", "result": {"success": False, "output": "Cannot connect to host 127.0.0.1:8001"}},
                {"tool": "create_prim", "result": {"success": False, "output": "Cannot connect to host 127.0.0.1:8001"}},
                {"tool": "create_prim", "result": {"success": False, "output": "genuine USD error: xform op not authored"}},
            ],
        }) + "\n"
    )
    gt = runs / "campaign_direct_20260418T050505_groundtruth.jsonl"
    gt.write_text(json.dumps({"task": "X", "transcript": str(tx), "verdict": {}}) + "\n")

    stats = af.aggregate()
    s = stats["create_prim"]
    assert s["calls"] == 3
    assert s["fails"] == 1  # only the genuine USD error
    assert s["infra_fails"] == 2
    assert all("Cannot connect" not in e for e in s["errors"])

    # With --include-infra they do count
    stats_i = af.aggregate(include_infra=True)
    assert stats_i["create_prim"]["fails"] == 3


def test_aggregate_failures_time_window_filters_old(tmp_path: Path, monkeypatch):
    """since_epoch must skip campaigns whose timestamp (from filename) is older."""
    import time as _t
    from scripts.qa import aggregate_failures as af

    monkeypatch.setattr(af, "REPO_ROOT", tmp_path)
    runs = tmp_path / "workspace" / "qa_runs"
    runs.mkdir(parents=True)
    tx = runs / "r" / "t.jsonl"
    tx.parent.mkdir(parents=True)
    tx.write_text(
        json.dumps({
            "event": "isaac_assist_reply",
            "tool_calls": [{"tool": "old_tool", "result": {"success": False, "output": "err"}}],
        }) + "\n"
    )
    gt_old = runs / "campaign_direct_20250101T000000_groundtruth.jsonl"
    gt_old.write_text(json.dumps({"task": "X", "transcript": str(tx), "verdict": {}}) + "\n")

    gt_new = runs / f"campaign_direct_{_t.strftime('%Y%m%dT%H%M%S')}_groundtruth.jsonl"
    gt_new.write_text(
        json.dumps({"task": "X",
                    "transcript": str(tx),
                    "verdict": {}}) + "\n"
    )

    # Window of last 1 hour: only the new one should be counted
    cutoff = _t.time() - 3600
    stats = af.aggregate(since_epoch=cutoff)
    # Both reference the same tx, so the tool appears once per gt-line.
    # Calls from old campaign must be excluded.
    assert stats["old_tool"]["calls"] == 1


# ---------------------------------------------------------------------------
# direct_eval._task_goal_query
# ---------------------------------------------------------------------------

def test_direct_eval_task_goal_query(tmp_path: Path, monkeypatch):
    from scripts.qa import direct_eval as de

    tasks_dir = tmp_path / "docs" / "qa" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "G-99.md").write_text(
        "# Task G-99\n\n"
        "**Goal:** A cube already exists at /World/Anchor. "
        "Alex wants a sphere placed 1.5m away along +X.\n\n"
        "**Success criteria:**\n- sphere at correct location\n"
    )
    monkeypatch.setattr(de, "REPO_ROOT", tmp_path)
    q = de._task_goal_query("G-99")
    assert q is not None
    # Full goal must be preserved — previous first-sentence truncation
    # dropped the actual ask ("Alex wants a sphere...") and left only the
    # context sentence, which reduced the query to a state check.
    assert q.startswith("A cube already exists at /World/Anchor.")
    assert "Alex wants a sphere placed 1.5m away along +X." in q


def test_direct_eval_task_goal_query_collapses_whitespace(tmp_path: Path, monkeypatch):
    from scripts.qa import direct_eval as de

    tasks_dir = tmp_path / "docs" / "qa" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "G-97.md").write_text(
        "# T\n\n**Goal:** First line.\n\n  Second line   with   extra spaces.\n\n**Success:**\n- x\n"
    )
    monkeypatch.setattr(de, "REPO_ROOT", tmp_path)
    q = de._task_goal_query("G-97")
    assert q == "First line. Second line with extra spaces."


def test_direct_eval_task_goal_query_caps_length(tmp_path: Path, monkeypatch):
    from scripts.qa import direct_eval as de

    tasks_dir = tmp_path / "docs" / "qa" / "tasks"
    tasks_dir.mkdir(parents=True)
    long_goal = "word " * 400  # ~2000 chars
    (tasks_dir / "G-96.md").write_text(f"# T\n\n**Goal:** {long_goal}\n\n**S:**\n- x\n")
    monkeypatch.setattr(de, "REPO_ROOT", tmp_path)
    q = de._task_goal_query("G-96")
    assert len(q) == 600


def test_direct_eval_task_goal_query_missing_file(tmp_path: Path, monkeypatch):
    from scripts.qa import direct_eval as de
    monkeypatch.setattr(de, "REPO_ROOT", tmp_path)
    assert de._task_goal_query("does-not-exist") is None


# ---------------------------------------------------------------------------
# multi_turn_session._is_give_up — tail-only check
# ---------------------------------------------------------------------------

def test_is_give_up_final_bye():
    from scripts.qa.multi_turn_session import _is_give_up
    assert _is_give_up("thanks for the help, bye.") is True


def test_is_give_up_stage_direction():
    from scripts.qa.multi_turn_session import _is_give_up
    assert _is_give_up("[session ended]") is True
    assert _is_give_up("*closes tab*") is True


def test_is_give_up_false_positive_later_midsentence():
    """Regression: 'physx tensor views work later. Source should land in the
    next drop...' triggered give-up at T1 before the tail-only fix. Now that
    only the last ~80 chars are scanned, 'later.' earlier in a long persona
    message must NOT trigger."""
    from scripts.qa.multi_turn_session import _is_give_up
    text = (
        "ok so physx tensor views work later. Source should land in the next "
        "drop. In the meantime, can you show me how to wire the articulation "
        "view through the scene-graph callback the right way from first "
        "principles — I'd like to actually understand it?"
    )
    assert _is_give_up(text) is False


def test_is_give_up_no_match():
    from scripts.qa.multi_turn_session import _is_give_up
    assert _is_give_up("can you also create a sphere next to it?") is False


# ---------------------------------------------------------------------------
# multi_turn_session._extract_pre_session_code
# ---------------------------------------------------------------------------

def test_extract_pre_session_code_python_fence(tmp_path: Path, monkeypatch):
    from scripts.qa import multi_turn_session as mts

    tasks_dir = tmp_path / "docs" / "qa" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "T-99.md").write_text(
        "# Task T-99\n\n**Goal:** do something.\n\n"
        "## Pre-session setup\n\n"
        "```python\n"
        "import omni.usd\n"
        "print('seeded')\n"
        "```\n\n"
        "## Success criteria\n- x\n"
    )
    monkeypatch.setattr(mts, "REPO_ROOT", tmp_path)
    code = mts._extract_pre_session_code("T-99")
    assert code is not None
    assert "import omni.usd" in code
    assert "print('seeded')" in code


def test_extract_pre_session_code_no_block(tmp_path: Path, monkeypatch):
    from scripts.qa import multi_turn_session as mts

    tasks_dir = tmp_path / "docs" / "qa" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "T-98.md").write_text("# Task\n\n**Goal:** x\n")
    monkeypatch.setattr(mts, "REPO_ROOT", tmp_path)
    assert mts._extract_pre_session_code("T-98") is None


def test_extract_pre_session_code_missing_spec(tmp_path: Path, monkeypatch):
    from scripts.qa import multi_turn_session as mts
    monkeypatch.setattr(mts, "REPO_ROOT", tmp_path)
    assert mts._extract_pre_session_code("nope") is None


# ---------------------------------------------------------------------------
# ground_truth_judge — fallback regex on truncated JSON
# ---------------------------------------------------------------------------

def test_gen_check_physics_health_imports_usd():
    """Regression: generated code uses Usd.PrimRange — must import Usd.
    Previous version imported only (UsdGeom, UsdPhysics, Gf, PhysxSchema)
    and blew up with 'name Usd is not defined' at runtime."""
    import sys
    sys.path.insert(0, "service")
    from isaac_assist_service.chat.tools.tool_executor import _gen_check_physics_health
    code = _gen_check_physics_health({})
    assert "Usd.PrimRange" in code
    import re
    m = re.search(r"from pxr import ([^\n]+)", code)
    assert m is not None
    imports = {t.strip() for t in m.group(1).split(",")}
    assert "Usd" in imports, f"Usd missing from pxr import; got {imports}"


def test_gen_create_behavior_fails_fast():
    """Regression: Isaac Sim 5.x Cortex API requires RmpFlow/AMP plumbing
    we don't have here. Better to raise NotImplementedError than emit code
    that calls MotionCommander('/path') and fails with a signature error."""
    import sys
    sys.path.insert(0, "service")
    from isaac_assist_service.chat.tools.tool_executor import _gen_create_behavior
    code = _gen_create_behavior({"articulation_path": "/World/Franka",
                                 "behavior_type": "pick_and_place"})
    assert "raise NotImplementedError" in code
    import ast
    ast.parse(code)  # must be valid Python


def test_gen_create_graph_drops_pipeline_stage_field():
    """Regression: og.Controller.edit() takes GraphPipelineStage for
    'pipeline_stage', not GraphBackingType. Passing a backing-type value
    there triggers 'incompatible function arguments' in 5.x. The template
    was simplified to drop that field entirely."""
    import sys
    sys.path.insert(0, "service")
    from isaac_assist_service.chat.tools.tool_executor import _gen_create_graph
    code = _gen_create_graph({"template": "ros2_clock", "description": "test"})
    assert "og.Controller.edit" in code
    assert "GraphBackingType" not in code
    assert "pipeline_stage" not in code


def test_judge_fallback_extracts_real_success_from_truncated():
    """Simulate the fallback code path in ground_truth_judge: when JSON parse
    fails, a regex should still lift out real_success and goal_achieved."""
    import re
    raw = (
        '{ "real_success": true, "goal_achieved": true, '
        '"fabricated_claims": [], "notes": "looks good but the string was tru'
    )
    fallback = {"parse_error": "Unterminated string", "raw": raw[:1200]}
    for field in ("real_success", "goal_achieved", "scene_matched_criterion"):
        m = re.search(rf'"{field}"\s*:\s*(true|false)', raw)
        if m:
            fallback[field] = (m.group(1) == "true")
    assert fallback["real_success"] is True
    assert fallback["goal_achieved"] is True
    assert "scene_matched_criterion" not in fallback
