"""CRM-A2 / CRM-B1 / CRM-B2 / CRM-B3 / CRM-C4 — compliance handlers.

Implements:
- `setup_admittance_controller` per CRM spec §5.1 (CRM-A2)
- `setup_impedance_controller` per §5.2 (CRM-B1)
- `set_compliance_params` per §5.3 (CRM-B2)
- `release_compliance` per §5.4 (CRM-B3)
- `follow_trajectory_with_compliance` per §5.5 (CRM-C4)

Admittance step law (pure Python, no Kit required for dry-run):
    F = K·(x_desired - x_actual) - D·v_actual + F_ext

Impedance control law (pure Python, no Kit required for dry-run):
    τ = J^T · (Kx·Δx + Dx·v + Kr·Δr + Dr·ω)

Trajectory-with-compliance bridge (CRM-C4) consumes Phase 63b's
`plan_constrained_trajectory` output (a list of waypoint dicts) plus a
`compliance_handoff_at` fraction; from t=0 to t=handoff_at the trajectory
waypoints are followed as rigid joint targets, and from t=handoff_at to
t=1 the compliance controller takes over (trajectory targets become the
"desired pose" reference, F/T feedback drives actual motion).

Dry-run mode validates args and returns a config dict describing what the
controller would look like. Live mode raises NotImplementedError with a clear
actionable message directing the caller to provision the Kit RPC +
ros2_control bridge.

Per docs/specs/2026-05-11-contact-rich-manipulation-spec.md §5.1 (CRM-A2),
§5.2 (CRM-B1), §5.3 (CRM-B2), §5.4 (CRM-B3), and §5.5 (CRM-C4).
"""
# audit-Q17: cohesive — full CRM compliance handler suite (admittance, impedance, set-params, release, trajectory-with-compliance) stays together by design
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from service.isaac_assist_service.multimodal.types import (
    COMPLIANCE_MODE_ENUM,
)
from service.isaac_assist_service.observability.handler_telemetry import with_telemetry


# ---------------------------------------------------------------------------
# Constants — handoff math + mismatch tolerance
#
# Tolerance for comparing the caller-supplied `compliance_handoff_at`
# against a Phase 63b trajectory's `lock_orientation_from` field.  When
# the two differ by more than this threshold a structured warning is
# emitted (but the operation does not fail) so the caller can decide
# whether to align the two values.

_HANDOFF_MISMATCH_TOLERANCE: float = 0.01


# ---------------------------------------------------------------------------
# Module-level in-memory compliance state
# Keyed by robot_path → dict of current param values.
# Populated by setup_admittance_controller and setup_impedance_controller
# on dry-run success.  Read + mutated by set_compliance_params.
#
# Concurrency rationale (CONC-1, 2026-05-14):
# FastAPI's request handler model means two concurrent chat sessions can race
# on this cross-session dict. The setup → set_params → release sequence is a
# read-modify-write cycle (set_params reads the entry, mutates fields, writes
# back; release pops the entry). Without serialization, set_params can see a
# half-mutated state from setup, or operate on a dict that release just
# popped. A single module-level `threading.Lock` is sufficient because all
# operations are short-running (pure-Python list manipulation, no I/O), so
# coarse-grained locking does not introduce meaningful contention.
# `threading.Lock` is chosen over `asyncio.Lock` because the GIL is the
# actual contention surface and `threading.Lock` works correctly across
# both sync helpers and async handlers in CPython's single-threaded event
# loop model.

_INSTALLED_COMPLIANCE: Dict[str, Dict[str, list]] = {}
_COMPLIANCE_LOCK: threading.Lock = threading.Lock()


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


@with_telemetry
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

    # Persist current params so set_compliance_params can mutate them later.
    # Lock held for the single dict-assign + return for consistency with the
    # other handlers below (set_compliance_params, release_compliance) that
    # do multi-step read-modify-write under the same lock.
    with _COMPLIANCE_LOCK:
        _INSTALLED_COMPLIANCE[robot_path] = {
            "stiffness_xyz": stiffness_xyz,
            "damping_xyz": damping_xyz,
            "mass_xyz": mass_xyz,
            "stiffness_rot": stiffness_rot,
            "damping_rot": damping_rot,
            "mass_rot": mass_rot,
            "compliance_mode": "admittance",
        }

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


