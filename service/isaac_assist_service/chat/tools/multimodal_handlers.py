"""
Multimodal tool handlers — registered into tool_executor.py via a single
import line per coordination doc §3.

Six tools per multimodal-foundation-spec §8.1:
    read_layout_spec       — return current state as structured JSON
    update_layout_spec     — apply mutations, set status='proposed'
    commit_layout_spec     — promote proposed → committed
    apply_layout_spec_to_scene — ratify → execute via canonical pipeline
    query_layout_metric    — geometric / structural metric query
    rebind_role            — explicit role rebinding (post-instantiate-safe)

These handlers DO NOT live as inline sections in tool_executor.py. They live
HERE. tool_executor.py imports `register_multimodal_handlers` and calls it
with its DATA_HANDLERS dict — one line. Reduces merge surface in the shared
file from ~50 inline rows to a single import + call.

Block 1A.1 wiring scope:
- read/update/commit/query → fully wired against MultimodalStore + ratify
- apply_layout_spec_to_scene → ratify path runs end-to-end; the actual Kit
  RPC execution path is held until Block 1B's role-based template refactor.
  Pre-1B: returns the ratified LayoutSpec for the legacy canonical pipeline
  to consume via the existing hard-instantiate flow.
- rebind_role → fully wired (mutates bindings, re-runs ratify)
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from ...multimodal import (
    LayoutSpec,
    Intent,
    Counts,
    StructuralFeatures,
    Source,
    TypedObject,
    RoleBinding,
    validate_layout_spec,
    LayoutSpecValidationError,
)
from ...multimodal.persistence import (
    MultimodalStore,
    RevisionConflictError,
)
from ...multimodal.asset_resolution import resolve_layout_assets
from ...multimodal.instantiator import instantiate
from ...multimodal.ratify import ratify, RatifyResult, resolve_compliance
from ...multimodal.types import Position, Size

logger = logging.getLogger(__name__)


# Module-level singleton store. Lazily initialized to avoid touching the
# disk during import. The store is process-wide (FastAPI single-process
# default); per-thread connections handled internally.
_store: Optional[MultimodalStore] = None


def _get_store() -> MultimodalStore:
    """Return the process-local MultimodalStore singleton (lazy-instantiated)."""
    global _store
    if _store is None:
        _store = MultimodalStore()
    return _store


# ============================================================================
# Handler implementations
# ============================================================================

# Each handler takes a dict of args (matching its tool schema), returns a
# dict that becomes the tool_result. Async to fit tool_executor.py's
# DATA_HANDLERS contract.

async def _handle_read_layout_spec(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return current LayoutSpec for the session.

    Args:
        session_id: required.
        detail_level: 'summary' | 'full' (default 'full').
    """
    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id required"}
    detail_level = args.get("detail_level", "full")

    store = _get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        return {
            "session_id": session_id,
            "spec": None,
            "revision": 0,
            "summary": f"FloorPlan: empty (no LayoutSpec persisted)",
        }

    if detail_level == "summary":
        return {
            "session_id": session_id,
            "revision": spec.revision,
            "summary": _format_compact_summary(spec),
        }
    return {
        "session_id": session_id,
        "revision": spec.revision,
        "spec": spec.model_dump(),
        "summary": _format_compact_summary(spec),
    }


