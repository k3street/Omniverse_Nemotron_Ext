"""Unit tests for diagnose/cache.py — TTL + invalidation."""
from __future__ import annotations

import time
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.diagnose import cache as dcache


@pytest.fixture(autouse=True)
def _clear_between_tests():
    dcache.clear_cache()
    yield
    dcache.clear_cache()


class TestKeyBuilding:
    def test_same_inputs_yield_same_key(self):
        k1 = dcache.make_key(robot_path="/R", pick_pose=[1, 2, 3], drop_pose=[4, 5, 6], seed=42)
        k2 = dcache.make_key(robot_path="/R", pick_pose=[1, 2, 3], drop_pose=[4, 5, 6], seed=42)
        assert k1 == k2

    def test_different_seed_yields_different_key(self):
        k1 = dcache.make_key(robot_path="/R", pick_pose=[1, 2, 3], drop_pose=[4, 5, 6], seed=42)
        k2 = dcache.make_key(robot_path="/R", pick_pose=[1, 2, 3], drop_pose=[4, 5, 6], seed=43)
        assert k1 != k2

    def test_different_pose_yields_different_key(self):
        k1 = dcache.make_key(robot_path="/R", pick_pose=[1, 2, 3], drop_pose=[4, 5, 6])
        k2 = dcache.make_key(robot_path="/R", pick_pose=[1.5, 2, 3], drop_pose=[4, 5, 6])
        assert k1 != k2

    def test_obstacle_bboxes_in_key(self):
        k1 = dcache.make_key(robot_path="/R", obstacle_bboxes={"/Pillar": {"min": [0,0,0], "max": [1,1,1]}})
        k2 = dcache.make_key(robot_path="/R", obstacle_bboxes={"/Pillar": {"min": [0,0,0], "max": [2,2,2]}})
        assert k1 != k2


class TestPutGet:
    def test_put_then_get(self):
        k = dcache.make_key(robot_path="/R", seed=42)
        dcache.put(k, {"verdict": "feasible"})
        out = dcache.get(k)
        assert out is not None
        assert out["verdict"] == "feasible"

    def test_miss_returns_none(self):
        assert dcache.get("nonexistent") is None

    def test_ttl_expiry(self):
        k = dcache.make_key(robot_path="/R", seed=42)
        dcache.put(k, {"verdict": "feasible"})
        # Force-expire by manipulating the stored timestamp
        payload, _ = dcache._cache[k]
        dcache._cache[k] = (payload, time.time() - 120)
        assert dcache.get(k, ttl_s=60) is None

    def test_ttl_within_window(self):
        k = dcache.make_key(robot_path="/R", seed=42)
        dcache.put(k, {"verdict": "feasible"})
        assert dcache.get(k, ttl_s=60) is not None


class TestClearAndInvalidate:
    def test_clear_returns_count(self):
        for i in range(3):
            dcache.put(f"key{i}", {"i": i})
        assert dcache.clear_cache() == 3
        assert dcache.stats()["size"] == 0

    def test_invalidate_prefix(self):
        dcache.put("aaa1", {"x": 1})
        dcache.put("aaa2", {"x": 2})
        dcache.put("bbb1", {"x": 3})
        n = dcache.invalidate_prefix("aaa")
        assert n == 2
        assert dcache.get("aaa1") is None
        assert dcache.get("bbb1") is not None


class TestStats:
    def test_empty(self):
        assert dcache.stats() == {"size": 0, "oldest_age_s": None}

    def test_size_after_puts(self):
        dcache.put("a", {"v": 1})
        dcache.put("b", {"v": 2})
        s = dcache.stats()
        assert s["size"] == 2
        assert s["oldest_age_s"] is not None
        assert s["oldest_age_s"] >= 0


class TestMutateGeometryToolsSet:
    def test_contains_known_mutators(self):
        # Sanity: the set is populated with realistic tool names
        assert "set_attribute" in dcache.MUTATE_GEOMETRY_TOOLS
        assert "translate" in dcache.MUTATE_GEOMETRY_TOOLS
        assert "execute_template_canonical" in dcache.MUTATE_GEOMETRY_TOOLS
        assert "open_stage" in dcache.MUTATE_GEOMETRY_TOOLS
