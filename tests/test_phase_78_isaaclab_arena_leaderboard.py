"""Phase 78 contract tests — IsaacLab arena leaderboard."""
from __future__ import annotations

import json
import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lb(tmp_path):
    from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import Leaderboard
    return Leaderboard(path=tmp_path / "arena.json")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_78_metadata():
    """Metadata must show phase 78 and status == 'landed'."""
    from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 78
    assert md["status"] == "landed"


def test_submit_returns_entry_id_and_top_k_round_trips(tmp_path):
    """submit() returns a non-empty ID; top_k() retrieves the entry by scenario."""
    lb = _make_lb(tmp_path)
    eid = lb.submit("reach_cube", score=0.87, agent_name="agent_A")
    assert isinstance(eid, str) and len(eid) > 0

    results = lb.top_k("reach_cube", k=10)
    assert len(results) == 1
    assert results[0]["entry_id"] == eid
    assert results[0]["score"] == pytest.approx(0.87)
    assert results[0]["agent_name"] == "agent_A"


def test_top_k_sorted_descending_by_score(tmp_path):
    """top_k() must return entries sorted by score high→low."""
    lb = _make_lb(tmp_path)
    scores = [0.5, 0.9, 0.3, 0.7, 0.8]
    for i, s in enumerate(scores):
        lb.submit("reach_cube", score=s, agent_name=f"agent_{i}")

    top = lb.top_k("reach_cube", k=10)
    returned_scores = [e["score"] for e in top]
    assert returned_scores == sorted(returned_scores, reverse=True)
    assert returned_scores[0] == pytest.approx(0.9)


def test_multi_scenario_isolation(tmp_path):
    """Entries for different scenarios must not bleed into each other."""
    lb = _make_lb(tmp_path)
    lb.submit("reach_cube", score=0.9, agent_name="A")
    lb.submit("stack_blocks", score=0.6, agent_name="B")
    lb.submit("reach_cube", score=0.7, agent_name="C")

    reach = lb.all_for_scenario("reach_cube")
    stack = lb.all_for_scenario("stack_blocks")

    assert len(reach) == 2
    assert len(stack) == 1
    assert all(e["scenario_id"] == "reach_cube" for e in reach)
    assert stack[0]["agent_name"] == "B"


def test_k_larger_than_entries_returns_all(tmp_path):
    """top_k(k=100) when only 3 entries exist must return all 3."""
    lb = _make_lb(tmp_path)
    for i in range(3):
        lb.submit("reach_cube", score=float(i), agent_name=f"agent_{i}")

    results = lb.top_k("reach_cube", k=100)
    assert len(results) == 3


def test_atomic_write_produces_valid_json(tmp_path):
    """After submit(), the backing file must be valid JSON with an 'entries' key."""
    lb = _make_lb(tmp_path)
    arena_path = tmp_path / "arena.json"

    lb.submit("reach_cube", score=0.5, agent_name="tester", metadata={"env": "lab"})

    assert arena_path.exists()
    with open(arena_path) as fh:
        data = json.load(fh)
    assert "entries" in data
    assert len(data["entries"]) == 1
    assert data["entries"][0]["metadata"] == {"env": "lab"}


def test_list_scenarios(tmp_path):
    """list_scenarios() returns deduplicated scenario IDs in insertion order."""
    lb = _make_lb(tmp_path)
    lb.submit("reach_cube", score=0.9, agent_name="A")
    lb.submit("stack_blocks", score=0.6, agent_name="B")
    lb.submit("reach_cube", score=0.7, agent_name="C")
    lb.submit("navigate_hallway", score=0.8, agent_name="D")

    scenarios = lb.list_scenarios()
    assert scenarios == ["reach_cube", "stack_blocks", "navigate_hallway"]
