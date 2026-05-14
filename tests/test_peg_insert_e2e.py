"""CRM-T3 — E2E peg-insert test with live Kit smoke + admittance vs rigid baseline.

Opt-in via the ``compliance_e2e`` marker:

    python -m pytest tests/test_peg_insert_e2e.py -m compliance_e2e --tb=short

Per docs/specs/2026-05-11-contact-rich-manipulation-spec.md §9.3 (E2E test
plan) and §18.5 (CRM-T3 task brief).

Test SHAPE
----------
The CRM compliance handlers are dry-run only at this point — the live
admittance hot loop is gated behind ``NotImplementedError`` until the Kit
RPC + ros2_control bridge ships (CRM-A1 wired the python bridge skeleton
but the runtime path is not yet live).  This test therefore exercises
the **planning shape** of the bridge — the structural assertion that the
spec §9.3 actually depends on:

* Admittance auto-picks for a Franka contact-rich task (no
  real_robot_deployment tag).
* A trajectory + handoff in admittance mode yields ``n_compliant > 0``.
* The same trajectory with ``compliance_handoff_at=1.0`` (rigid baseline
  proxy) yields ``n_compliant == 0``.

The rigid baseline cannot use ``compliance_controller="null"`` because
the bridge rejects that mode by design (the bridge exists precisely to
hand off TO a compliance controller — see CRM-C4 §5.5 contract).  Per
the CRM-T3 brief, we simulate the rigid baseline by setting
``compliance_handoff_at=1.0`` ("all rigid, no compliant suffix").

Live Kit smoke check
--------------------
``test_kit_rpc_smoke_stage_nonempty`` issues an ``execute_tool_call``
for ``list_all_prims`` against the live Kit RPC (port 8001) and asserts
a non-empty stage.  The anon stage was confirmed to have 14 prims at
test-authoring time (Kit pid 391837); subsequent runs need a Kit RPC
process to be live or the test is skipped.

When Kit RPC is unreachable, the smoke check is skipped (not failed)
because the structural test path is independent of live Kit.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

# All tests in this file are opt-in via -m compliance_e2e.
pytestmark = pytest.mark.compliance_e2e


# Constants — keep magic numbers out of the test body.
_PEG_ROBOT_PATH: str = "/World/Franka"
_PEG_TRAJECTORY_LEN: int = 8  # 8 waypoints — within the spec brief's 5-10 range
_PEG_HANDOFF_AT: float = 0.5  # spec §6 default; bridge expects rigid-then-compliant
_RIGID_HANDOFF_AT: float = 1.0  # all-rigid proxy when 'null' mode is bridge-rejected
_KIT_RPC_URL: str = "http://127.0.0.1:8001"
_KIT_RPC_TIMEOUT_S: float = 5.0


# ---------------------------------------------------------------------------
# Helpers


def _build_peg_insert_trajectory(n: int) -> List[Dict[str, Any]]:
    """Build a minimal peg-in-hole shaped trajectory.

    Phase 63b emits a list of waypoint dicts; this is a synthetic stand-in
    that mirrors that shape without depending on Phase 63b being live.  The
    trajectory descends from above the hole (z=0.95) to the hole z (0.82)
    along a straight line — emulating an APPROACH→ALIGN→INSERT path that
    a real planner would produce for the CP-NEW-peg-in-hole-single
    template (hole at [0, -0.4, 0.825]).

    Args:
        n: Number of waypoints (5-10 per CRM-T3 brief).

    Returns:
        List[Dict[str, Any]] of length ``n``, each waypoint with
        ``joint_positions`` (7 DOF for Franka) and ``pose`` (xyz + quat).
        First waypoint exposes ``lock_orientation_from=0.5`` so the
        admittance call matches the Phase 63b seam at handoff_at=0.5
        without emitting a mismatch warning.
    """
    if n < 2:
        raise ValueError(f"need >=2 waypoints, got {n}")
    # Descend from z=0.95 to z=0.82 (above hole → at hole)
    z_start: float = 0.95
    z_end: float = 0.82
    traj: List[Dict[str, Any]] = []
    for i in range(n):
        frac: float = i / (n - 1)
        z: float = z_start + (z_end - z_start) * frac
        wp: Dict[str, Any] = {
            "joint_positions": [0.0, -0.5, 0.0, -2.0, 0.0, 1.5, 0.785],
            "pose": [0.0, -0.4, z, 1.0, 0.0, 0.0, 0.0],
        }
        traj.append(wp)
    # First waypoint exposes the planner's seam fraction so the admittance
    # call at handoff_at=0.5 matches and no mismatch warning is emitted.
    traj[0]["lock_orientation_from"] = _PEG_HANDOFF_AT
    return traj


def _build_layout_spec_for_peg_insert() -> Any:
    """Build a minimal LayoutSpec-like object for autopick_compliance_mode.

    Per CRM-C2 (role_retriever.autopick_compliance_mode) the function reads
    ``intent.structural_features.has_contact_phase`` defensively via
    getattr — so a duck-typed stand-in is sufficient.

    A peg-in-hole task has has_contact_phase=True; without the
    real_robot_deployment structural tag, autopick must return
    ``"admittance"`` for a Franka.

    Returns:
        Lightweight object with ``intent.structural_features.has_contact_phase``
        and ``intent.structural_tags`` attributes; matches the shape that
        autopick_compliance_mode expects.
    """

    class _Features:
        has_contact_phase: bool = True

    class _Intent:
        structural_features: _Features = _Features()
        structural_tags: List[str] = []  # no real_robot_deployment tag

    class _Spec:
        intent: _Intent = _Intent()

    return _Spec()


async def _kit_rpc_alive() -> bool:
    """Probe Kit RPC's /health endpoint with a short timeout.

    Returns True iff the bridge survived through to the test.  When False,
    the smoke check is skipped (not failed) so the structural assertions
    can still run without a live Kit.
    """
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_KIT_RPC_URL}/health",
                timeout=aiohttp.ClientTimeout(total=_KIT_RPC_TIMEOUT_S),
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                return bool(data.get("ok", False))
    except (asyncio.TimeoutError, OSError, ValueError):
        # OSError covers connection refused; ValueError covers bad JSON;
        # TimeoutError covers slow / unreachable bridge.
        return False
    except Exception:  # pragma: no cover — defensive only
        return False


# ---------------------------------------------------------------------------
# Test 1 — auto-pick resolves to admittance for Franka peg-in-hole


class TestAutopickResolvesToAdmittance:
    """Per spec §4.1: Franka + has_contact_phase + no real-robot tag → admittance."""

    def test_autopick_compliance_mode_for_franka_peg_insert(self) -> None:
        """A Franka peg-in-hole spec must auto-pick admittance.

        This is the CRM-T3 contract step 1: auto-pick should select
        admittance for a Franka task with a contact phase and no
        real_robot_deployment tag.  Without this, the admittance plan
        wouldn't be the natural default.
        """
        from service.isaac_assist_service.chat.tools.role_retriever import (
            autopick_compliance_mode,
        )

        spec = _build_layout_spec_for_peg_insert()
        role_bindings: Dict[str, Any] = {"primary_robot": {"class": "franka_panda"}}
        mode = autopick_compliance_mode(spec, role_bindings)
        assert mode == "admittance", (
            f"Expected auto-pick to return 'admittance' for Franka peg-insert "
            f"without real_robot_deployment tag; got {mode!r}.  "
            "If this changed, spec §4.1 or the auto-pick table was updated."
        )


# ---------------------------------------------------------------------------
# Test 2 — admittance plan yields n_compliant > 0


class TestAdmittancePlanHasCompliantPhase:
    """Per spec §9.3: admittance run must have at least one compliant waypoint."""

    @pytest.mark.asyncio
    async def test_admittance_plan_has_compliant_waypoints(self) -> None:
        """Setup admittance + follow trajectory at handoff_at=0.5 → n_compliant > 0.

        Path:
          1. setup_admittance_controller(dry_run=True) registers state.
          2. follow_trajectory_with_compliance(handoff_at=0.5,
             compliance_controller="admittance") splits trajectory into
             rigid prefix (n_rigid=4) + compliant suffix (n_compliant=4).
          3. Assert n_compliant > 0 and success=True.
        """
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _INSTALLED_COMPLIANCE,
            follow_trajectory_with_compliance,
            release_compliance,
            setup_admittance_controller,
        )

        # Clean slate so prior tests in the same process don't leak state.
        _INSTALLED_COMPLIANCE.pop(_PEG_ROBOT_PATH, None)

        try:
            setup_result = await setup_admittance_controller(
                robot_path=_PEG_ROBOT_PATH,
                stiffness_xyz=[400.0, 400.0, 200.0],
                stiffness_rot=[40.0, 40.0, 40.0],
                dry_run=True,
            )
            assert setup_result["success"] is True, (
                f"setup_admittance_controller failed: {setup_result}"
            )

            trajectory = _build_peg_insert_trajectory(_PEG_TRAJECTORY_LEN)
            plan = await follow_trajectory_with_compliance(
                trajectory=trajectory,
                robot_path=_PEG_ROBOT_PATH,
                compliance_handoff_at=_PEG_HANDOFF_AT,
                compliance_controller="admittance",
                dry_run=True,
            )
            assert plan["success"] is True, f"admittance plan failed: {plan}"
            assert plan["ok"] is True
            assert plan["n_waypoints"] == _PEG_TRAJECTORY_LEN
            assert plan["n_compliant"] > 0, (
                f"Spec §9.3 requires admittance plan to have at least one "
                f"compliant waypoint; got n_compliant={plan['n_compliant']}."
            )
            # The plan must report admittance as the active controller.
            assert plan["compliance_controller"] == "admittance"
            # No mismatch warning expected — trajectory's lock_orientation_from
            # matches the caller's handoff_at exactly.
            assert plan["handoff_mismatch_warning"] is None, (
                f"Unexpected handoff_mismatch_warning: "
                f"{plan['handoff_mismatch_warning']}"
            )
        finally:
            await release_compliance(_PEG_ROBOT_PATH, dry_run=True)


# ---------------------------------------------------------------------------
# Test 3 — rigid baseline plan yields n_compliant == 0


class TestRigidBaselineHasNoCompliantPhase:
    """Per spec §9.3: rigid baseline must have zero compliant waypoints."""

    @pytest.mark.asyncio
    async def test_rigid_baseline_has_zero_compliant_waypoints(self) -> None:
        """Rigid baseline via handoff_at=1.0 → n_compliant=0, n_rigid=n.

        CRM-T3 brief step 5: ``compliance_controller="null"`` is rejected
        by the bridge (it exists to hand off TO compliance — see CRM-C4
        §5.5 contract).  The brief explicitly suggests passing
        handoff_at=1.0 as "all rigid" to simulate the rigid baseline.

        Path:
          1. setup_admittance_controller(dry_run=True) — still required
             since the bridge expects a controller installed before
             accepting any trajectory call.
          2. follow_trajectory_with_compliance(handoff_at=1.0,
             compliance_controller="admittance") → all rigid, no compliant.
          3. Assert n_compliant == 0 and success=True.
        """
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _INSTALLED_COMPLIANCE,
            follow_trajectory_with_compliance,
            release_compliance,
            setup_admittance_controller,
        )

        # Clean slate for rigid baseline.
        _INSTALLED_COMPLIANCE.pop(_PEG_ROBOT_PATH, None)

        try:
            setup_result = await setup_admittance_controller(
                robot_path=_PEG_ROBOT_PATH,
                dry_run=True,
            )
            assert setup_result["success"] is True, (
                f"setup_admittance_controller failed: {setup_result}"
            )

            # Build a trajectory WITHOUT lock_orientation_from on the first
            # waypoint — the rigid baseline doesn't claim a Phase 63b seam,
            # so no mismatch warning should fire.
            rigid_trajectory = _build_peg_insert_trajectory(_PEG_TRAJECTORY_LEN)
            rigid_trajectory[0].pop("lock_orientation_from", None)

            plan = await follow_trajectory_with_compliance(
                trajectory=rigid_trajectory,
                robot_path=_PEG_ROBOT_PATH,
                compliance_handoff_at=_RIGID_HANDOFF_AT,
                compliance_controller="admittance",
                dry_run=True,
            )
            assert plan["success"] is True, f"rigid plan failed: {plan}"
            assert plan["ok"] is True
            assert plan["n_waypoints"] == _PEG_TRAJECTORY_LEN
            assert plan["n_compliant"] == 0, (
                f"Spec §9.3 requires rigid baseline to have zero compliant "
                f"waypoints; got n_compliant={plan['n_compliant']}."
            )
            assert plan["n_rigid"] == _PEG_TRAJECTORY_LEN, (
                f"Rigid baseline must have all waypoints in rigid phase; "
                f"got n_rigid={plan['n_rigid']}."
            )
            # No mismatch warning — rigid trajectory has no
            # lock_orientation_from on the first waypoint.
            assert plan["handoff_mismatch_warning"] is None
        finally:
            await release_compliance(_PEG_ROBOT_PATH, dry_run=True)


# ---------------------------------------------------------------------------
# Test 4 — admittance vs rigid comparison (the spec §9.3 invariant)


class TestAdmittanceVsRigidComparison:
    """Spec §9.3 invariant: admittance has compliant waypoints; rigid does not."""

    @pytest.mark.asyncio
    async def test_admittance_has_compliant_rigid_has_none(self) -> None:
        """Both plans succeed; admittance n_compliant > 0; rigid n_compliant == 0.

        This is the principal CRM-T3 assertion: the test SHAPE that
        spec §9.3 actually requires before the live admittance hot loop
        is in place.  When the live path lands (Kit RPC + ros2_control
        bridge per CRM-A1), an L3 successor test should replace
        ``dry_run=True`` with live execution and assert the §9.3
        success-rate threshold (≥50% better with admittance).
        """
        from service.isaac_assist_service.chat.tools.handlers.compliance import (
            _INSTALLED_COMPLIANCE,
            follow_trajectory_with_compliance,
            release_compliance,
            setup_admittance_controller,
        )

        _INSTALLED_COMPLIANCE.pop(_PEG_ROBOT_PATH, None)

        try:
            await setup_admittance_controller(
                robot_path=_PEG_ROBOT_PATH, dry_run=True
            )
            trajectory = _build_peg_insert_trajectory(_PEG_TRAJECTORY_LEN)

            admittance_plan = await follow_trajectory_with_compliance(
                trajectory=trajectory,
                robot_path=_PEG_ROBOT_PATH,
                compliance_handoff_at=_PEG_HANDOFF_AT,
                compliance_controller="admittance",
                dry_run=True,
            )

            # Rigid baseline uses a trajectory without lock_orientation_from.
            rigid_trajectory = _build_peg_insert_trajectory(_PEG_TRAJECTORY_LEN)
            rigid_trajectory[0].pop("lock_orientation_from", None)

            rigid_plan = await follow_trajectory_with_compliance(
                trajectory=rigid_trajectory,
                robot_path=_PEG_ROBOT_PATH,
                compliance_handoff_at=_RIGID_HANDOFF_AT,
                compliance_controller="admittance",
                dry_run=True,
            )

            # Both calls must succeed.
            assert admittance_plan["success"] is True
            assert rigid_plan["success"] is True

            # The §9.3 invariant.
            assert admittance_plan["n_compliant"] > 0, (
                "Admittance plan must have at least one compliant waypoint "
                "(spec §9.3); got "
                f"n_compliant={admittance_plan['n_compliant']}."
            )
            assert rigid_plan["n_compliant"] == 0, (
                "Rigid baseline must have zero compliant waypoints "
                "(spec §9.3); got "
                f"n_compliant={rigid_plan['n_compliant']}."
            )

            # And both plans must have processed the same number of waypoints.
            assert (
                admittance_plan["n_waypoints"]
                == rigid_plan["n_waypoints"]
                == _PEG_TRAJECTORY_LEN
            )
        finally:
            await release_compliance(_PEG_ROBOT_PATH, dry_run=True)


# ---------------------------------------------------------------------------
# Test 5 — live Kit RPC smoke (skipped if Kit unreachable)


class TestKitRpcSmoke:
    """Live Kit smoke — assert the bridge survived through to the test.

    Per CRM-T3 brief step 7: "if you can issue a list_all_prims call via
    tool_executor and assert a non-empty stage (the anon stage on the
    running Kit has 14 prims), include that as a smoke check that the
    bridge survived through to here."

    Skipped (not failed) when Kit RPC is unreachable — keeps the
    structural admittance-vs-rigid assertions independent of Kit
    availability.
    """

    @pytest.mark.asyncio
    async def test_kit_rpc_smoke_stage_nonempty(self) -> None:
        """list_all_prims through tool_executor returns non-empty stage.

        At test-authoring time the running Kit (pid 391837) had 14 prims
        on its anon stage.  We assert prim_count > 0 (not exactly 14) so
        the test stays robust against successive runs that may add or
        remove prims.
        """
        if not await _kit_rpc_alive():
            pytest.skip(
                f"Kit RPC at {_KIT_RPC_URL} unreachable — skipping smoke check. "
                "Provision Kit RPC (see launch_isaac.sh) to enable this assertion."
            )

        from service.isaac_assist_service.chat.tools import tool_executor

        result = await tool_executor.execute_tool_call("list_all_prims", {})
        # list_all_prims is a data handler; result wraps the stage dict.
        # Per scene_authoring._handle_list_all_prims it returns ctx['stage'],
        # which includes prim_count.
        assert result.get("type") == "data", (
            f"Expected list_all_prims to return type='data'; got {result!r}"
        )
        prim_count = result.get("prim_count")
        assert isinstance(prim_count, int), (
            f"Expected prim_count to be int; got {prim_count!r}.  "
            f"Full result keys: {sorted(result.keys())}"
        )
        assert prim_count > 0, (
            f"Live Kit stage should have at least one prim; got "
            f"prim_count={prim_count}.  Bridge may have lost its stage."
        )