async def _handle_update_layout_spec(args: Dict[str, Any]) -> Dict[str, Any]:
    """Apply mutations to LayoutSpec and persist a new revision via CAS.

    Args:
        session_id: required.
        mutations: list of mutation dicts (see schema).
        reason: one-line explanation of why the agent proposed this mutation.
            Surfaces in the UI confirm bar.
        parent_revision: required. Caller must supply the revision they read.
            Mismatch → 409-style return.
    """
    session_id = args.get("session_id")
    mutations = args.get("mutations", [])
    reason = args.get("reason", "")
    parent_revision = args.get("parent_revision")

    if not session_id:
        return {"error": "session_id required"}
    if parent_revision is None:
        return {"error": "parent_revision required"}

    store = _get_store()
    current = store.get_latest(session_id)

    if current is None:
        # Bootstrap with a minimal spec; only valid if parent_revision == 0
        if parent_revision != 0:
            return {
                "valid": False,
                "issues": [
                    f"no spec exists for session, but parent_revision="
                    f"{parent_revision} (expected 0 for bootstrap)"
                ],
            }
        # Create a baseline spec from the mutations
        try:
            new_spec = _bootstrap_spec_from_mutations(session_id, mutations)
        except ValueError as e:
            return {"valid": False, "issues": [str(e)]}
    else:
        # Apply mutations to a copy of current
        try:
            new_spec = _apply_mutations(current, mutations)
        except ValueError as e:
            return {"valid": False, "issues": [str(e)]}

    # Validate before persist
    validation = validate_layout_spec(new_spec)
    if not validation.valid:
        return {
            "valid": False,
            "issues": [f"[{i.code}] {i.message}" for i in validation.errors],
        }

    try:
        saved = await store.save_with_cas(session_id, new_spec, parent_revision)
    except RevisionConflictError as e:
        return {
            "valid": False,
            "conflict": True,
            "expected_revision": e.expected,
            "actual_revision": e.actual,
            "current_spec": e.current_spec.model_dump() if e.current_spec else None,
            "issues": [f"revision conflict: caller had {e.expected}, current is {e.actual}"],
        }

    # Log telemetry event
    store.append_event(session_id, "update_layout_spec", {
        "revision": saved.revision,
        "n_mutations": len(mutations),
        "reason": reason,
    })

    return {
        "valid": True,
        "revision": saved.revision,
        "summary": _format_compact_summary(saved),
    }


async def _handle_commit_layout_spec(args: Dict[str, Any]) -> Dict[str, Any]:
    """Promote proposed → committed.

    For now this is a no-op marker — the proposed/committed distinction
    lives in the SPA UI state, not in the persisted LayoutSpec itself
    (the persisted LayoutSpec is always authoritative). Logged for
    telemetry per spec §17.1.
    """
    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id required"}

    store = _get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        return {"error": "no LayoutSpec to commit"}

    store.append_event(session_id, "commit_layout_spec", {
        "revision": spec.revision,
    })
    return {"committed": True, "revision": spec.revision}


async def _handle_apply_layout_spec_to_scene(args: Dict[str, Any]) -> Dict[str, Any]:
    """Ratify the LayoutSpec against the matched (or specified) template,
    then trigger build via canonical pipeline.

    Block 1A.1 scope: ratify path runs end-to-end; the actual Kit RPC
    execution is the existing canonical_instantiator flow which is
    independent of role-based templates (legacy templates work today;
    role-based templates land in Block 1B).

    Args:
        session_id: required.
        template_id: optional. If absent, retrieval is invoked separately
            (or hard-instantiate path picks via existing similarity gate).
        force_freeform: bool, default False. When True, skips canonical
            ratify and falls to T5.
    """
    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id required"}
    force_freeform = bool(args.get("force_freeform", False))
    dry_run = bool(args.get("dry_run", True))

    store = _get_store()
    spec = store.get_latest(session_id)
    if spec is None:
        return {"error": "no LayoutSpec to apply"}

    if force_freeform:
        store.append_event(session_id, "apply_layout_spec_to_scene", {
            "revision": spec.revision, "path": "freeform_t5",
        })
        return {
            "ratified": False,
            "path": "freeform_t5",
            "summary": _format_compact_summary(spec),
            "next_step": (
                "agent should fall to free-form planning against this "
                "LayoutSpec since user requested force_freeform"
            ),
        }

    # Stub: in Block 1A we don't have a template loaded; the canonical
    # pipeline (canonical_instantiator) will load it via existing flow.
    # Ratify with a legacy template = trivially ok (no roles to bind).
    template = {"id": args.get("template_id", "<unspecified>")}
    result = ratify(template, spec)
    asset_resolutions = resolve_layout_assets(spec.objects or [])

    payload = {
        "ratified": result.status == "ok",
        "status": result.status,
        "diagnostics": [
            {"role": d.role_name, "object_id": d.object_id,
             "decision": d.decision, "reason": d.reason}
            for d in result.diagnostics
        ],
        "errors": [
            {"kind": e.kind, "role_name": e.role_name,
             "expected": e.expected, "got": e.got,
             "diagnosis": e.diagnosis}
            for e in result.errors
        ],
        "summary": _format_compact_summary(spec),
        "asset_resolutions": [
            {
                "object_id": item.object_id,
                "object_class": item.object_class,
                "usd_ref": item.usd_ref,
                "source": item.source,
                "needs_review": item.needs_review,
            }
            for item in asset_resolutions
        ],
    }

    if result.status == "ok":
        payload["bindings"] = {
            role: {"object_id": b.object_id, "source": b.source}
            for role, b in result.bindings.items()
        }
        # CRM-C2 + CRM-C3 wire-in: auto-pick compliance mode from intent
        # + role bindings, OR validate an explicit override if the
        # LayoutSpec carries one. Result lives under "compliance_resolution"
        # so the canonical-pipeline build path can read the resolved mode
        # without re-running the auto-pick logic.
        compliance = resolve_compliance(spec, result)
        payload["compliance_resolution"] = {
            "mode": compliance.mode,
            "source": compliance.source,
            "violations": [
                {
                    "constraint_id": getattr(v, "constraint_id", None),
                    "severity": str(getattr(v, "severity", "")),
                    "message": getattr(v, "message", ""),
                }
                for v in compliance.violations
            ],
            "hard_violation": compliance.hard_violation,
            "diagnostics": list(compliance.diagnostics),
        }
        payload["next_step"] = (
            "ratified ok — instantiation result is attached; review generated_code "
            "when dry_run=true or inspect build_id when dry_run=false"
        )
        instantiation = await instantiate(
            spec,
            template_id=args.get("template_id"),
            dry_run=dry_run,
        )
        payload["instantiation"] = {
            "status": instantiation.status,
            "message": instantiation.message,
            "build_id": instantiation.build_id,
            "dry_run": dry_run,
            "generated_code": instantiation.generated_code if dry_run else None,
        }
    elif result.status == "needs_choice":
        payload["ambiguous_roles"] = [
            {"role": a.role_name,
             "candidates": a.candidate_object_ids,
             "constraints": a.role_constraints}
            for a in result.ambiguous_roles
        ]
        payload["next_step"] = (
            "ambiguous role bindings; UI should prompt user to choose"
        )
    else:  # rejected
        payload["next_step"] = (
            "rejected — surface diagnostics to user; rebind_role to fix or "
            "drop to T5 free-form"
        )

    store.append_event(session_id, "apply_layout_spec_to_scene", {
        "revision": spec.revision,
        "ratify_status": result.status,
    })
    return payload


