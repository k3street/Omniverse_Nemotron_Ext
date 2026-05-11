"""Edge-case / pathological-input tests for the verify-contract extractors.

The happy-path tests live in `test_orchestrator_claim_extractors.py` and
pin the normal behavior. This file stresses the extractors with inputs
that could silently break regex behavior: empty strings, huge strings,
unicode, mixed-case, adversarial near-matches, pathological repetition.

If any of these regress, the verify-contract silently misses claims that
agents make in production — the same class of bug as the 2026-04-19
discovery where count-claim verification was latently disabled because
of an optional path group.

L0 — no Kit, no network.
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
    )
    return {
        "count": _extract_count_claims,
        "pose": _extract_pose_claims,
        "schema": _extract_schema_claims,
        "attr": _extract_attr_claims,
    }


# ── empty / None inputs ────────────────────────────────────────────────
@pytest.mark.parametrize("inp", ["", " ", "\n", "\t\n\t", None])
def test_empty_inputs_return_empty_for_all_extractors(inp):
    E = _E()
    assert E["count"](inp) == []
    assert E["pose"](inp) == []
    assert E["schema"](inp) == []
    assert E["attr"](inp) == []


# ── pathological length (regex catastrophic backtracking check) ────────
def test_huge_input_without_claims_completes_fast():
    """Regex should not blow up on a 100kb string with no claims. The
    non-greedy {0,200}? quantifier limits backtracking by length."""
    import time
    E = _E()
    blob = "lorem ipsum dolor sit amet, consectetur. " * 2500  # ~100kb
    t0 = time.perf_counter()
    assert E["count"](blob) == []
    assert E["pose"](blob) == []
    assert E["schema"](blob) == []
    assert E["attr"](blob) == []
    # All four should finish within 500ms on 100kb input
    assert time.perf_counter() - t0 < 0.5


def test_huge_input_with_one_claim_still_finds_it():
    E = _E()
    filler = "x " * 5000
    reply = f"{filler} placed 8 cubes under /World/Grid {filler}"
    claims = E["count"](reply)
    assert (8, "cubes", "/World/Grid") in claims


# ── unicode / non-ASCII reply text ────────────────────────────────────
def test_unicode_in_reply_does_not_crash_extractors():
    """Swedish / emoji / CJK characters must not make any extractor
    raise — just produce empty results if no match."""
    E = _E()
    unicode_reply = "Jag placerade 3 kuber på /World/Grid — allt klart 🎉"
    # Count extractor uses english nouns, so won't match Swedish "kuber"
    assert E["count"](unicode_reply) == []
    # Pose/schema/attr should also not crash
    E["pose"](unicode_reply)
    E["schema"](unicode_reply)
    E["attr"](unicode_reply)


# ── case sensitivity ──────────────────────────────────────────────────
def test_count_is_case_insensitive():
    E = _E()
    assert E["count"]("PLACED 4 CUBES UNDER /World/Grid")[0][0] == 4


def test_attr_is_case_insensitive():
    E = _E()
    # "MASS" uppercase
    assert E["attr"]("/World/X MASS=1.0")[0][1] == "mass"


# ── adversarial near-matches ──────────────────────────────────────────
def test_count_non_world_path_not_matched():
    """Path must start with /World — /Render or /OmniverseKit shouldn't
    match (too easy to falsely flag system prims)."""
    E = _E()
    assert E["count"]("rendered 4 cubes under /Render/FrameBuffer") == []
    assert E["count"]("found 3 meshes at /OmniKit_Environment") == []


def test_pose_non_world_path_not_matched():
    E = _E()
    assert E["pose"]("/Render/Foo at (1, 2, 3)") == []


def test_attr_keyword_boundary():
    """_ATTR_WORDS regex uses \\b; substrings shouldn't match.
    "massive" contains "mass" but must not be flagged as a mass attr."""
    E = _E()
    # This SHOULD match — "mass" right before "="
    assert E["attr"]("/World/X mass=1.0") != []
    # This is subtle — "mass of /World/X is 1.0" SHOULD match attr-first
    assert E["attr"]("mass of /World/X is 1.0") != []
    # But "massive objects aren't lighter" — no path, no match
    assert E["attr"]("massive objects aren't lighter") == []


def test_schema_does_not_match_bare_api_word():
    """The regex requires the *API suffix after a named prefix; "API"
    alone or random PascalCase ending shouldn't produce a match."""
    E = _E()
    # Plain "API" reference with path — should NOT match because the
    # regex requires at least one alnum before "API"
    assert E["schema"]("The API documentation says /World/X supports REST") == []
    # But a real schema name like "CollisionAPI" applied to a path — YES
    assert E["schema"]("CollisionAPI applied to /World/X") != []


# ── float parsing edge cases ──────────────────────────────────────────
@pytest.mark.parametrize("val_str,expected", [
    ("1", 1.0),
    ("1.0", 1.0),
    ("-1.5", -1.5),
    ("0.0001", 0.0),  # rounds to 3dp
    ("9999", 9999.0),
])
def test_attr_numeric_value_variations(val_str, expected):
    E = _E()
    claims = E["attr"](f"/World/X mass={val_str}")
    assert claims != []
    assert claims[0][2] == expected


