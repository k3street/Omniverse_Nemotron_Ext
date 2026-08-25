import pytest
import torch

from scripts.residual_centering import (
    bounded_scalar_step,
    bounded_xy_step,
    damped_least_squares_delta,
)


def test_bounded_xy_step_preserves_direction_and_norm_limit():
    error = torch.tensor([0.03, 0.04])
    step = bounded_xy_step(error, 0.02)
    assert torch.linalg.vector_norm(step).item() == pytest.approx(0.02)
    assert step[0].item() / step[1].item() == pytest.approx(0.75)


def test_bounded_xy_step_keeps_small_error_unchanged():
    error = torch.tensor([0.003, -0.004])
    assert torch.equal(bounded_xy_step(error, 0.02), error)


def test_bounded_scalar_step_clamps_without_changing_direction():
    assert bounded_scalar_step(torch.tensor(-0.06), 0.015).item() == pytest.approx(-0.015)
    assert bounded_scalar_step(torch.tensor(0.004), 0.015).item() == pytest.approx(0.004)


def test_damped_least_squares_maps_cartesian_step_and_bounds_joints():
    jacobian = torch.zeros((6, 7))
    jacobian[:, :6] = torch.eye(6)
    desired = torch.tensor([0.02, -0.01, 0.0, 0.0, 0.0, 0.0])
    delta_joint = damped_least_squares_delta(
        jacobian, desired, damping=0.01, max_joint_step_rad=0.015
    )
    assert torch.max(torch.abs(delta_joint)).item() == pytest.approx(0.015)
    assert delta_joint[0].item() > 0
    assert delta_joint[1].item() < 0
    assert delta_joint[6].item() == pytest.approx(0.0)


def test_residual_helpers_reject_non_finite_inputs():
    with pytest.raises(ValueError, match="non-finite"):
        bounded_xy_step(torch.tensor([float("nan"), 0.0]), 0.02)
    with pytest.raises(ValueError, match="non-finite"):
        damped_least_squares_delta(
            torch.full((6, 7), float("nan")),
            torch.zeros(6),
            damping=0.05,
            max_joint_step_rad=0.08,
        )
