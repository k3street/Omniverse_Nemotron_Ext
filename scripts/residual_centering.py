"""Pure tensor helpers for bounded Cartesian residual correction."""
from __future__ import annotations

import torch


def bounded_xy_step(error_xy: torch.Tensor, max_step_m: float) -> torch.Tensor:
    """Return an XY correction with preserved direction and bounded norm."""
    if error_xy.shape != (2,):
        raise ValueError(f"expected XY error shape (2,), got {tuple(error_xy.shape)}")
    if max_step_m <= 0:
        raise ValueError("max_step_m must be positive")
    if not bool(torch.isfinite(error_xy).all()):
        raise ValueError("XY error contains a non-finite value")
    norm = torch.linalg.vector_norm(error_xy)
    if float(norm) <= max_step_m:
        return error_xy.clone()
    return error_xy * (max_step_m / norm)


def bounded_scalar_step(error: torch.Tensor, max_step: float) -> torch.Tensor:
    """Clamp a scalar residual without changing its sign."""
    if error.numel() != 1:
        raise ValueError(f"expected one scalar residual, got shape {tuple(error.shape)}")
    if max_step <= 0:
        raise ValueError("max_step must be positive")
    if not bool(torch.isfinite(error).all()):
        raise ValueError("scalar error contains a non-finite value")
    return torch.clamp(error, min=-max_step, max=max_step)


def damped_least_squares_delta(
    jacobian: torch.Tensor,
    cartesian_delta: torch.Tensor,
    damping: float,
    max_joint_step_rad: float,
) -> torch.Tensor:
    """Map a Cartesian residual to a direction-preserving bounded joint step."""
    if jacobian.ndim != 2 or jacobian.shape[0] != cartesian_delta.numel():
        raise ValueError(
            f"jacobian/delta mismatch: {tuple(jacobian.shape)} vs {tuple(cartesian_delta.shape)}"
        )
    if damping <= 0 or max_joint_step_rad <= 0:
        raise ValueError("damping and max_joint_step_rad must be positive")
    if not bool(torch.isfinite(jacobian).all()) or not bool(torch.isfinite(cartesian_delta).all()):
        raise ValueError("Jacobian or Cartesian residual contains a non-finite value")

    jacobian_t = jacobian.mT
    regularizer = (damping**2) * torch.eye(
        jacobian.shape[0], dtype=jacobian.dtype, device=jacobian.device
    )
    delta_joint = jacobian_t @ torch.linalg.solve(
        jacobian @ jacobian_t + regularizer, cartesian_delta
    )
    largest = torch.max(torch.abs(delta_joint))
    if float(largest) > max_joint_step_rad:
        delta_joint = delta_joint * (max_joint_step_rad / largest)
    return delta_joint