async def _handle_query_layout_metric(args: Dict[str, Any]) -> Dict[str, Any]:
    """Query a geometric/structural metric against the current LayoutSpec
    without returning the full state.

    Supported metrics:
        distance — args: {from_id, to_id} → distance in meters
        reachable — args: {robot_id, target_id, reach_m} → bool
        overlap — args: {id_a, id_b} → bool (AABB overlap)
        footprint_area — args: {} → total xy area covered

    Args:
        session_id: required.
        metric: required, one of above.
        args: required, metric-specific.
    """
    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id required"}
    metric = args.get("metric")
    metric_args = args.get("args", {})

    store = _get_store()
    spec = store.get_latest(session_id)
    if spec is None or spec.objects is None:
        return {"error": "no LayoutSpec / no objects to query"}

    obj_by_id = {o.id: o for o in spec.objects}

    if metric == "distance":
        a = obj_by_id.get(metric_args.get("from_id"))
        b = obj_by_id.get(metric_args.get("to_id"))
        if a is None or b is None:
            return {"error": "from_id/to_id not found"}
        import math
        d = math.hypot(a.position.x - b.position.x,
                       a.position.y - b.position.y)
        return {"metric": "distance", "value": d, "unit": "m",
                "detail": f"{a.name} ↔ {b.name}: {d:.3f}m"}

    if metric == "reachable":
        robot = obj_by_id.get(metric_args.get("robot_id"))
        target = obj_by_id.get(metric_args.get("target_id"))
        reach_m = float(metric_args.get("reach_m", 0.855))
        if robot is None or target is None:
            return {"error": "robot_id/target_id not found"}
        import math
        d = math.hypot(robot.position.x - target.position.x,
                       robot.position.y - target.position.y)
        reachable = d <= reach_m
        return {"metric": "reachable", "value": reachable,
                "detail": f"{robot.name}↔{target.name}: {d:.3f}m vs reach {reach_m:.3f}m"}

    if metric == "overlap":
        a = obj_by_id.get(metric_args.get("id_a"))
        b = obj_by_id.get(metric_args.get("id_b"))
        if a is None or b is None:
            return {"error": "id_a/id_b not found"}
        # AABB check
        a_xmin = a.position.x - a.size.w / 2
        a_xmax = a.position.x + a.size.w / 2
        a_ymin = a.position.y - a.size.h / 2
        a_ymax = a.position.y + a.size.h / 2
        b_xmin = b.position.x - b.size.w / 2
        b_xmax = b.position.x + b.size.w / 2
        b_ymin = b.position.y - b.size.h / 2
        b_ymax = b.position.y + b.size.h / 2
        overlap = not (a_xmax < b_xmin or b_xmax < a_xmin
                       or a_ymax < b_ymin or b_ymax < a_ymin)
        return {"metric": "overlap", "value": overlap,
                "detail": f"{a.name}↔{b.name} AABB overlap"}

    if metric == "footprint_area":
        # Sum of object footprint areas (does not deduct overlap)
        area = sum(o.size.w * o.size.h for o in spec.objects)
        return {"metric": "footprint_area", "value": area, "unit": "m²"}

    return {"error": f"unknown metric {metric!r}"}


