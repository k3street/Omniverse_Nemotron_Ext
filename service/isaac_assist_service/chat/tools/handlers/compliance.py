"""CRM-A2 — compliance handler: setup_admittance_controller.

Implements the `setup_admittance_controller` tool per the CRM spec §5.1.

Admittance step law (pure Python, no Kit required for dry-run):
    F = K·(x_desired - x_actual) - D·v_actual + F_ext

Dry-run mode validates args and returns a config dict describing what the
controller would look like. Live mode raises NotImplementedError with a clear
actionable message directing the caller to provision the Kit RPC +
ros2_control bridge.

Per docs/specs/2026-05-11-contact-rich-manipulation-spec.md §5.1 (CRM-A2).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Admittance step law — pure Python


def _admittance_step(
    K: List[float],
    D: List[float],
    x_desired: List[float],
    x_actual: List[float],
    v_actual: List[float],
    F_ext: Optional[List[float]] = None,
) -> List[float]:
    """Compute one admittance control step per axis.

    F = K·(x_desired - x_actual) - D·v_actual + F_ext

    Args:
        K:         Stiffness gains [Kx, Ky, Kz] (N/m or N·m/rad).
        D:         Damping gains  [Dx, Dy, Dz] (N·s/m or N·m·s/rad).
        x_desired: Desired position/orientation per axis.
        x_actual:  Actual  position/orientation per axis.
        v_actual:  Actual  velocity per axis.
        F_ext:     External force per axis (default all-zeros).

    Returns:
        List[float] — control force per axis.
    """
    n = len(K)
    if F_ext is None:
        F_ext = [0.0] * n
    return [
        K[i] * (x_desired[i] - x_actual[i]) - D[i] * v_actual[i] + F_ext[i]
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Handler


async def _handle_setup_admittance_controller(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch handler for `setup_admittance_controller` tool.

    Args (from tool call, all optional except robot_path):
        robot_path:          USD path to the robot root.
        target_frame:        Tool frame name (default "tool0").
        mass_xyz:            Virtual mass per translational axis [kg].
        stiffness_xyz:       Translational stiffness [N/m].
        damping_xyz:         Translational damping [N·s/m].
        mass_rot:            Virtual mass per rotational axis [kg·m²].
        stiffness_rot:       Rotational stiffness [N·m/rad].
        damping_rot:         Rotational damping [N·m·s/rad].
        ft_sensor_path:      USD path to F/T sensor (optional).
        chain_after:         Controller to chain before admittance.
        dry_run:             True → return config dict; False → Kit RPC.

    Returns dict with success: bool on all paths.
    """
    robot_path: str = args.get("robot_path", "")
    if not robot_path:
        return {
            "success": False,
            "error": "robot_path is required",
        }

    target_frame: str = args.get("target_frame", "tool0")
    mass_xyz: List[float] = args.get("mass_xyz", [1.0, 1.0, 1.0])
    stiffness_xyz: List[float] = args.get("stiffness_xyz", [500.0, 500.0, 500.0])
    damping_xyz: List[float] = args.get("damping_xyz", [50.0, 50.0, 50.0])
    mass_rot: List[float] = args.get("mass_rot", [0.1, 0.1, 0.1])
    stiffness_rot: List[float] = args.get("stiffness_rot", [50.0, 50.0, 50.0])
    damping_rot: List[float] = args.get("damping_rot", [5.0, 5.0, 5.0])
    ft_sensor_path: Optional[str] = args.get("ft_sensor_path", None)
    chain_after: str = args.get("chain_after", "joint_trajectory_controller")
    dry_run: bool = bool(args.get("dry_run", True))

    # --- param validation ---
    for label, vec in (
        ("stiffness_xyz", stiffness_xyz),
        ("stiffness_rot", stiffness_rot),
    ):
        if not all(v > 0 for v in vec):
            return {
                "success": False,
                "error": (
                    f"{label} values must all be positive (got {vec}). "
                    "Non-positive stiffness produces unstable admittance behaviour."
                ),
            }

    for label, vec in (
        ("damping_xyz", damping_xyz),
        ("damping_rot", damping_rot),
    ):
        if not all(v >= 0 for v in vec):
            return {
                "success": False,
                "error": (
                    f"{label} values must be non-negative (got {vec}). "
                    "Negative damping is physically inadmissible."
                ),
            }

    if not dry_run:
        raise NotImplementedError(
            "setup_admittance_controller live mode requires Kit RPC + "
            "ros2_control bridge (see CRM-A1 bridge + ros2_control_bridge.py). "
            "Use dry_run=True to receive the config dict for offline inspection, "
            "or provision the bridge and retry with dry_run=False."
        )

    # --- dry-run: build and return config dict ---
    controller_chain = [chain_after, "admittance_controller"]

    result: Dict[str, Any] = {
        "success": True,
        "dry_run": True,
        "robot_path": robot_path,
        "target_frame": target_frame,
        "mass_xyz": mass_xyz,
        "stiffness_xyz": stiffness_xyz,
        "damping_xyz": damping_xyz,
        "mass_rot": mass_rot,
        "stiffness_rot": stiffness_rot,
        "damping_rot": damping_rot,
        "compliance_mode": "admittance",
        "controller_chain": controller_chain,
    }

    if ft_sensor_path is not None:
        result["ft_sensor_path"] = ft_sensor_path

    return result


