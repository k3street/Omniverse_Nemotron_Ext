"""CRM-A2 / CRM-B1 — compliance handlers: setup_admittance_controller,
setup_impedance_controller.

Implements `setup_admittance_controller` per CRM spec §5.1 (CRM-A2) and
`setup_impedance_controller` per §5.2 (CRM-B1).

Admittance step law (pure Python, no Kit required for dry-run):
    F = K·(x_desired - x_actual) - D·v_actual + F_ext

Impedance control law (pure Python, no Kit required for dry-run):
    τ = J^T · (Kx·Δx + Dx·v + Kr·Δr + Dr·ω)

Dry-run mode validates args and returns a config dict describing what the
controller would look like. Live mode raises NotImplementedError with a clear
actionable message directing the caller to provision the Kit RPC +
ros2_control bridge.

Per docs/specs/2026-05-11-contact-rich-manipulation-spec.md §5.1 (CRM-A2)
and §5.2 (CRM-B1).
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
# Impedance step law — pure Python


def _impedance_torque(
    Kx: List[float],
    Dx: List[float],
    Kr: List[float],
    Dr: List[float],
    delta_x: List[float],
    v_linear: List[float],
    delta_r: List[float],
    omega: List[float],
) -> List[float]:
    """Compute Cartesian impedance generalised torques per axis.

    τ = J^T · (Kx·Δx + Dx·v + Kr·Δr + Dr·ω)

    This function computes the Cartesian wrench components (3 translational +
    3 rotational) that the impedance controller would command. In a real
    deployment these are multiplied by the Jacobian transpose (J^T) to map to
    joint torques; that step requires live kinematics and is deferred to the
    Kit RPC layer.

    Args:
        Kx:        Cartesian translational stiffness per axis [N/m].
        Dx:        Cartesian translational damping per axis [N·s/m].
        Kr:        Rotational stiffness per axis [N·m/rad].
        Dr:        Rotational damping per axis [N·m·s/rad].
        delta_x:   Position error (x_desired − x_actual) per axis [m].
        v_linear:  End-effector linear velocity per axis [m/s].
        delta_r:   Orientation error per axis [rad].
        omega:     End-effector angular velocity per axis [rad/s].

    Returns:
        List[float] of 6 elements — [fx, fy, fz, tx, ty, tz] — representing
        the Cartesian wrench that J^T will map to joint torques.
    """
    n = len(Kx)
    translational = [Kx[i] * delta_x[i] + Dx[i] * v_linear[i] for i in range(n)]
    rotational = [Kr[i] * delta_r[i] + Dr[i] * omega[i] for i in range(len(Kr))]
    return translational + rotational


# ---------------------------------------------------------------------------
# Impedance handler


async def _handle_setup_impedance_controller(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch handler for `setup_impedance_controller` tool.

    Args (from tool call, all optional except robot_path):
        robot_path:           USD path to the robot articulation root.
        target_frame:         Tool frame name (default "tool0").
        Kx:                   Cartesian translational stiffness [N/m] (3-vector).
        Kr:                   Rotational stiffness [N·m/rad] (3-vector).
        Dx:                   Translational damping [N·s/m] (3-vector).
        Dr:                   Rotational damping [N·m·s/rad] (3-vector).
        null_space_stiffness: Null-space stiffness scalar (keeps arm near
                              rest configuration).
        null_space_damping:   Null-space damping scalar.
        torque_mode:          Must be True; if False returns error with
                              recommended_alternative="admittance".
        dry_run:              True → return config dict; False → Kit RPC.

    Returns dict with success: bool on all paths.
    Structured error on torque_mode=False: {success, error,
    recommended_alternative}.
    """
    robot_path: str = args.get("robot_path", "")
    if not robot_path:
        return {
            "success": False,
            "error": "robot_path is required",
        }

    torque_mode: bool = bool(args.get("torque_mode", True))
    if not torque_mode:
        return {
            "success": False,
            "error": (
                "setup_impedance_controller requires torque_mode=True. "
                "Impedance control maps Cartesian errors to joint torques "
                "(τ = J^T·(Kx·Δx + Dx·v + Kr·Δr + Dr·ω)) and therefore "
                "requires a torque-command interface (e.g. Franka FCI in "
                "libfranka). Your robot appears to be in position-mode only. "
                "Use setup_admittance_controller instead, which operates on "
                "position-mode robots with an external F/T sensor."
            ),
            "recommended_alternative": "admittance",
        }

    target_frame: str = args.get("target_frame", "tool0")
    Kx: List[float] = args.get("Kx", [400.0, 400.0, 400.0])
    Kr: List[float] = args.get("Kr", [40.0, 40.0, 40.0])
    Dx: List[float] = args.get("Dx", [40.0, 40.0, 40.0])
    Dr: List[float] = args.get("Dr", [4.0, 4.0, 4.0])
    null_space_stiffness: float = float(args.get("null_space_stiffness", 0.5))
    null_space_damping: float = float(args.get("null_space_damping", 0.5))
    dry_run: bool = bool(args.get("dry_run", True))

    if not dry_run:
        raise NotImplementedError(
            "setup_impedance_controller live mode requires Kit RPC + "
            "ros2_control bridge + torque-mode robot. "
            "Use dry_run=True to receive the config dict for offline inspection, "
            "or provision the bridge (CRM-A1) with a torque-capable robot and "
            "retry with dry_run=False."
        )

    result: Dict[str, Any] = {
        "success": True,
        "dry_run": True,
        "robot_path": robot_path,
        "target_frame": target_frame,
        "Kx": Kx,
        "Kr": Kr,
        "Dx": Dx,
        "Dr": Dr,
        "null_space_stiffness": null_space_stiffness,
        "null_space_damping": null_space_damping,
        "compliance_mode": "impedance",
        "torque_mode": True,
    }

    return result


