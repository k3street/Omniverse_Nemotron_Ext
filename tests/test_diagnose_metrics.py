"""Unit tests for diagnose/metrics.py — pure-Python metric scoring."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.diagnose.schema import Severity
from service.isaac_assist_service.diagnose import metrics as M


class TestIKFeasible:
    def test_success(self):
        v, s = M.metric_ik_feasible(ik_result={"success": True})
        assert v is True and s is None

    def test_failure_critical(self):
        v, s = M.metric_ik_feasible(ik_result={"success": False})
        assert v is False and s == Severity.CRITICAL

    def test_alt_key_ok(self):
        v, s = M.metric_ik_feasible(ik_result={"ok": True})
        assert v is True


class TestCollisionDistance:
    def test_in_collision_critical(self):
        v, s = M.metric_collision_distance(distance_m=-0.01)
        assert s == Severity.CRITICAL

    def test_too_close_error(self):
        v, s = M.metric_collision_distance(distance_m=0.003)
        assert s == Severity.ERROR

    def test_clear_no_violation(self):
        v, s = M.metric_collision_distance(distance_m=0.10)
        assert s is None

    def test_none_returns_none(self):
        v, s = M.metric_collision_distance(distance_m=None)
        assert v is None and s is None


class TestManipulability:
    def test_singular_warning(self):
        v, s = M.metric_manipulability(manip=0.02)
        assert s == Severity.WARNING

    def test_above_threshold_clean(self):
        v, s = M.metric_manipulability(manip=0.10)
        assert s is None


class TestReachUtilization:
    def test_within_reach(self):
        # robot at origin, pose at 0.4m from origin, max_reach=0.85
        v, s = M.metric_reach_utilization(pose=[0.4, 0, 0], robot_base=[0, 0, 0], max_reach=0.85)
        assert s is None
        assert 0.4 < v < 0.5

    def test_near_edge_warning(self):
        v, s = M.metric_reach_utilization(pose=[0.81, 0, 0], robot_base=[0, 0, 0], max_reach=0.85)
        assert s == Severity.WARNING
        assert v > 0.95

    def test_out_of_reach_critical(self):
        v, s = M.metric_reach_utilization(pose=[1.0, 0, 0], robot_base=[0, 0, 0], max_reach=0.85)
        assert s == Severity.CRITICAL
        assert v > 1.0


class TestInsideObstacleBbox:
    def test_pose_inside_bbox(self):
        bboxes = {"/Bin": {"min": [0, 0, 0], "max": [1, 1, 1]}}
        path, s = M.metric_inside_obstacle_bbox(pose=[0.5, 0.5, 0.5], obstacle_bboxes=bboxes)
        assert path == "/Bin"
        assert s == Severity.CRITICAL

    def test_pose_outside_bbox(self):
        bboxes = {"/Bin": {"min": [0, 0, 0], "max": [1, 1, 1]}}
        path, s = M.metric_inside_obstacle_bbox(pose=[2, 2, 2], obstacle_bboxes=bboxes)
        assert path is None
        assert s is None

    def test_empty_bboxes(self):
        path, s = M.metric_inside_obstacle_bbox(pose=[0, 0, 0], obstacle_bboxes={})
        assert path is None and s is None


class TestClearancePct:
    def test_full_clearance_no_violation(self):
        v, s = M.metric_clearance_pct(clear_count=20, total=20)
        assert v == 100.0
        assert s is None

    def test_partial_clearance_warning(self):
        # 17/20 = 85% — just below the 90% WARNING threshold (strict <)
        v, s = M.metric_clearance_pct(clear_count=17, total=20)
        assert s == Severity.WARNING
        assert v == 85.0

    def test_clearance_at_threshold_boundary(self):
        # 18/20 = 90% — equals threshold, NOT a violation (strict <)
        v, s = M.metric_clearance_pct(clear_count=18, total=20)
        assert v == 90.0
        assert s is None

    def test_blocked_error(self):
        v, s = M.metric_clearance_pct(clear_count=10, total=20)
        assert s == Severity.ERROR
        assert v == 50.0

    def test_zero_total(self):
        v, s = M.metric_clearance_pct(clear_count=0, total=0)
        assert v == 0.0
        assert s is None  # not enough data → no violation


class TestSensorZone:
    def test_cube_in_zone(self):
        ok, s = M.metric_cube_in_sensor_zone_at_settle(
            cube_xys=[[0.05, 0.05, 0.5]],
            sensor_xy=[0.0, 0.0],
            sensor_radius=0.1,
            k_factor=3.0,
        )
        assert ok is True and s is None

    def test_no_cube_in_zone_error(self):
        ok, s = M.metric_cube_in_sensor_zone_at_settle(
            cube_xys=[[5.0, 5.0, 0.5]],
            sensor_xy=[0.0, 0.0],
            sensor_radius=0.1,
            k_factor=3.0,
        )
        assert ok is False and s == Severity.ERROR

    def test_no_data_does_not_flag(self):
        ok, s = M.metric_cube_in_sensor_zone_at_settle(
            cube_xys=[],
            sensor_xy=[0.0, 0.0],
            sensor_radius=0.1,
        )
        assert ok is True and s is None


class TestMutexConflict:
    def test_overlap_no_mutex_error(self):
        a = {"min": [0, 0, 0], "max": [1, 1, 1]}
        b = {"min": [0.5, 0, 0], "max": [1.5, 1, 1]}
        v, s = M.metric_mutex_conflict(robot_a_corridor=a, robot_b_corridor=b, has_mutex=False)
        assert v is True and s == Severity.ERROR

    def test_overlap_with_mutex_clean(self):
        a = {"min": [0, 0, 0], "max": [1, 1, 1]}
        b = {"min": [0.5, 0, 0], "max": [1.5, 1, 1]}
        v, s = M.metric_mutex_conflict(robot_a_corridor=a, robot_b_corridor=b, has_mutex=True)
        assert v is False and s is None

    def test_no_overlap(self):
        a = {"min": [0, 0, 0], "max": [1, 1, 1]}
        b = {"min": [2, 2, 2], "max": [3, 3, 3]}
        v, s = M.metric_mutex_conflict(robot_a_corridor=a, robot_b_corridor=b, has_mutex=False)
        assert v is False and s is None
