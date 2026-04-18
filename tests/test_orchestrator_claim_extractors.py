"""Unit tests for the Fas-2 verify-contract claim extractors.

Extracted into pure helpers in orchestrator.py during the 2026-04-19
refactor so each branch (count, pose, schema, attribute) can be tested
in isolation without mocking the full orchestrator or Kit RPC.

L0 — no Kit, no network. Regex-only.

Coverage here pins the happy path plus the known edge cases we've
actually hit in the canary:
 - attr-first phrasing ("friction on /World/X is 0.8") — added to fix
   the regression surfaced in the AD-11 task on 2026-04-18
 - count without path is discarded — count-claim verification was
   latently broken for weeks because the path group was optional and
   the non-greedy quantifier preferred to drop it; the extractor fix
   flipped that and this test pins the new behavior
 - schema-prefix normalization across both path-first and schema-first
   phrasings
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _E():
    from service.isaac_assist_service.chat.orchestrator import (
        _extract_count_claims,
        _extract_pose_claims,
        _extract_schema_claims,
        _extract_attr_claims,
        _ATTR_NAME_MAP,
    )
    return {
        "count": _extract_count_claims,
        "pose": _extract_pose_claims,
        "schema": _extract_schema_claims,
        "attr": _extract_attr_claims,
        "attr_map": _ATTR_NAME_MAP,
    }


# ── count extractor ─────────────────────────────────────────────────────
def test_count_basic_plural_noun():
    f = _E()["count"]
    claims = f("cloned 16 robots under /World/envs")
    assert claims == [(16, "robots", "/World/envs")]


def test_count_without_path_is_dropped():
    """Pre-refactor this was the secret failure mode: path was optional
    so the regex always matched without a path, then the extractor
    discarded the match — count-claim verification was silently off."""
    f = _E()["count"]
    assert f("I found 16 robots but didn't place them") == []


def test_count_multiple_distinct_claims():
    f = _E()["count"]
    txt = "Placed 3 cubes under /World/Drop and 5 spheres under /World/Markers"
    claims = f(txt)
    assert (3, "cubes", "/World/Drop") in claims
    assert (5, "spheres", "/World/Markers") in claims


def test_count_dedup_on_same_n_and_path():
    f = _E()["count"]
    txt = "I see 4 cubes under /World/G. The 4 cubes under /World/G look fine."
    claims = f(txt)
    assert len(claims) == 1
    assert claims[0][0] == 4


def test_count_noun_list_includes_envs_environments():
    f = _E()["count"]
    assert f("Spawned 64 envs inside /World/Envs")[0] == (64, "envs", "/World/Envs")
    assert f("16 environments at /World/Gym")[0] == (16, "environments", "/World/Gym")


def test_count_none_for_non_matching_noun():
    f = _E()["count"]
    # "Franka" isn't in the noun list — no match
    assert f("spawned 16 Franka under /World/envs") == []


# ── pose extractor ─────────────────────────────────────────────────────
def test_pose_parenthesized_tuple():
    f = _E()["pose"]
    assert f("/World/Cube is at (1, 2, 3)") == [("/World/Cube", (1.0, 2.0, 3.0))]


def test_pose_no_parens():
    f = _E()["pose"]
    # Regex makes the parens optional — also matches "at 1, 2, 3"
    assert f("placed /World/A at 1, 2, 3") == [("/World/A", (1.0, 2.0, 3.0))]


def test_pose_multiple_verbs():
    f = _E()["pose"]
    assert f("moved /World/X to (0.5, 0.5, 0.1)")[0][1] == (0.5, 0.5, 0.1)
    assert f("positioned /World/Y at (1.0, 0, 0)")[0][1] == (1.0, 0.0, 0.0)
    assert f("located /World/Z at (0, 0, 2.5)")[0] == ("/World/Z", (0.0, 0.0, 2.5))


def test_pose_dedup_same_path_same_claim():
    f = _E()["pose"]
    txt = "/World/Cube at (1,2,3). /World/Cube at (1,2,3) confirmed."
    claims = f(txt)
    assert len(claims) == 1


def test_pose_rounds_to_three_dp():
    f = _E()["pose"]
    c = f("/World/A at (1.12345, 2.67890, 0.00001)")[0][1]
    # 3-dp rounding: 1.123, 2.679, 0.0
    assert c == (1.123, 2.679, 0.0)


# ── schema extractor ───────────────────────────────────────────────────
def test_schema_path_first_phrasing():
    f = _E()["schema"]
    assert f("/World/Cube has CollisionAPI") == [("CollisionAPI", "/World/Cube")]


def test_schema_schema_first_phrasing():
    f = _E()["schema"]
    assert f("applied RigidBodyAPI to /World/Cube") == [("RigidBodyAPI", "/World/Cube")]


def test_schema_prefix_stripped():
    f = _E()["schema"]
    # "UsdPhysics.MassAPI" should normalize to "MassAPI"
    assert f("UsdPhysics.MassAPI on /World/Arm") == [("MassAPI", "/World/Arm")]


def test_schema_punctuation_stripped():
    f = _E()["schema"]
    # trailing period/comma/etc must not leak into the schema name
    assert f("Applied CollisionAPI. to /World/Cube.")[0][0] == "CollisionAPI"


def test_schema_dedup_same_key():
    f = _E()["schema"]
    txt = "RigidBodyAPI on /World/C. Confirmed: RigidBodyAPI on /World/C."
    assert len(f(txt)) == 1


# ── attribute extractor ────────────────────────────────────────────────
def test_attr_path_first_equals():
    f = _E()["attr"]
    assert f("/World/Cube mass=1.0") == [("/World/Cube", "mass", 1.0)]


def test_attr_attr_first_is_phrasing():
    """The regression surfaced in AD-11 — attr-first phrasing was
    previously unmatched. Commit a880236 added the second regex."""
    f = _E()["attr"]
    assert f("mass on /World/Cube is 1.5") == [("/World/Cube", "mass", 1.5)]
    assert f("friction on /World/Floor is 0.8") == [("/World/Floor", "friction", 0.8)]


def test_attr_all_words():
    f = _E()["attr"]
    for word in ("mass", "friction", "restitution", "damping", "stiffness",
                 "radius", "height", "density", "size"):
        claims = f(f"/World/X {word}=1.0")
        assert claims == [("/World/X", word, 1.0)], f"missed word: {word}"


def test_attr_negative_value():
    f = _E()["attr"]
    assert f("/World/X mass=-0.5") == [("/World/X", "mass", -0.5)]


def test_attr_dedup_across_both_patterns():
    f = _E()["attr"]
    # Same logical claim via path-first and attr-first phrasing → dedup
    txt = "/World/C mass=1.5 — confirmed: mass on /World/C is 1.5"
    claims = f(txt)
    assert len(claims) == 1


def test_attr_name_map_covers_all_attr_words():
    attr_map = _E()["attr_map"]
    for word in ("mass", "friction", "restitution", "damping", "stiffness",
                 "radius", "height", "density", "size"):
        assert word in attr_map, f"_ATTR_NAME_MAP missing: {word}"


def test_attr_rounds_to_three_dp():
    f = _E()["attr"]
    claims = f("/World/X mass=1.23456")
    assert claims[0][2] == 1.235