async def _handle_rebind_role(args: Dict[str, Any]) -> Dict[str, Any]:
    """Explicit role rebinding.

    Per spec §5.5: lives in `ALLOWED_AFTER_INSTANTIATE` so the agent can
    rebind even after build. Mutates LayoutSpec.bindings, persists, returns
    fresh ratify result.

    Args:
        session_id: required.
        role_name: required.
        target: object_id (preferred) or USD path.
        parent_revision: optional CAS guard.
    """
    session_id = args.get("session_id")
    role_name = args.get("role_name")
    target = args.get("target")
    if not (session_id and role_name and target):
        return {"error": "session_id, role_name, target required"}

    store = _get_store()
    spec = store.get_latest(session_id)
    if spec is None or spec.objects is None:
        return {"error": "no LayoutSpec / no objects available for rebinding"}

    # Resolve target → object_id
    target_obj = None
    for o in spec.objects:
        if o.id == target or o.name == target or f"/World/{o.name}" == target:
            target_obj = o
            break
    if target_obj is None:
        return {"error": f"target {target!r} not found in spec.objects"}

    # Mutate bindings
    new_bindings = dict(spec.bindings or {})
    new_bindings[role_name] = RoleBinding(
        object_id=target_obj.id,
        source="user_correction",
        confidence=1.0,
    )
    new_spec = spec.model_copy(update={"bindings": new_bindings})

    parent_revision = args.get("parent_revision", spec.revision)
    try:
        saved = await store.save_with_cas(session_id, new_spec, parent_revision)
    except RevisionConflictError as e:
        return {
            "rebound": False,
            "conflict": True,
            "expected_revision": e.expected,
            "actual_revision": e.actual,
        }

    store.append_event(session_id, "rebind_role", {
        "role_name": role_name,
        "object_id": target_obj.id,
        "source": "user_correction",
    })
    return {
        "rebound": True,
        "role_name": role_name,
        "object_id": target_obj.id,
        "object_name": target_obj.name,
        "revision": saved.revision,
    }


# ============================================================================
# Helpers
# ============================================================================

def _format_compact_summary(spec: LayoutSpec) -> str:
    """Compact textual summary for LLM context (per spec §8.3 — structured
    JSON, NOT NL synthesis. This summary is small + deterministic).

    Avoids natural-language synthesis at the structure→text boundary; the
    output is one line per object with typed key=value pairs.
    """
    lines = [
        f"LayoutSpec[v={spec.version} rev={spec.revision} "
        f"pattern={spec.intent.pattern_hint} "
        f"counts=robots:{spec.intent.counts.robots},"
        f"conveyors:{spec.intent.counts.conveyors},"
        f"bins:{spec.intent.counts.bins},"
        f"cubes:{spec.intent.counts.cubes}]"
    ]
    if spec.objects:
        for o in spec.objects:
            note_suffix = f" note={o.notes!r}" if o.notes else ""
            lines.append(
                f"  {o.id[:8]} {o.object_class}@({o.position.x:.2f},{o.position.y:.2f}) "
                f"rot={o.rotation:.0f} name={o.name}{note_suffix}"
            )
    if spec.bindings:
        for role, b in spec.bindings.items():
            lines.append(f"  binding {role}={b.object_id[:8]} src={b.source}")
    return "\n".join(lines)