# ---------------------------------------------------------------------------
# Public signature for setup_impedance_controller (matches §5.2 exactly)


async def setup_impedance_controller(
    robot_path: str,
    target_frame: str = "tool0",
    Kx: list[float] = [400.0]*3,
    Kr: list[float] = [40.0]*3,
    Dx: list[float] = [40.0]*3,
    Dr: list[float] = [4.0]*3,
    null_space_stiffness: float = 0.5,
    null_space_damping: float = 0.5,
    torque_mode: bool = True,
    dry_run: bool = True,
) -> dict:
    """Configure a Cartesian impedance controller for a torque-mode robot.

    Impedance control law: τ = J^T · (Kx·Δx + Dx·v + Kr·Δr + Dr·ω)

    Requires torque_mode=True; returns a structured error with
    recommended_alternative="admittance" if torque_mode=False.

    In dry-run mode (default) returns a config dict with all resolved
    parameters without touching Kit or ROS2.

    In live mode (dry_run=False) raises NotImplementedError until the
    Kit RPC + ros2_control bridge is provisioned.

    Args:
        robot_path:           USD path to the robot articulation root.
        target_frame:         Tool/end-effector frame name.
        Kx:                   Cartesian translational stiffness [N/m].
        Kr:                   Rotational stiffness [N·m/rad].
        Dx:                   Translational damping [N·s/m].
        Dr:                   Rotational damping [N·m·s/rad].
        null_space_stiffness: Null-space stiffness scalar.
        null_space_damping:   Null-space damping scalar.
        torque_mode:          Must be True for impedance control.
        dry_run:              If True, return config dict only (no Kit calls).

    Returns:
        dict with success: bool and controller config on success, or
        success: False + error: str + recommended_alternative: str when
        torque_mode=False.

    Raises:
        NotImplementedError: when dry_run=False (bridge not yet wired).
    """
    return await _handle_setup_impedance_controller({
        "robot_path": robot_path,
        "target_frame": target_frame,
        "Kx": Kx,
        "Kr": Kr,
        "Dx": Dx,
        "Dr": Dr,
        "null_space_stiffness": null_space_stiffness,
        "null_space_damping": null_space_damping,
        "torque_mode": torque_mode,
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
    data["setup_impedance_controller"] = _handle_setup_impedance_controller
