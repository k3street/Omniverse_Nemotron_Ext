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
@pytest.mark.xfail(reason="Known gap 2026-04-19: attr-first regex requires "
                   "value AFTER path, not before. 'height set to 2.0 on "
                   "/World/X' doesn't match because value-then-path phrasing "
                   "is neither branch. Widen the regex when a canary task "
                   "surfaces this as a real fabrication.")
def test_attr_value_before_path_phrasing_unsupported():
    E = _E()
    # This phrasing is natural English but neither regex branch catches it
    claims = E["attr"]("height set to 2.0 on /World/Cylinder")
    assert claims != []


@pytest.mark.xfail(reason="Known gap 2026-04-19 via AD-21: pose extractor matches "
                   "'rotated to (0, 90, 0)' as a pose claim and the orchestrator's "
                   "verify-contract (c) then cross-checks against get_world_transform's "
                   "TRANSLATION field — false positive because user claimed rotation. "
                   "Fix: either tag the claim with verb-kind (translation|rotation) or "
                   "add 'rotated' to an excluded list and dispatch rotation claims to "
                   "a separate check.")
def test_pose_rotation_verb_not_dispatched_to_translation_check():
    E = _E()
    # This SHOULDN'T be returned as a translation claim — "rotated to (0, 90, 0)"
    # is about rotation, not position. Until the extractor splits verb classes,
    # false positives in verify-contract (c) can occur on rotation-phrased replies.
    claims = E["pose"]("/World/X is rotated to (0, 90, 0)")
    # Desired future behavior: pose extractor returns [] for rotation-verb phrasings
    assert claims == []


@pytest.mark.xfail(reason="Known gap: count regex can span sentences. "
                   "'16 robots. /World/envs is empty.' matches even though "
                   "the robots and the path are logically separate. Tighten "
                   "the {0,200}? window or add a sentence-boundary guard "
                   "if this surfaces as a real false-positive.")
def test_count_sentence_boundary_not_enforced():
    E = _E()
    # Agent mentions 16 robots in one sentence, /World/envs in another —
    # current extractor links them, which can cause false-flag verification
    # of claims the agent never actually paired together.
    claims = E["count"]("we have 16 robots. /World/envs is empty.")
    # If this ever actually returns [], the behavior was tightened intentionally
    assert claims == []  # desired future behavior


# ── many-claim replies ────────────────────────────────────────────────
def test_many_claims_all_returned_by_extractor():
    """Extractor doesn't cap — cap is caller's concern. 10 distinct
    claims should all come out."""
    E = _E()
    lines = "\n".join(f"/World/env_{i} at ({i}, 0, 0)" for i in range(10))
    claims = E["pose"](lines)
    assert len(claims) == 10
