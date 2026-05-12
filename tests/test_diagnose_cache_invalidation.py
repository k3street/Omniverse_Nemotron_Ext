"""Phase 49b — diagnose cache invalidation on stage revision change.

Without Phase 49b, repeated `diagnose_scene_feasibility` calls after a
`delete_prim` could return the stale verdict from cache (the args
hash didn't change, so the cache key didn't change either). Phase 49b
folds a stage-revision token into the key so any stage mutation
invalidates the previous entry.

These tests use a counter-based revision provider that the test
controls — no live USD stage required.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.diagnose import cache as dcache


class _Counter:
    """Test-only stage-revision provider. Bumps yield distinct keys."""

    def __init__(self, start: int = 0) -> None:
        self.value = start

    def __call__(self) -> str:
        return f"rev{self.value}"

    def bump(self) -> None:
        self.value += 1


@pytest.fixture
def revision_counter():
    """Installs a counter as the revision provider, cleans up on exit."""
    counter = _Counter()
    dcache.set_revision_provider(counter)
    dcache.clear_cache()
    try:
        yield counter
    finally:
        dcache.set_revision_provider(None)
        dcache.clear_cache()


@pytest.fixture
def no_provider():
    """Ensure no provider is set (verifies legacy fallback behaviour)."""
    dcache.set_revision_provider(None)
    dcache.clear_cache()
    try:
        yield
    finally:
        dcache.clear_cache()


# ---------------------------------------------------------------------------
# Core invariant: same args + same revision → same key


def test_same_args_same_revision_same_key(revision_counter):
    k1 = dcache.make_key(robot_path="/W/A", pick_pose=[0, 0, 0])
    k2 = dcache.make_key(robot_path="/W/A", pick_pose=[0, 0, 0])
    assert k1 == k2


def test_same_args_different_revision_different_key(revision_counter):
    k_before = dcache.make_key(robot_path="/W/A", pick_pose=[0, 0, 0])
    revision_counter.bump()
    k_after = dcache.make_key(robot_path="/W/A", pick_pose=[0, 0, 0])
    assert k_before != k_after, (
        "Phase 49b regression: cache key should change after stage "
        "revision bump even when args are identical."
    )


def test_different_args_different_key_regardless_of_revision(revision_counter):
    k_a = dcache.make_key(robot_path="/W/A", pick_pose=[0, 0, 0])
    k_b = dcache.make_key(robot_path="/W/B", pick_pose=[0, 0, 0])
    assert k_a != k_b


# ---------------------------------------------------------------------------
# End-to-end: put under one revision, get under a different revision → miss


def test_cache_miss_after_revision_bump(revision_counter):
    k1 = dcache.make_key(robot_path="/W/A")
    dcache.put(k1, {"verdict": "ok", "from": "rev0"})
    assert dcache.get(k1) is not None, "Sanity: same-revision lookup must hit"

    revision_counter.bump()
    k2 = dcache.make_key(robot_path="/W/A")
    assert k2 != k1
    assert dcache.get(k2) is None, (
        "Phase 49b: lookup after stage revision bump must MISS — the "
        "stale entry under k1 is now inaccessible."
    )


def test_stress_20_mutations_no_stale_hits(revision_counter):
    """Spec: '20 mutations interleaved with 20 cache calls — no stale hits.'

    Pattern: put@rev_i with verdict 'rev_i', bump revision, lookup with
    same args → MUST miss (would be a stale hit otherwise).
    """
    args = {"robot_path": "/W/Robot", "pick_pose": [0.1, 0.2, 0.3]}
    stale_hits = 0
    for i in range(20):
        k_i = dcache.make_key(**args)
        dcache.put(k_i, {"verdict": f"rev{i}", "i": i})
        # Mutate the stage
        revision_counter.bump()
        k_next = dcache.make_key(**args)
        hit = dcache.get(k_next)
        if hit is not None:
            stale_hits += 1
    assert stale_hits == 0, (
        f"Phase 49b stress: {stale_hits}/20 stale cache hits after "
        f"stage mutations. Expected 0."
    )


# ---------------------------------------------------------------------------
# Backwards compatibility: no provider registered → legacy behaviour


def test_no_provider_uses_legacy_key_shape(no_provider):
    """When no revision provider is set, get_stage_revision returns None
    and the key falls back to the pre-Phase-49b shape."""
    assert dcache.get_stage_revision() is None
    k1 = dcache.make_key(robot_path="/W/A")
    k2 = dcache.make_key(robot_path="/W/A")
    assert k1 == k2  # Same args = same key, exactly as before


def test_explicit_stage_revision_overrides_provider(revision_counter):
    """A caller passing stage_revision= explicitly overrides the provider."""
    k_from_provider = dcache.make_key(robot_path="/W/A")
    k_explicit = dcache.make_key(robot_path="/W/A", stage_revision="custom-token")
    # They differ because the explicit value differs from the provider's "rev0"
    assert k_from_provider != k_explicit


# ---------------------------------------------------------------------------
# Provider error handling


def test_provider_exception_falls_through_to_none(no_provider):
    """A misbehaving provider must not break the diagnose call path."""

    def broken_provider():
        raise RuntimeError("simulated stage-query failure")

    dcache.set_revision_provider(broken_provider)
    try:
        assert dcache.get_stage_revision() is None  # error → None
        # And make_key still produces a valid key (legacy shape)
        k = dcache.make_key(robot_path="/W/A")
        assert isinstance(k, str) and len(k) == 16
    finally:
        dcache.set_revision_provider(None)


def test_set_revision_provider_to_none_clears(revision_counter):
    """Explicitly setting provider to None clears it."""
    assert dcache.get_stage_revision() == "rev0"
    dcache.set_revision_provider(None)
    assert dcache.get_stage_revision() is None
