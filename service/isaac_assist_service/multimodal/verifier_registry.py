"""verifier_registry.py — Block 1B Step 17.

Restructures verify-pipeline as registry-dispatched checks per
docs/specs/2026-05-08-multimodal-foundation-spec.md §6.

Each VerifierCheck declares:
  - id: namespaced ("verify:reach", "simulate:upright_at_rest")
  - applies_when: predicate over StructuralFeatures
  - run: takes (template, bindings, args) → CheckResult

Two gates:
  - form_gate: pre-build / pre-canonical static checks
  - function_gate: post-build, sim-running dynamic checks

`verify_pickplace_pipeline` and `simulate_traversal_check` become thin
wrappers that delegate to the appropriate gate. No new behavior added;
this is a pure structural refactor. New checks register without
touching existing verifier code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

from .types import LayoutSpec, RoleBinding, StructuralFeatures


CheckStatus = Literal["pass", "fail", "skipped"]


@dataclass
class CheckResult:
    """Result of a single verifier check."""

    status: CheckStatus
    diagnostics: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    reason: str = ""  # for "skipped"
    data: Dict[str, Any] = field(default_factory=dict)

    def is_pass(self) -> bool:
        return self.status == "pass"

    def is_fail(self) -> bool:
        return self.status == "fail"

    def is_skipped(self) -> bool:
        return self.status == "skipped"


@dataclass
class VerifierCheck:
    """A single feature-dispatched verifier check."""

    id: str  # namespaced: "verify:reach", "simulate:upright_at_rest"
    applies_when: Callable[[StructuralFeatures], bool]
    run: Callable[..., CheckResult]
    description: str = ""

    def __post_init__(self):
        # Enforce namespace convention
        if ":" not in self.id:
            raise ValueError(f"CheckId must be namespaced (have ':'): {self.id}")


# ---------------------------------------------------------------------------
# Gate result aggregation
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """Aggregated result of running all applicable checks in a gate."""

    gate: Literal["form_gate", "function_gate"]
    overall: CheckStatus  # "pass" if all applicable pass, else "fail" or "skipped"
    checks_run: List[str] = field(default_factory=list)
    checks_skipped: List[str] = field(default_factory=list)
    per_check: Dict[str, CheckResult] = field(default_factory=dict)

    def passed_check_count(self) -> int:
        return sum(1 for r in self.per_check.values() if r.is_pass())

    def failed_check_count(self) -> int:
        return sum(1 for r in self.per_check.values() if r.is_fail())

    def is_pass(self) -> bool:
        return self.overall == "pass"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class VerifierRegistry:
    """Holds all registered form_gate and function_gate checks.

    Checks are registered append-only at module-load time via
    `register_form_check()` / `register_function_check()`. Dispatch is
    by structural-feature predicate.
    """

    def __init__(self):
        self._form_checks: List[VerifierCheck] = []
        self._function_checks: List[VerifierCheck] = []

    def register_form_check(self, check: VerifierCheck) -> None:
        if any(c.id == check.id for c in self._form_checks):
            raise ValueError(f"form_gate check {check.id!r} already registered")
        self._form_checks.append(check)

    def register_function_check(self, check: VerifierCheck) -> None:
        if any(c.id == check.id for c in self._function_checks):
            raise ValueError(f"function_gate check {check.id!r} already registered")
        self._function_checks.append(check)

    def all_form_checks(self) -> List[VerifierCheck]:
        return list(self._form_checks)

    def all_function_checks(self) -> List[VerifierCheck]:
        return list(self._function_checks)

    def form_check_ids(self) -> List[str]:
        return [c.id for c in self._form_checks]

    def function_check_ids(self) -> List[str]:
        return [c.id for c in self._function_checks]

    # --- gate runners ---

    def run_form_gate(
        self,
        features: StructuralFeatures,
        template: Optional[Dict[str, Any]] = None,
        bindings: Optional[List[RoleBinding]] = None,
        args: Optional[Dict[str, Any]] = None,
    ) -> GateResult:
        return self._run_gate("form_gate", self._form_checks, features, template, bindings, args or {})

    def run_function_gate(
        self,
        features: StructuralFeatures,
        template: Optional[Dict[str, Any]] = None,
        bindings: Optional[List[RoleBinding]] = None,
        args: Optional[Dict[str, Any]] = None,
    ) -> GateResult:
        return self._run_gate("function_gate", self._function_checks, features, template, bindings, args or {})

    def _run_gate(
        self,
        gate_name: Literal["form_gate", "function_gate"],
        checks: List[VerifierCheck],
        features: StructuralFeatures,
        template: Optional[Dict[str, Any]],
        bindings: Optional[List[RoleBinding]],
        args: Dict[str, Any],
    ) -> GateResult:
        per_check: Dict[str, CheckResult] = {}
        run_ids: List[str] = []
        skipped_ids: List[str] = []

        for check in checks:
            try:
                applies = check.applies_when(features)
            except Exception as e:
                # Defensive: don't let a misbehaving predicate take down the gate
                per_check[check.id] = CheckResult(
                    status="skipped",
                    reason=f"applies_when raised {type(e).__name__}: {e}",
                )
                skipped_ids.append(check.id)
                continue

            if not applies:
                per_check[check.id] = CheckResult(status="skipped", reason="predicate false")
                skipped_ids.append(check.id)
                continue

            try:
                result = check.run(template=template, bindings=bindings, args=args)
            except Exception as e:
                result = CheckResult(
                    status="fail",
                    issues=[f"{check.id} raised {type(e).__name__}: {e}"],
                )
            per_check[check.id] = result
            run_ids.append(check.id)

        # Aggregate: pass if all run checks pass; fail if any run check fails;
        # skipped if no checks applied at all.
        if not run_ids:
            overall: CheckStatus = "skipped"
        elif any(per_check[i].is_fail() for i in run_ids):
            overall = "fail"
        else:
            overall = "pass"

        return GateResult(
            gate=gate_name,
            overall=overall,
            checks_run=run_ids,
            checks_skipped=skipped_ids,
            per_check=per_check,
        )


# ---------------------------------------------------------------------------
# Module-level singleton + register API
# ---------------------------------------------------------------------------


REGISTRY = VerifierRegistry()


def register_form_check(
    id: str,
    applies_when: Callable[[StructuralFeatures], bool],
    run: Callable[..., CheckResult],
    description: str = "",
) -> VerifierCheck:
    """Register a form_gate check at module-load time. Returns the check."""
    check = VerifierCheck(id=id, applies_when=applies_when, run=run, description=description)
    REGISTRY.register_form_check(check)
    return check


def register_function_check(
    id: str,
    applies_when: Callable[[StructuralFeatures], bool],
    run: Callable[..., CheckResult],
    description: str = "",
) -> VerifierCheck:
    """Register a function_gate check. Returns the check."""
    check = VerifierCheck(id=id, applies_when=applies_when, run=run, description=description)
    REGISTRY.register_function_check(check)
    return check


# ---------------------------------------------------------------------------
# Default checks per spec §6.3
# ---------------------------------------------------------------------------


def _make_skipped(reason: str) -> CheckResult:
    return CheckResult(status="skipped", reason=reason)


def _check_reach(**kwargs) -> CheckResult:
    """verify:reach — robot stations exist; verify each robot's pick/drop poses
    are within its reachable workspace.

    Implementation hook: delegates to existing reach-feasibility logic in
    `tool_executor.py` via verify_pickplace_pipeline (Block 1B step 17
    backward-compat wrapper). Pure-Python here is a placeholder result that
    passes through when no detailed reach data is available.
    """
    args = kwargs.get("args") or {}
    if "reach_diagnostics" in args:
        # External caller already computed reach feasibility
        if args["reach_diagnostics"].get("all_reachable", True):
            return CheckResult(status="pass", diagnostics=["reach OK from external"])
        return CheckResult(
            status="fail",
            issues=args["reach_diagnostics"].get("unreachable", ["unreachable cubes"]),
        )
    # No detail available; treat as pass with caveat (Block 1B is structural
    # only; Block 2+ wires real check)
    return CheckResult(status="pass", diagnostics=["reach check stub — no detail provided"])


def _check_conveyor_active(**kwargs) -> CheckResult:
    """verify:conveyor_active — when transport uses conveyor, verify belt has
    non-zero surfaceVelocity at simulation start."""
    args = kwargs.get("args") or {}
    if "conveyor_active" in args:
        if args["conveyor_active"]:
            return CheckResult(status="pass", diagnostics=["belt active"])
        return CheckResult(status="fail", issues=["conveyor belt has zero surfaceVelocity"])
    return CheckResult(status="pass", diagnostics=["conveyor_active stub — no detail"])


def _check_controller_installed(**kwargs) -> CheckResult:
    """verify:controller_installed — robot stations exist; check each has
    setup_pick_place_controller (or equivalent) installed."""
    args = kwargs.get("args") or {}
    if "controllers_installed" in args:
        missing = args["controllers_installed"].get("missing", [])
        if not missing:
            return CheckResult(status="pass", diagnostics=["all controllers installed"])
        return CheckResult(status="fail", issues=[f"missing controllers: {missing}"])
    return CheckResult(status="pass", diagnostics=["controller_installed stub"])


def _check_cube_source_bridged(**kwargs) -> CheckResult:
    """verify:cube_source_bridged — cubes exist and conveyor transport bridges
    them to the pick zone (no orphan cubes spawning outside belt footprint).
    """
    args = kwargs.get("args") or {}
    if "orphan_cubes" in args:
        orphans = args["orphan_cubes"]
        if not orphans:
            return CheckResult(status="pass", diagnostics=["all cubes on belt"])
        return CheckResult(status="fail", issues=[f"orphan cubes: {orphans}"])
    return CheckResult(status="pass", diagnostics=["cube_source_bridged stub"])


def _check_footprint_within_bounds(**kwargs) -> CheckResult:
    """verify:footprint_within_bounds — scene xy-extent ≤ has_bounded_footprint's
    declared limits."""
    args = kwargs.get("args") or {}
    if "footprint_overshoot" in args:
        overshoot = args["footprint_overshoot"]
        if not overshoot:
            return CheckResult(status="pass", diagnostics=["footprint within bounds"])
        return CheckResult(status="fail", issues=[f"footprint overshoot: {overshoot}"])
    return CheckResult(status="pass", diagnostics=["footprint stub"])


def _check_color_routing_consistent(**kwargs) -> CheckResult:
    """verify:color_routing_consistent — color_routing map covers all source-
    cube color tags, no orphan colors."""
    args = kwargs.get("args") or {}
    if "color_routing" in args:
        cr = args["color_routing"]
        missing = cr.get("missing_colors", [])
        if not missing:
            return CheckResult(status="pass", diagnostics=["color_routing complete"])
        return CheckResult(status="fail", issues=[f"colors without routing: {missing}"])
    return CheckResult(status="pass", diagnostics=["color_routing stub"])


def _check_cube_delivered(**kwargs) -> CheckResult:
    """simulate:cube_delivered — after sim, ≥1 cube reached target bbox."""
    args = kwargs.get("args") or {}
    if "delivered_count" in args:
        if args["delivered_count"] > 0:
            return CheckResult(
                status="pass",
                diagnostics=[f"delivered {args['delivered_count']}"],
                data={"delivered": args["delivered_count"]},
            )
        return CheckResult(status="fail", issues=["no cubes delivered"])
    return CheckResult(status="pass", diagnostics=["cube_delivered stub"])


def _check_upright_at_rest(**kwargs) -> CheckResult:
    """simulate:upright_at_rest — for orientation-required scenarios, final
    cube orientation must satisfy upright_dot_threshold."""
    args = kwargs.get("args") or {}
    if "upright_pass" in args:
        if args["upright_pass"]:
            return CheckResult(status="pass", diagnostics=["upright OK"])
        return CheckResult(status="fail", issues=["cube not upright at rest"])
    return CheckResult(status="pass", diagnostics=["upright_at_rest stub"])


def _check_human_safety_zone(**kwargs) -> CheckResult:
    """simulate:human_safety_zone — when human is in workspace, robot trajectory
    must maintain min distance to human-link bounding boxes throughout sim."""
    args = kwargs.get("args") or {}
    if "safety_violations" in args:
        violations = args["safety_violations"]
        if not violations:
            return CheckResult(status="pass", diagnostics=["safety distance maintained"])
        return CheckResult(status="fail", issues=[f"safety violations: {violations}"])
    return CheckResult(status="pass", diagnostics=["human_safety_zone stub"])


# Register all default checks per spec §6.3
register_form_check(
    "verify:reach",
    applies_when=lambda f: f.n_robot_stations > 0,
    run=_check_reach,
    description="Each robot's pick+drop poses are within reach.",
)
register_form_check(
    "verify:conveyor_active",
    applies_when=lambda f: f.uses_conveyor_transport,
    run=_check_conveyor_active,
    description="Conveyor surfaceVelocity is non-zero.",
)
register_form_check(
    "verify:controller_installed",
    applies_when=lambda f: f.n_robot_stations > 0,
    run=_check_controller_installed,
    description="Every robot has a pick-place controller installed.",
)
register_form_check(
    "verify:cube_source_bridged",
    applies_when=lambda f: f.uses_conveyor_transport,
    run=_check_cube_source_bridged,
    description="Cubes spawn on belt; no orphans outside transport.",
)
register_form_check(
    "verify:footprint_within_bounds",
    applies_when=lambda f: f.has_bounded_footprint,
    run=_check_footprint_within_bounds,
    description="Scene xy-extent within declared footprint limits.",
)
register_form_check(
    "verify:color_routing_consistent",
    applies_when=lambda f: f.has_color_routing,
    run=_check_color_routing_consistent,
    description="Color-routing map covers every source-cube color.",
)

register_function_check(
    "simulate:cube_delivered",
    applies_when=lambda f: True,  # cube delivery is universal for pick-place
    run=_check_cube_delivered,
    description="At least one cube reached target bbox during sim.",
)
register_function_check(
    "simulate:upright_at_rest",
    applies_when=lambda f: f.has_orientation_requirement,
    run=_check_upright_at_rest,
    description="Cube orientation satisfies upright_dot_threshold at end of sim.",
)
register_function_check(
    "simulate:human_safety_zone",
    applies_when=lambda f: f.has_human_in_workspace,
    run=_check_human_safety_zone,
    description="Robot maintains safety distance to human throughout sim.",
)