@with_telemetry
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

    # Persist current params so set_compliance_params can mutate them later.
    # Same locking rationale as setup_admittance — keep cross-handler
    # invariants under the same lock surface.
    with _COMPLIANCE_LOCK:
        _INSTALLED_COMPLIANCE[robot_path] = {
            "stiffness_xyz": Kx,
            "damping_xyz": Dx,
            "stiffness_rot": Kr,
            "damping_rot": Dr,
            "compliance_mode": "impedance",
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
# set_compliance_params handler (CRM-B2)


@with_telemetry
async def _handle_set_compliance_params(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch handler for `set_compliance_params` tool.

    Reads the in-memory state for robot_path and applies any non-None
    param overrides (additive / pass-through semantics).

    Args (from tool call):
        robot_path:    USD path to the robot root — required.
        stiffness_xyz: New translational stiffness [N/m] (3-vector) or None.
        damping_xyz:   New translational damping [N·s/m] (3-vector) or None.
        mass_xyz:      New virtual mass [kg] (3-vector) or None.
        stiffness_rot: New rotational stiffness [N·m/rad] (3-vector) or None.
        damping_rot:   New rotational damping [N·m·s/rad] (3-vector) or None.
        mass_rot:      New rotational virtual mass [kg·m²] (3-vector) or None.
        dry_run:       True (default) → mutate in-memory dict + return merged
                       state. False → raises NotImplementedError.

    Returns:
        On success: {success: True, robot_path, merged params...}
        On missing controller: {success: False, error: str, available_robots: list}
    """
    robot_path: str = args.get("robot_path", "")
    if not robot_path:
        return {
            "success": False,
            "error": "robot_path is required",
        }

    dry_run: bool = bool(args.get("dry_run", True))
    if not dry_run:
        raise NotImplementedError(
            "runtime mutation of live ros2_control controller requires Kit RPC + bridge"
        )

    # Additive update under the global compliance lock.
    # The read-validate-mutate-snapshot sequence below is the critical
    # section that the CONC-1 audit flagged: without serialization a
    # concurrent release_compliance() could pop the entry between the
    # `robot_path not in` check and the `_INSTALLED_COMPLIANCE[robot_path]`
    # lookup, raising KeyError; a concurrent setup_admittance_controller()
    # could overwrite the entry mid-update, causing the snapshot returned
    # to the caller to differ from what set_compliance_params just wrote.
    _PARAM_KEYS = (
        "stiffness_xyz",
        "damping_xyz",
        "mass_xyz",
        "stiffness_rot",
        "damping_rot",
        "mass_rot",
    )
    with _COMPLIANCE_LOCK:
        if robot_path not in _INSTALLED_COMPLIANCE:
            return {
                "success": False,
                "error": f"no compliance controller installed for {robot_path}",
                "available_robots": list(_INSTALLED_COMPLIANCE.keys()),
            }

        state = _INSTALLED_COMPLIANCE[robot_path]

        for key in _PARAM_KEYS:
            value = args.get(key)
            if value is not None:
                state[key] = list(value)

        # Snapshot the merged state under the lock so the returned dict
        # reflects exactly what was written, even if another writer fires
        # immediately after we release.
        snapshot = dict(state)

    result: Dict[str, Any] = {"success": True, "robot_path": robot_path, "dry_run": True}
    result.update(snapshot)
    return result


# ---------------------------------------------------------------------------
# Public signature (matches §5.3 exactly — used by higher-level callers)


async def set_compliance_params(
    robot_path: str,
    stiffness_xyz: list[float] | None = None,
    damping_xyz: list[float] | None = None,
    mass_xyz: list[float] | None = None,
    stiffness_rot: list[float] | None = None,
    damping_rot: list[float] | None = None,
    mass_rot: list[float] | None = None,
    dry_run: bool = True,
) -> dict:
    """Mutate an already-installed compliance controller's parameters.

    Used by `variable_impedance` to shift K between search-phase (low K)
    and insertion-phase (high K) without reinstalling the controller.

    Param mutation is additive: None arguments pass through unchanged.
    The in-memory state dict (keyed by robot_path) is the authoritative
    source — populated by setup_admittance_controller /
    setup_impedance_controller on dry-run success.

    In dry-run mode (default) mutates the in-memory dict and returns
    the merged state. In live mode (dry_run=False) raises
    NotImplementedError until the Kit RPC + ros2_control bridge is wired.

    Args:
        robot_path:    USD path to the robot articulation root.
        stiffness_xyz: New translational stiffness [N/m] per axis, or None.
        damping_xyz:   New translational damping [N·s/m] per axis, or None.
        mass_xyz:      New virtual mass [kg] per axis, or None.
        stiffness_rot: New rotational stiffness [N·m/rad] per axis, or None.
        damping_rot:   New rotational damping [N·m·s/rad] per axis, or None.
        mass_rot:      New rotational virtual mass [kg·m²] per axis, or None.
        dry_run:       If True, mutate in-memory state + return merged dict.

    Returns:
        dict with success: True and merged controller params on success, or
        success: False + error: str + available_robots: list when no
        controller is installed for robot_path.

    Raises:
        NotImplementedError: when dry_run=False (bridge not yet wired).
    """
    return await _handle_set_compliance_params({
        "robot_path": robot_path,
        "stiffness_xyz": stiffness_xyz,
        "damping_xyz": damping_xyz,
        "mass_xyz": mass_xyz,
        "stiffness_rot": stiffness_rot,
        "damping_rot": damping_rot,
        "mass_rot": mass_rot,
        "dry_run": dry_run,
    })


# ---------------------------------------------------------------------------
# release_compliance handler (CRM-B3)


@with_telemetry
async def _handle_release_compliance(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch handler for `release_compliance` tool.

    Pops the robot_path entry from _INSTALLED_COMPLIANCE.  Idempotent —
    releasing an absent robot_path returns success=True with a note.

    Args (from tool call):
        robot_path: USD path to the robot articulation root — required.
        dry_run:    True (default) → mutate in-memory state dict only.
                    False → raises NotImplementedError (requires Kit RPC).

    Returns:
        Absent controller:
            {success: True, robot_path, was_installed: False, note: str}
        Present controller removed:
            {success: True, robot_path, was_installed: True,
             released_mode: str}

    Raises:
        NotImplementedError: when dry_run=False (Kit RPC teardown not
        yet implemented).
    """
    robot_path: str = args.get("robot_path", "")
    if not robot_path:
        return {
            "success": False,
            "error": "robot_path is required",
        }

    dry_run: bool = bool(args.get("dry_run", True))
    if not dry_run:
        raise NotImplementedError(
            "release_compliance live mode requires Kit RPC to tear down "
            "ros2_control bridge — provisioned bridge not yet available. "
            "Use dry_run=True to release the in-memory state entry."
        )

    # The pop + post-read of compliance_mode runs under the lock so a
    # concurrent setup_admittance/impedance cannot squeeze a new entry
    # between pop() and our `entry.get(...)` (it could, but the new entry
    # would be irrelevant to the released_mode of the entry we just popped).
    with _COMPLIANCE_LOCK:
        entry = _INSTALLED_COMPLIANCE.pop(robot_path, None)

    if entry is None:
        return {
            "success": True,
            "robot_path": robot_path,
            "was_installed": False,
            "note": "no compliance controller was installed",
        }

    return {
        "success": True,
        "robot_path": robot_path,
        "was_installed": True,
        "released_mode": entry.get("compliance_mode", "unknown"),
    }


# ---------------------------------------------------------------------------
# Public signature (matches §5.4 exactly — used by higher-level callers)


async def release_compliance(
    robot_path: str,
    dry_run: bool = True,
) -> dict:
    """Remove a previously installed compliance controller for a robot.

    Pops the robot_path entry from the module-level _INSTALLED_COMPLIANCE
    state dict, restoring the robot to its pre-compliance (rigid
    joint-target) state.

    Idempotent: releasing a robot_path that has no installed controller
    returns success=True with was_installed=False and a descriptive note —
    it is safe to call on any robot_path without checking first.

    In dry-run mode (default) modifies only the in-memory state dict.
    In live mode (dry_run=False) raises NotImplementedError until the
    Kit RPC + ros2_control bridge teardown path is wired.

    Args:
        robot_path: USD path to the robot articulation root.
        dry_run:    If True, release in-memory state only (no Kit calls).

    Returns:
        If controller was installed and released:
            {success: True, robot_path, was_installed: True,
             released_mode: str}
        If no controller was present (idempotent success):
            {success: True, robot_path, was_installed: False,
             note: "no compliance controller was installed"}

    Raises:
        NotImplementedError: when dry_run=False (bridge teardown not yet
        wired).
    """
    return await _handle_release_compliance({
        "robot_path": robot_path,
        "dry_run": dry_run,
    })


# ---------------------------------------------------------------------------
# follow_trajectory_with_compliance handler (CRM-C4)
#
# The Phase 63b ↔ Layer 1 bridge.  Splits a Phase-63b trajectory at
# `compliance_handoff_at` into a rigid prefix (`n_rigid` waypoints
# executed as exact joint targets) and a compliant suffix (`n_compliant`
# waypoints fed to the compliance controller as desired-pose references
# that yield to F/T feedback).
#
# When the Phase 63b trajectory's first waypoint exposes a
# `lock_orientation_from` field, it must equal `compliance_handoff_at`
# (within `_HANDOFF_MISMATCH_TOLERANCE`) for seamless transition.  A
# divergence emits a structured `handoff_mismatch_warning` in the result
# — never a failure — so the caller can rebind or accept the gap.


def _validate_trajectory(trajectory: Any) -> Optional[str]:
    """Validate that the trajectory shape matches the Phase 63b contract.

    Args:
        trajectory: Caller-supplied trajectory argument; must be a non-empty
            list of dicts where each waypoint contains at least one of
            ``joint_positions`` or ``pose``.

    Returns:
        None on success, or a human-readable error message on failure.
    """
    if not isinstance(trajectory, list):
        return (
            "trajectory must be a list of waypoint dicts produced by "
            "Phase 63b plan_constrained_trajectory; got "
            f"{type(trajectory).__name__}."
        )
    if len(trajectory) == 0:
        return (
            "trajectory must contain at least one waypoint. "
            "Call plan_constrained_trajectory first to obtain a "
            "non-empty trajectory before invoking compliance handoff."
        )
    for i, wp in enumerate(trajectory):
        if not isinstance(wp, dict):
            return (
                f"trajectory[{i}] must be a dict, got {type(wp).__name__}. "
                "Phase 63b waypoints are dicts with 'joint_positions' or "
                "'pose' keys."
            )
        if "joint_positions" not in wp and "pose" not in wp:
            return (
                f"trajectory[{i}] must contain at least one of "
                "'joint_positions' or 'pose' (Phase 63b waypoint contract)."
            )
    return None


def _build_handoff_mismatch_warning(
    traj_lock: float,
    caller_handoff: float,
) -> str:
    """Render the structured handoff-mismatch warning string.

    Emitted when the trajectory's `lock_orientation_from` differs from
    the caller-supplied `compliance_handoff_at` by more than
    `_HANDOFF_MISMATCH_TOLERANCE`.  Not a failure — the caller decides
    whether to realign or accept the gap.

    Args:
        traj_lock: The trajectory's first waypoint's
            ``lock_orientation_from`` value (the planner's
            seam-of-rigid-to-compliant fraction).
        caller_handoff: The caller-supplied ``compliance_handoff_at``.

    Returns:
        A human-readable warning string referencing both values + the
        recommendation to align them for seamless transition.
    """
    return (
        f"compliance_handoff_at={caller_handoff:.4f} differs from "
        f"trajectory.lock_orientation_from={traj_lock:.4f} by "
        f"{abs(caller_handoff - traj_lock):.4f} (> "
        f"{_HANDOFF_MISMATCH_TOLERANCE:.4f}). "
        "For seamless rigid→compliant transition, align "
        "compliance_handoff_at with the trajectory's "
        "lock_orientation_from. Continuing with the caller value."
    )


@with_telemetry
async def _handle_follow_trajectory_with_compliance(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch handler for `follow_trajectory_with_compliance` tool.

    Splits a Phase 63b trajectory at ``compliance_handoff_at`` into a
    rigid prefix (``n_rigid = int(handoff_at * n_waypoints)``) and a
    compliant suffix (``n_compliant = n_waypoints - n_rigid``).  In
    dry-run mode the handler validates all inputs and returns a
    structured plan dict; in live mode it raises NotImplementedError
    until the Kit RPC + ros2_control bridge is wired.

    Args (from tool call):
        trajectory:            Required.  Non-empty list of waypoint
                               dicts from Phase 63b.  Each must contain
                               at least ``joint_positions`` or ``pose``.
        robot_path:            USD path to the robot articulation root.
        compliance_handoff_at: Fraction in [0, 1] dividing rigid prefix
                               vs compliant suffix.  Should match the
                               trajectory's ``lock_orientation_from``
                               when present.
        compliance_controller: One of COMPLIANCE_MODE_ENUM excluding
                               ``"null"`` (which means "no compliance").
        timeout_s:             Live-mode watchdog timeout (seconds).
        velocity_scaling:      Scaling factor passed through to the
                               trajectory follower.
        dry_run:               True (default) → validate + return plan
                               dict.  False → raise NotImplementedError.

    Returns:
        On success:  Structured plan dict — see §5.5 of the CRM spec.
        On validation failure:  ``{success: False, error: str, ...}``.

    Raises:
        NotImplementedError:  When ``dry_run=False`` (bridge not wired).
    """
    # --- required field: trajectory ---
    trajectory = args.get("trajectory")
    err = _validate_trajectory(trajectory)
    if err is not None:
        return {
            "success": False,
            "ok": False,
            "error": err,
        }

    # --- compliance_handoff_at validation (range [0, 1]) ---
    handoff_raw = args.get("compliance_handoff_at", 0.5)
    try:
        compliance_handoff_at = float(handoff_raw)
    except (TypeError, ValueError):
        return {
            "success": False,
            "ok": False,
            "error": (
                "compliance_handoff_at must be a float in [0, 1]; got "
                f"{handoff_raw!r}."
            ),
        }
    if not (0.0 <= compliance_handoff_at <= 1.0):
        return {
            "success": False,
            "ok": False,
            "error": (
                f"compliance_handoff_at must be in [0, 1]; got "
                f"{compliance_handoff_at}. Phase 63b emits a fraction of "
                "trajectory progress, never a count."
            ),
        }

    # --- compliance_controller validation (6-mode enum from §6) ---
    compliance_controller = args.get("compliance_controller", "admittance")
    if not isinstance(compliance_controller, str):
        return {
            "success": False,
            "ok": False,
            "error": (
                "compliance_controller must be a string from "
                f"COMPLIANCE_MODE_ENUM; got {type(compliance_controller).__name__}."
            ),
        }
    # The "null" mode means "no compliance" — incompatible with this
    # bridge (which exists precisely to install + hand off TO compliance).
    if compliance_controller == "null":
        return {
            "success": False,
            "ok": False,
            "error": (
                "compliance_controller='null' is incompatible with "
                "follow_trajectory_with_compliance — this bridge exists "
                "to hand off TO a compliance controller. Use one of "
                f"{sorted(COMPLIANCE_MODE_ENUM - {'null'})!r}, or call "
                "follow_trajectory (rigid path) instead."
            ),
            "valid_modes": sorted(COMPLIANCE_MODE_ENUM - {"null"}),
        }
    if compliance_controller not in COMPLIANCE_MODE_ENUM:
        return {
            "success": False,
            "ok": False,
            "error": (
                f"compliance_controller={compliance_controller!r} is not in "
                f"COMPLIANCE_MODE_ENUM. Valid modes: "
                f"{sorted(COMPLIANCE_MODE_ENUM)!r}."
            ),
            "valid_modes": sorted(COMPLIANCE_MODE_ENUM),
        }

    # --- timeout / velocity validation (positive floats) ---
    # Pure-arg checks land before the stateful robot_path lookup so
    # callers see numeric errors regardless of installation state.
    timeout_s_raw = args.get("timeout_s", 30.0)
    try:
        timeout_s = float(timeout_s_raw)
    except (TypeError, ValueError):
        return {
            "success": False,
            "ok": False,
            "error": (
                f"timeout_s must be a positive float; got {timeout_s_raw!r}."
            ),
        }
    if timeout_s <= 0.0:
        return {
            "success": False,
            "ok": False,
            "error": (
                f"timeout_s must be > 0; got {timeout_s}."
            ),
        }

    velocity_scaling_raw = args.get("velocity_scaling", 1.0)
    try:
        velocity_scaling = float(velocity_scaling_raw)
    except (TypeError, ValueError):
        return {
            "success": False,
            "ok": False,
            "error": (
                "velocity_scaling must be a positive float; got "
                f"{velocity_scaling_raw!r}."
            ),
        }
    if velocity_scaling <= 0.0:
        return {
            "success": False,
            "ok": False,
            "error": (
                f"velocity_scaling must be > 0; got {velocity_scaling}."
            ),
        }

    # --- robot_path + installed-controller check (last — stateful) ---
    # Snapshot the membership + keys under the lock so a concurrent
    # release_compliance() can't make `available_robots` lie.
    robot_path: str = args.get("robot_path", "/World/Franka")
    with _COMPLIANCE_LOCK:
        is_installed = robot_path in _INSTALLED_COMPLIANCE
        if not is_installed:
            available_robots = list(_INSTALLED_COMPLIANCE.keys())
    if not is_installed:
        return {
            "success": False,
            "ok": False,
            "error": (
                f"no compliance controller installed for {robot_path}. "
                "Call setup_admittance_controller (or setup_impedance_controller "
                "for torque-mode robots) before invoking "
                "follow_trajectory_with_compliance."
            ),
            "available_robots": available_robots,
            "suggested_tool": "setup_admittance_controller",
        }

    dry_run: bool = bool(args.get("dry_run", True))
    if not dry_run:
        raise NotImplementedError(
            "follow_trajectory_with_compliance live mode requires Kit RPC + "
            "ros2_control bridge (CRM-A1) to drive the rigid prefix as joint "
            "targets and hand off to the compliance controller for the "
            "compliant suffix. Use dry_run=True to receive the plan dict "
            "for offline inspection, or provision the bridge and retry with "
            "dry_run=False."
        )

    # --- compute split ---
    # int() truncates toward zero, so handoff_at=0.0 → n_rigid=0 (fully
    # compliant) and handoff_at=1.0 → n_rigid=n_waypoints (fully rigid).
    n_waypoints = len(trajectory)
    n_rigid = int(compliance_handoff_at * n_waypoints)
    n_compliant = n_waypoints - n_rigid

    # --- check for Phase 63b lock_orientation_from mismatch ---
    # The first waypoint of a Phase 63b trajectory may expose its planner's
    # seam fraction. If present and divergent, emit a structured warning
    # (NOT a failure — caller decides).
    handoff_mismatch_warning: Optional[str] = None
    traj_lock = trajectory[0].get("lock_orientation_from")
    if traj_lock is not None:
        try:
            traj_lock_f = float(traj_lock)
        except (TypeError, ValueError):
            traj_lock_f = None
        if traj_lock_f is not None and abs(
            traj_lock_f - compliance_handoff_at
        ) > _HANDOFF_MISMATCH_TOLERANCE:
            handoff_mismatch_warning = _build_handoff_mismatch_warning(
                traj_lock_f, compliance_handoff_at
            )

    # --- assemble plan dict (§5.5 return shape) ---
    final_pose = trajectory[-1]

    return {
        "success": True,
        "ok": True,
        "dry_run": True,
        "robot_path": robot_path,
        "compliance_controller": compliance_controller,
        "n_waypoints": n_waypoints,
        "n_rigid": n_rigid,
        "n_compliant": n_compliant,
        "compliance_handoff_at": compliance_handoff_at,
        "t_handoff_observed": compliance_handoff_at,
        "velocity_scaling": velocity_scaling,
        "timeout_s": timeout_s,
        # Live-only fields: surfaced as None in dry-run; live executor
        # populates them on contact / handoff sample.
        "contact_detected_at": None,
        "ft_at_handoff": None,
        "final_pose": final_pose,
        "handoff_mismatch_warning": handoff_mismatch_warning,
    }


# ---------------------------------------------------------------------------
# Public signature (matches §5.5 exactly — used by higher-level callers)


async def follow_trajectory_with_compliance(
    trajectory: list[dict],                      # waypoints from Phase 63b plan_constrained_trajectory
    robot_path: str = "/World/Franka",
    compliance_handoff_at: float = 0.5,          # 0..1 fraction
    compliance_controller: str = "admittance",
    timeout_s: float = 30.0,
    velocity_scaling: float = 1.0,
    dry_run: bool = True,
) -> dict:
    """Execute a constrained trajectory with rigid-to-compliant handoff.

    Phase 63b ↔ Layer 1 bridge.  From t=0 to t=compliance_handoff_at,
    rigid joint targets follow ``trajectory`` exactly.  From
    t=compliance_handoff_at to t=1, the compliance controller takes
    over; trajectory targets become the "desired pose" reference but
    yield to F/T feedback (admittance/impedance/FDCC dynamics).

    When the trajectory's first waypoint exposes a
    ``lock_orientation_from`` field, the bridge compares it against
    ``compliance_handoff_at``; a divergence > 0.01 emits a structured
    ``handoff_mismatch_warning`` (not a failure — the caller decides).

    Requires that a compliance controller has already been installed
    on ``robot_path`` via ``setup_admittance_controller`` (or the
    impedance variant for torque-mode robots); otherwise returns a
    structured error with ``suggested_tool``.

    In dry-run mode (default) returns a structured plan dict
    describing the rigid/compliant split + handoff metadata without
    touching Kit or ROS2.

    In live mode (dry_run=False) raises NotImplementedError until the
    Kit RPC + ros2_control bridge is provisioned.

    Args:
        trajectory:            Non-empty list of Phase 63b waypoint
                               dicts.  Each waypoint must contain at
                               least one of ``joint_positions`` or
                               ``pose``.  The first waypoint may
                               optionally expose ``lock_orientation_from``
                               for seamless-transition checking.
        robot_path:            USD path to the robot articulation root.
        compliance_handoff_at: Fraction in [0, 1] dividing the rigid
                               prefix (``[0, handoff_at)``) from the
                               compliant suffix (``[handoff_at, 1]``).
                               Should equal the trajectory's
                               ``lock_orientation_from`` when present.
        compliance_controller: Must be a member of COMPLIANCE_MODE_ENUM
                               excluding ``"null"`` (which means "no
                               compliance" — incompatible with this
                               bridge).
        timeout_s:             Live-mode watchdog (seconds).  Must be > 0.
        velocity_scaling:      Multiplier on trajectory velocity.
                               Must be > 0.
        dry_run:               If True (default), return the plan dict
                               without touching Kit.  Set False only
                               when the Kit RPC + ros2_control bridge
                               is provisioned.

    Returns:
        On success::

            {
              "success": True, "ok": True, "dry_run": True,
              "robot_path": ..., "compliance_controller": ...,
              "n_waypoints": ..., "n_rigid": ..., "n_compliant": ...,
              "compliance_handoff_at": ..., "t_handoff_observed": ...,
              "velocity_scaling": ..., "timeout_s": ...,
              "contact_detected_at": None, "ft_at_handoff": None,
              "final_pose": <last waypoint dict>,
              "handoff_mismatch_warning": None | <warning string>,
            }

        On validation failure::

            {"success": False, "ok": False, "error": <message>, ...}

    Raises:
        NotImplementedError: when ``dry_run=False`` (bridge not yet wired).
    """
    return await _handle_follow_trajectory_with_compliance({
        "trajectory": trajectory,
        "robot_path": robot_path,
        "compliance_handoff_at": compliance_handoff_at,
        "compliance_controller": compliance_controller,
        "timeout_s": timeout_s,
        "velocity_scaling": velocity_scaling,
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
    data["set_compliance_params"] = _handle_set_compliance_params
    data["release_compliance"] = _handle_release_compliance
    data["follow_trajectory_with_compliance"] = (
        _handle_follow_trajectory_with_compliance
    )