def test_pose_negative_coordinates():
    E = _E()
    c = E["pose"]("/World/X at (-1.5, -2.3, -0.1)")[0][1]
    assert c == (-1.5, -2.3, -0.1)


# ── regex boundary preservation ───────────────────────────────────────
def test_count_word_boundary_on_digit():
    """\\b on the \\d — "16000" shouldn't get split as "16" + "000 robots"."""
    E = _E()
    # "16000 robots" IS a valid count claim — \\d{1,4} allows up to 4 digits
    claims = E["count"]("running 16000 robots at /World/envs")
    # Wait, 16000 is 5 digits — \\d{1,4} won't match. Verify the cap works:
    assert claims == []
    # But 9999 (4 digits) should match
    claims = E["count"]("running 9999 robots at /World/envs")
    assert claims[0][0] == 9999


def test_pose_at_to_both_match_separately():
    """The pose regex pattern has multiple verbs ORed together. Each
    should match independently in its own reply."""
    E = _E()
    assert E["pose"]("/World/A at (1, 2, 3)") != []
    assert E["pose"]("/World/A to (1, 2, 3)") != []
    assert E["pose"]("/World/A positioned at (1, 2, 3)") != []
    assert E["pose"]("/World/A moved to (1, 2, 3)") != []


# ── known-limitation (xfail) tests pinning gaps for future widening ───
def test_attr_value_before_path_phrasing_supported():
    """2026-04-19 fix: added _ATTR_PAT_VAL_BEFORE_PATH as a third
    pattern covering attr → value → on → path phrasing ("height set
    to 2.0 on /World/Cylinder"). Previously neither path-first nor
    attr-first (val-after-path) regexes matched this natural phrasing,
    so the verify-contract (e) check silently ignored it — agent could
    false-claim this phrasing without triggering the structural guard."""
    E = _E()
    assert E["attr"]("height set to 2.0 on /World/Cylinder") == [
        ("/World/Cylinder", "height", 2.0)
    ]
    assert E["attr"]("friction is 0.8 for /World/Floor") == [
        ("/World/Floor", "friction", 0.8)
    ]
    assert E["attr"]("density of 5000 on /World/Concrete") == [
        ("/World/Concrete", "density", 5000.0)
    ]


def test_pose_rotation_verb_not_dispatched_to_translation_check():
    """2026-04-19 fix: pose extractor excludes rotation-verb phrasings.
    The translation verify-contract (c) path cross-checks against
    get_world_transform's translation field; if a rotation claim
    "rotated to (0, 90, 0)" matched as a translation claim, it would
    produce a false-positive mismatch warning.

    Fix: post-match filter drops any match whose span contains
    'rotate[d|ion|e]'. AD-21 is the canary for this regression."""
    E = _E()
    assert E["pose"]("/World/X is rotated to (0, 90, 0)") == []
    assert E["pose"]("/World/Y rotation set to (10, 20, 30)") == []
    assert E["pose"]("/World/Z rotated (0, 0, 0)") == []
    # But translation claims still work
    assert E["pose"]("/World/X at (1, 2, 3)") == [("/World/X", (1.0, 2.0, 3.0))]
    assert E["pose"]("/World/X moved to (0.5, 0.5, 0.5)") == [("/World/X", (0.5, 0.5, 0.5))]


def test_count_sentence_boundary_enforced():
    """2026-04-19 fix: _COUNT_PAT gap-fill char class is [^.\\n] so the
    regex can't cross a period. '16 robots. /World/envs is empty.' no
    longer produces a false-positive (16, 'robots', '/World/envs') link.

    Commas still work as intra-sentence separators:
    '16 robots, at /World/envs' still matches.
    """
    E = _E()
    assert E["count"]("we have 16 robots. /World/envs is empty.") == []
    # valid intra-sentence forms still match
    assert E["count"]("16 robots at /World/envs") == [(16, "robots", "/World/envs")]
    assert E["count"]("16 robots, at /World/envs") == [(16, "robots", "/World/envs")]
    # Multi-claim: first sentence's count+path should link, second shouldn't
    # bleed through the period boundary
    claims = E["count"]("cloned 16 clones into /World/A. 4 more at /World/B")
    assert (16, "clones", "/World/A") in claims
    # 4 more at /World/B is also a valid claim, and it sits AFTER the period,
    # so the regex should find it on a fresh scan (no leakage in either direction)


# ── many-claim replies ────────────────────────────────────────────────
def test_many_claims_all_returned_by_extractor():
    """Extractor doesn't cap — cap is caller's concern. 10 distinct
    claims should all come out."""
    E = _E()
    lines = "\n".join(f"/World/env_{i} at ({i}, 0, 0)" for i in range(10))
    claims = E["pose"](lines)
    assert len(claims) == 10
