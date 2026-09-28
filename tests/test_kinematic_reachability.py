import math

import pytest
import torch

from scripts.adaptive_pick_place import (
    quaternion_error_axis_angle_wxyz,
    quaternion_multiply_wxyz,
)
from scripts.kinematic_reachability import (
    ProbePose,
    normalized_joint_margin,
    probe_pose_sequence,
    slerp_wxyz,
)


def axis_quaternion(axis: int, angle: float) -> torch.Tensor:
    quaternion = torch.zeros(4, dtype=torch.float64)
    quaternion[0] = math.cos(angle / 2.0)
    quaternion[1 + axis] = math.sin(angle / 2.0)
    return quaternion


class CartesianArm:
    """Three prismatic joints then ZYX rotations; an exact, invertible toy."""

    link_names = ("base", "wrist")

    def __init__(self, limits: torch.Tensor, *, refreshes: bool = True):
        self._limits = limits
        self._positions = torch.zeros(6, dtype=torch.float64)
        self._refreshes = refreshes
        self._reported = self._positions.clone()
        self.writes = 0

    def joint_positions(self) -> torch.Tensor:
        return self._positions.clone()

    def joint_limits(self) -> torch.Tensor:
        return self._limits.clone()

    def set_joint_positions(self, positions: torch.Tensor) -> None:
        self.writes += 1
        self._positions = positions.clone().to(torch.float64)
        if self._refreshes:
            self._reported = self._positions.clone()

    def _pose(self, q: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        quaternion = quaternion_multiply_wxyz(
            axis_quaternion(2, float(q[3])),
            quaternion_multiply_wxyz(
                axis_quaternion(1, float(q[4])), axis_quaternion(0, float(q[5]))
            ),
        )
        return q[:3].clone(), quaternion

    def eef_pose(self) -> tuple[torch.Tensor, torch.Tensor]:
        return self._pose(self._reported)

    def jacobian(self) -> torch.Tensor:
        epsilon = 1.0e-6
        base_xyz, base_quaternion = self._pose(self._positions)
        columns = []
        for joint in range(6):
            nudged = self._positions.clone()
            nudged[joint] += epsilon
            xyz, quaternion = self._pose(nudged)
            columns.append(
                torch.cat(
                    (
                        (xyz - base_xyz) / epsilon,
                        quaternion_error_axis_angle_wxyz(quaternion, base_quaternion)
                        / epsilon,
                    )
                )
            )
        return torch.stack(columns, dim=1)

    def link_positions(self) -> torch.Tensor:
        wrist, _ = self._pose(self._reported)
        return torch.stack((torch.zeros(3, dtype=torch.float64), wrist))


LIMITS = torch.tensor(
    [[-0.5, 0.5], [-0.5, 0.5], [-0.2, 0.6], [-2.5, 2.5], [-1.2, 1.2], [-2.5, 2.5]],
    dtype=torch.float64,
)
IDENTITY = torch.tensor([1.0, 0.0, 0.0, 0.0], dtype=torch.float64)


def pose(name: str, xyz, quaternion=IDENTITY) -> ProbePose:
    return ProbePose(name, torch.tensor(xyz, dtype=torch.float64), quaternion)


def test_reachable_sequence_is_solved_and_the_arm_is_put_back():
    arm = CartesianArm(LIMITS)
    arm.set_joint_positions(torch.tensor([0.1, -0.1, 0.2, 0.0, 0.0, 0.0]))
    original = arm.joint_positions()

    result = probe_pose_sequence(
        arm,
        [
            pose("approach", [0.3, 0.1, 0.3], axis_quaternion(2, 0.6)),
            pose("grasp", [0.3, 0.1, 0.1], axis_quaternion(2, 0.6)),
            pose("lift", [0.3, 0.1, 0.4], axis_quaternion(2, 0.6)),
        ],
    )

    assert result["status"] == "evaluated"
    assert result["all_reachable"] is True
    assert [record["reachable"] for record in result["poses"]] == [True, True, True]
    for record in result["poses"]:
        assert record["position_error_m"] <= 0.005
        assert record["orientation_error_deg"] <= 3.0
    assert result["restored"] is True
    assert result["physics_stepped"] is False
    assert torch.equal(arm.joint_positions(), original)


def test_a_pose_past_a_joint_limit_is_unreachable_and_later_poses_are_not_guessed():
    arm = CartesianArm(LIMITS)

    result = probe_pose_sequence(
        arm,
        [
            pose("approach", [0.2, 0.0, 0.3]),
            pose("place", [0.9, 0.0, 0.3]),
            pose("retreat", [0.2, 0.0, 0.4]),
        ],
    )

    reachable = [record["reachable"] for record in result["poses"]]
    assert reachable == [True, False, None]
    assert result["all_reachable"] is False
    assert "failed_at_waypoint" in result["poses"][1]
    assert result["poses"][1]["minimum_normalized_joint_limit_margin_along_path"] < 0.01
    assert "not_evaluated_reason" in result["poses"][2]
    assert result["restored"] is True


def test_an_adapter_whose_writes_do_not_move_the_eef_is_refused():
    arm = CartesianArm(LIMITS, refreshes=False)
    original = arm.joint_positions()

    result = probe_pose_sequence(arm, [pose("grasp", [0.2, 0.0, 0.1])])

    assert result["status"] == "unavailable"
    assert "did not move" in result["unavailable_reason"]
    assert result["poses"] == []
    assert torch.equal(arm.joint_positions(), original)


def test_lowest_link_height_is_measured_along_the_path_not_only_at_the_end():
    arm = CartesianArm(LIMITS)
    arm.set_joint_positions(torch.tensor([0.0, 0.0, 0.3, 0.0, 0.0, 0.0]))

    result = probe_pose_sequence(
        arm,
        [pose("dip", [0.0, 0.0, -0.1]), pose("rise", [0.0, 0.0, 0.3])],
        support_height_m=0.0,
    )

    dip, rise = result["poses"]
    assert dip["minimum_link_origin_height_above_support_m"] == pytest.approx(-0.1, abs=0.006)
    assert dip["lowest_link_along_path"] == "wrist"
    # The rise starts at the bottom of the dip, so its path minimum is there too.
    assert rise["minimum_link_origin_height_above_support_m"] < 0.0


def test_the_arm_is_restored_even_when_the_solver_raises():
    class BrokenJacobian(CartesianArm):
        def jacobian(self) -> torch.Tensor:
            raise RuntimeError("tensor view lost")

    arm = BrokenJacobian(LIMITS)
    arm.set_joint_positions(torch.tensor([0.1, 0.0, 0.2, 0.0, 0.0, 0.0]))
    original = arm.joint_positions()

    with pytest.raises(RuntimeError, match="tensor view lost"):
        probe_pose_sequence(arm, [pose("grasp", [0.3, 0.0, 0.1])])

    assert torch.equal(arm.joint_positions(), original)


def test_joint_margin_is_the_nearest_limit_as_a_fraction_of_range():
    limits = torch.tensor([[-1.0, 1.0], [0.0, 4.0]])
    assert normalized_joint_margin(torch.tensor([0.0, 3.0]), limits) == pytest.approx(0.25)


def test_slerp_takes_the_short_way_round():
    start = axis_quaternion(2, 0.2)
    end = -axis_quaternion(2, 0.8)
    halfway = slerp_wxyz(start, end, 0.5)
    angle = torch.linalg.vector_norm(quaternion_error_axis_angle_wxyz(halfway, IDENTITY))
    assert float(angle) == pytest.approx(0.5, abs=1.0e-6)