# ---------------------------------------------------------------------------
# Public signature (matches §5.1 exactly — used by higher-level callers)


async def setup_admittance_controller(
    robot_path: str,                    # "/World/Franka"
    target_frame: str = "tool0",
    mass_xyz: list[float] = [1.0]*3,
    stiffness_xyz: list[float] = [500.0]*3,
    damping_xyz: list[float] = [50.0]*3,
    mass_rot: list[float] = [0.1]*3,
    stiffness_rot: list[float] = [50.0]*3,
    damping_rot: list[float] = [5.0]*3,
    ft_sensor_path: str | None = None,
    chain_after: str = "joint_trajectory_controller",
    dry_run: bool = True,
) -> dict:
    """Configure an admittance controller for a robot.

    Admittance step law: F = K·(x_desired - x_actual) - D·v_actual + F_ext

    In dry-run mode (default) returns a config dict with all resolved
    parameters and the controller chain, without touching Kit or ROS2.

    In live mode (dry_run=False) raises NotImplementedError until the
    Kit RPC + ros2_control bridge is provisioned.

    Args:
        robot_path:     USD path to the robot articulation root.
        target_frame:   Tool/end-effector frame name.
        mass_xyz:       Virtual mass for each translational axis [kg].
        stiffness_xyz:  Translational spring stiffness [N/m].
        damping_xyz:    Translational damping coefficient [N·s/m].
        mass_rot:       Virtual inertia for each rotational axis [kg·m²].
        stiffness_rot:  Rotational spring stiffness [N·m/rad].
        damping_rot:    Rotational damping coefficient [N·m·s/rad].
        ft_sensor_path: Optional USD path to the force/torque sensor prim.
        chain_after:    ros2_control controller that runs before admittance.
        dry_run:        If True, return config dict only (no Kit calls).

    Returns:
        dict with success: bool and controller config on success, or
        success: False + error: str on validation failure.

    Raises:
        NotImplementedError: when dry_run=False (bridge not yet wired).
    """
    return await _handle_setup_admittance_controller({
        "robot_path": robot_path,
        "target_frame": target_frame,
        "mass_xyz": mass_xyz,
        "stiffness_xyz": stiffness_xyz,
        "damping_xyz": damping_xyz,
        "mass_rot": mass_rot,
        "stiffness_rot": stiffness_rot,
        "damping_rot": damping_rot,
        "ft_sensor_path": ft_sensor_path,
        "chain_after": chain_after,
        "dry_run": dry_run,
    })


# ---------------------------------------------------------------------------
# Dispatch registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Register compliance handlers into the dispatch table."""
    data["setup_admittance_controller"] = _handle_setup_admittance_controller
