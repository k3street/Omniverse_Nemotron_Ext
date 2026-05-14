"""Tests for the 2026-04-19 user/system prim breakdown in scene_summary.

Pins the behavior where `format_stage_context_for_llm` must report total,
user-authored, and system prim counts separately. Before this fix the agent
would quote the total (e.g. "15 prims") to the user, who would push back
("it's 2 cubes and a light, not 15"). The system prims are /Render,
/OmniverseKit_*, /persistent — default stage machinery — and must not be
conflated with what the user created.

L0 — pure Python, no Kit.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.kit_tools import (
    _count_user_vs_system_prims,
    format_stage_context_for_llm,
)


def test_count_empty_tree():
    assert _count_user_vs_system_prims([]) == (0, 0)


def test_count_only_user_prims():
    tree = [{"path": "/World", "type": "Xform", "children": [
        {"path": "/World/Cube_1", "type": "Cube"},
        {"path": "/World/Cube_2", "type": "Cube"},
    ]}]
    u, s = _count_user_vs_system_prims(tree)
    assert u == 3
    assert s == 0


def test_count_only_system_prims():
    tree = [
        {"path": "/Render", "type": "Scope", "children": [
            {"path": "/Render/Vars", "type": "Scope"}
        ]},
        {"path": "/OmniverseKit_Persp", "type": "Camera"},
        {"path": "/persistent", "type": "Scope"},
    ]
    u, s = _count_user_vs_system_prims(tree)
    assert u == 0
    assert s == 4


def test_count_mixed_matches_session_scene():
    """The exact 15-prim count from the 2026-04-19 trace: 2 cubes + dome
    light = 3 user-authored — the other 12 are defaults."""
    tree = [
        {"path": "/World", "type": "Xform", "children": [
            {"path": "/World/Cube_1", "type": "Cube"},
            {"path": "/World/Cube_2", "type": "Cube"},
        ]},
        {"path": "/Environment", "type": "Xform", "children": [
            {"path": "/Environment/DomeLight", "type": "DomeLight"},
        ]},
        {"path": "/Render", "type": "Scope", "children": [
            {"path": "/Render/RenderProduct", "type": "RenderProduct"},
            {"path": "/Render/Vars", "type": "Scope"},
        ]},
        {"path": "/OmniverseKit_Persp", "type": "Camera"},
        {"path": "/OmniverseKit_Front", "type": "Camera"},
        {"path": "/OmniverseKit_Top", "type": "Camera"},
    ]
    u, s = _count_user_vs_system_prims(tree)
    # /World + 2 cubes + /Environment + DomeLight = 5 user
    # /Render + 2 children + 3 OmniverseKit_* cameras = 6 system
    assert u == 5
    assert s == 6


def test_format_reports_breakdown():
    """The formatted text must include the user/system split so the agent
    has the information when quoting counts to the user."""
    ctx = {"stage": {
        "stage_url": "anon:test",
        "prim_count": 10,
        "tree": [
            {"path": "/World", "type": "Xform", "children": [
                {"path": "/World/Cube", "type": "Cube"},
            ]},
            {"path": "/Render", "type": "Scope"},
            {"path": "/OmniverseKit_Persp", "type": "Camera"},
        ],
    }}
    out = format_stage_context_for_llm(ctx)
    assert "10 total" in out
    assert "user-authored" in out
    assert "default/system" in out


def test_format_omits_breakdown_when_tree_empty():
    """Edge case: if tree is missing or empty, just report the total."""
    ctx = {"stage": {"stage_url": "anon:test", "prim_count": 0}}
    out = format_stage_context_for_llm(ctx)
    assert "Prim count: 0" in out
    # Must not fabricate a 0 user / 0 system breakdown.
    assert "user-authored" not in out


def test_environment_counts_as_user():
    """/Environment is where Kit lands create_hdri_skydome output — user
    requested. Must NOT be filtered out as system."""
    tree = [{"path": "/Environment", "type": "Xform", "children": [
        {"path": "/Environment/DomeLight", "type": "DomeLight"},
    ]}]
    u, s = _count_user_vs_system_prims(tree)
    assert u == 2
    assert s == 0
