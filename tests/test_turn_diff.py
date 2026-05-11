"""Tests for turn_diff — the snapshot-diff verify-contract primitive.

turn_diff is deliberately STRUCTURAL and LANGUAGE-AGNOSTIC. No verb regex,
no noun-class classification. It answers one question: given a set of
paths the caller found in the reply, which ones do NOT appear in the
stage diff? Those are the unsubstantiated claims.

Path extraction + intent classification live upstream (orchestrator's
existing /World/... regex + bare-name extractor, and the LLM-based
intent_router). This module is a leaf.

L0 — pure Python, no Kit, no network.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.turn_diff import (
    TurnDiff,
    unsubstantiated_paths,
)


# ── unsubstantiated_paths ──────────────────────────────────────────────
class TestUnsubstantiatedPaths:
    """Pins the 2026-04-19 fabrication scenario: agent claims cubes were
    scaled+placed, but the only stage change was a new conveyor prim.
    /World/Cube1 etc must surface as unsubstantiated."""

    def test_mentioned_but_not_in_diff_flagged(self):
        # Only /World/ConveyorBelt was actually added
        diff = TurnDiff(
            ok=True,
            added={"/World/ConveyorBelt"},
            modified={},
        )
        mentioned = {"/World/Cube1", "/World/Cube2", "/World/ConveyorBelt"}
        unsub = unsubstantiated_paths(mentioned, diff)
        assert "/World/Cube1" in unsub
        assert "/World/Cube2" in unsub
        assert "/World/ConveyorBelt" not in unsub

    def test_mentioned_and_added_is_substantiated(self):
        diff = TurnDiff(ok=True, added={"/World/Cube1"})
        assert unsubstantiated_paths({"/World/Cube1"}, diff) == []

    def test_mentioned_and_modified_is_substantiated(self):
        diff = TurnDiff(ok=True, modified={"/World/Cube1": ["size"]})
        assert unsubstantiated_paths({"/World/Cube1"}, diff) == []

    def test_mentioned_and_removed_is_substantiated(self):
        """Valid 'I deleted /World/Cube3' claim — path in removed set."""
        diff = TurnDiff(ok=True, removed={"/World/Cube3"})
        assert unsubstantiated_paths({"/World/Cube3"}, diff) == []

    def test_empty_mentions_is_silent(self):
        diff = TurnDiff(ok=True, modified={"/World/X": ["a"]})
        assert unsubstantiated_paths(set(), diff) == []

    def test_diff_not_ok_is_silent(self):
        """If diff failed to compute, no evidence either way → don't cry wolf."""
        failed = TurnDiff(ok=False, error="no stage")
        assert unsubstantiated_paths({"/World/Any"}, failed) == []

    def test_returns_sorted_for_determinism(self):
        diff = TurnDiff(ok=True)
        unsub = unsubstantiated_paths({"/World/Z", "/World/A", "/World/M"}, diff)
        assert unsub == ["/World/A", "/World/M", "/World/Z"]


# ── TurnDiff dataclass helpers ─────────────────────────────────────────
class TestTurnDiffHelpers:
    def test_total_changes_sums_categories(self):
        d = TurnDiff(
            ok=True,
            added={"/A", "/B"},
            removed={"/C"},
            modified={"/D": ["x"], "/E": ["y"]},
        )
        assert d.total_changes == 5

    def test_paths_under_filters_by_prefix(self):
        d = TurnDiff(
            ok=True,
            added={"/World/Cube1", "/World/Robot", "/Render/Vars"},
            modified={"/World/Cube2": ["x"]},
        )
        under_world = d.paths_under("/World")
        assert "/World/Cube1" in under_world
        assert "/World/Robot" in under_world
        assert "/World/Cube2" in under_world
        assert "/Render/Vars" not in under_world

    def test_empty_diff_has_zero_changes(self):
        assert TurnDiff(ok=True).total_changes == 0
