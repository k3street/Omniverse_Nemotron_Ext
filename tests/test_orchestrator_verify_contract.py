"""Unit tests for _partition_path_existence — the helper that powers
the Fas 2 verify-contract item (a) path check (orchestrator.py).

L0 — no Kit, no USD. Pure dict parsing.

Exercises the inversion-of-meaning gap closed on 2026-04-18:
the earlier version of the orchestrator used a dumb substring skip
on the tool_output_blob, so a path returned with ``exists=false``
got skipped for re-verification and the agent could falsely claim
it existed.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.l0


def _helper():
    from service.isaac_assist_service.chat.orchestrator import (
        _partition_path_existence,
    )
    return _partition_path_existence


def test_absent_prim_from_data_handler_json_output():
    """prim_exists data-handler returns output as a JSON string."""
    fn = _helper()
    tools = [
        {
            "tool": "prim_exists",
            "arguments": {"prim_path": "/World/Missing"},
            "result": {
                "output": json.dumps(
                    {"prim_path": "/World/Missing", "exists": False, "type_name": None}
                )
            },
        }
    ]
    present, absent = fn(tools)
    assert "/World/Missing" in absent
    assert "/World/Missing" not in present


def test_present_prim_from_data_handler_json_output():
    fn = _helper()
    tools = [
        {
            "tool": "prim_exists",
            "arguments": {"prim_path": "/World/Cube"},
            "result": {
                "output": json.dumps(
                    {"prim_path": "/World/Cube", "exists": True, "type_name": "Cube"}
                )
            },
        }
    ]
    present, absent = fn(tools)
    assert "/World/Cube" in present
    assert "/World/Cube" not in absent


def test_top_level_dict_payload_also_parsed():
    """Not all handlers wrap in output/json; some return direct dicts."""
    fn = _helper()
    tools = [
        {
            "tool": "prim_exists",
            "arguments": {"prim_path": "/World/X"},
            "result": {"prim_path": "/World/X", "exists": True},
        }
    ]
    present, absent = fn(tools)
    assert "/World/X" in present


def test_mixed_results_partitioned_correctly():
    fn = _helper()
    tools = [
        {"tool": "prim_exists", "result": {"output": json.dumps({"prim_path": "/World/A", "exists": True})}},
        {"tool": "prim_exists", "result": {"output": json.dumps({"prim_path": "/World/B", "exists": False})}},
        {"tool": "prim_exists", "result": {"output": json.dumps({"prim_path": "/World/C", "exists": True})}},
    ]
    present, absent = fn(tools)
    assert present == {"/World/A", "/World/C"}
    assert absent == {"/World/B"}


def test_no_evidence_means_empty_sets():
    """Tools that don't carry (prim_path, exists) pairs should not populate sets."""
    fn = _helper()
    tools = [
        {"tool": "scene_summary", "result": {"output": json.dumps({"total_prims": 42})}},
        {"tool": "get_timeline_state", "result": {"output": json.dumps({"is_playing": False})}},
    ]
    present, absent = fn(tools)
    assert present == set()
    assert absent == set()


def test_empty_and_none_inputs():
    fn = _helper()
    assert fn([]) == (set(), set())
    assert fn(None) == (set(), set())


def test_non_world_paths_ignored():
    """Helper filters to /World paths to avoid picking up /Render or other
    stage-metadata paths."""
    fn = _helper()
    tools = [
        {"tool": "prim_exists", "result": {"output": json.dumps({"prim_path": "/Render/Foo", "exists": True})}},
        {"tool": "prim_exists", "result": {"output": json.dumps({"prim_path": "/World/Real", "exists": True})}},
    ]
    present, absent = fn(tools)
    assert "/World/Real" in present
    assert "/Render/Foo" not in present
    assert "/Render/Foo" not in absent


def test_malformed_output_ignored_gracefully():
    """Invalid JSON in output shouldn't crash; top-level dict still parsed."""
    fn = _helper()
    tools = [
        {
            "tool": "prim_exists",
            "result": {
                "output": "not a json string at all {{",
                "prim_path": "/World/Fallback",
                "exists": True,
            },
        }
    ]
    present, absent = fn(tools)
    assert "/World/Fallback" in present


def test_inversion_of_meaning_scenario():
    """Reproduce the exact gap the fix closes: tool returned exists=false,
    agent claims the prim exists. Because the path appeared in tool_output
    (old dumb-substring check), it was skipped. Now: flagged as absent."""
    fn = _helper()
    tools = [
        {
            "tool": "prim_exists",
            "arguments": {"prim_path": "/World/GhostRobot"},
            "result": {
                "output": json.dumps(
                    {"prim_path": "/World/GhostRobot", "exists": False}
                )
            },
        }
    ]
    present, absent = fn(tools)
    assert "/World/GhostRobot" in absent, (
        "The confirmed_absent set must contain a path the tool said was missing. "
        "Without this, an agent claim that /World/GhostRobot exists would slip "
        "through verify-contract (a) because the dumb substring check used to see "
        "the path in the tool output blob and skip re-verification."
    )