def _bootstrap_spec_from_mutations(
    session_id: str, mutations: list,
) -> LayoutSpec:
    """Create a baseline LayoutSpec from a list of `add` mutations.

    Used when the session has no spec yet and the first update is creating
    the initial layout. Only `add` operations valid in this path; any
    `move`/`remove`/`set_attr` against a non-existent object raises.
    """
    objects = []
    for m in mutations:
        if m.get("op") != "add":
            raise ValueError(
                f"cannot apply op={m.get('op')!r} when no LayoutSpec exists; "
                "first mutation must be 'add'"
            )
        # Construct TypedObject from add args
        obj_class = m.get("class")
        if not obj_class:
            raise ValueError("add mutation missing 'class'")
        pos = m.get("position", [0, 0])
        size = m.get("size", [0.1, 0.1])
        objects.append(TypedObject(
            **{"class": obj_class},
            name=m.get("name", f"{obj_class}_{len(objects)}"),
            position=Position(x=pos[0], y=pos[1]),
            size=Size(w=size[0], h=size[1]),
            rotation=m.get("rotation", 0),
            notes=m.get("notes", ""),
        ))

    counts = Counts(
        robots=sum(1 for o in objects if "robot" in o.object_class.lower()
                   or o.object_class in ("franka_panda", "ur5e", "ur10e",
                                         "kinova_gen3", "iiwa", "jaco7",
                                         "nova_carter")),
        conveyors=sum(1 for o in objects if o.object_class == "conveyor"),
        bins=sum(1 for o in objects if o.object_class == "bin"),
        cubes=sum(1 for o in objects if o.object_class == "cube"),
        sensors=sum(1 for o in objects if "sensor" in o.object_class),
    )
    return LayoutSpec(
        intent=Intent(
            pattern_hint="pick_place",  # default; agent can refine later
            counts=counts,
            structural_features=StructuralFeatures(),
        ),
        objects=objects,
        source=Source(modality="drag_drop", confidence=1.0),
    )


def _apply_mutations(spec: LayoutSpec, mutations: list) -> LayoutSpec:
    """Apply a list of mutations to a copy of the LayoutSpec."""
    objects_by_id = {o.id: o for o in (spec.objects or [])}

    for m in mutations:
        op = m.get("op")
        if op == "add":
            obj_class = m.get("class")
            if not obj_class:
                raise ValueError("add mutation missing 'class'")
            pos = m.get("position", [0, 0])
            size = m.get("size", [0.1, 0.1])
            new_obj = TypedObject(
                **{"class": obj_class},
                name=m.get("name", f"{obj_class}_{len(objects_by_id)}"),
                position=Position(x=pos[0], y=pos[1]),
                size=Size(w=size[0], h=size[1]),
                rotation=m.get("rotation", 0),
                notes=m.get("notes", ""),
            )
            objects_by_id[new_obj.id] = new_obj
        elif op == "remove":
            obj_id = m.get("id")
            if obj_id in objects_by_id:
                del objects_by_id[obj_id]
        elif op == "move":
            obj_id = m.get("id")
            obj = objects_by_id.get(obj_id)
            if obj is None:
                raise ValueError(f"move: id {obj_id!r} not found")
            new_pos = Position(x=m["position"][0], y=m["position"][1])
            objects_by_id[obj_id] = obj.model_copy(update={"position": new_pos})
        elif op == "set_attr":
            obj_id = m.get("id")
            obj = objects_by_id.get(obj_id)
            if obj is None:
                raise ValueError(f"set_attr: id {obj_id!r} not found")
            attr = m.get("attr")
            value = m.get("value")
            if attr in {"name", "rotation", "notes", "color", "layer"}:
                objects_by_id[obj_id] = obj.model_copy(update={attr: value})
            else:
                raise ValueError(f"set_attr: unknown/unsupported attr {attr!r}")
        else:
            raise ValueError(f"unknown mutation op {op!r}")

    return spec.model_copy(update={"objects": list(objects_by_id.values())})


# ============================================================================
# Registration entry point
# ============================================================================

def register_multimodal_handlers(
    data_handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]],
) -> None:
    """Register the 6 multimodal handlers into tool_executor's DATA_HANDLERS.

    Called once at module import time from tool_executor.py:

        from .multimodal_handlers import register_multimodal_handlers
        register_multimodal_handlers(DATA_HANDLERS)
    """
    data_handlers["read_layout_spec"] = _handle_read_layout_spec
    data_handlers["update_layout_spec"] = _handle_update_layout_spec
    data_handlers["commit_layout_spec"] = _handle_commit_layout_spec
    data_handlers["apply_layout_spec_to_scene"] = _handle_apply_layout_spec_to_scene
    data_handlers["query_layout_metric"] = _handle_query_layout_metric
    data_handlers["rebind_role"] = _handle_rebind_role
    logger.info("registered 6 multimodal handlers into DATA_HANDLERS")
