"""
Viewport-edit / round-trip-from-stage modality per spec §10.5
and IA Full Spec Phase 22.

`sync_from_stage(scope_prim)` reads Kit's current stage state and
produces a LayoutSpec reflecting the prims under the given scope.

Two layers:
- Pure classification + conversion (this file): `prims_to_layout_spec`
  takes a list of prim-dicts (already read from Kit) and produces a
  LayoutSpec. Testable without Kit RPC.
- Kit-RPC wrapper: `sync_from_stage_via_kit_rpc` (TBD; lands when Block
  4 wires the route — IA Full Spec Phase 19/22).

Per multimodal foundation spec §10.5: modality is "viewport",
confidence is 1.0 (the stage is ground truth — read, not predicted).

Per spec §7.2: viewport-edit provides objects (read from existing
prims) + reverse-derived bindings when role-hints exist as metadata.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .types import (
    Intent,
    LayoutSpec,
    Position,
    RoleBinding,
    Size,
    Source,
    TypedObject,
)

logger = logging.getLogger(__name__)


#: USD reference URL prefix → object_class. The baseline heuristic per
#: IA spec Phase 22 ("match by USD reference URL prefix"). Extended over
#: time as new asset classes onboard.
USD_REF_CLASS_MAP: Dict[str, str] = {
    "franka_panda": "franka_panda",
    "panda": "franka_panda",
    "ur5e": "ur5e",
    "ur10e": "ur10e",
    "kinova_gen3": "kinova_gen3",
    "Franka": "franka_panda",
    "Panda": "franka_panda",
}

#: USD prim type → object_class. Used when no reference URL fingerprints.
USD_TYPE_CLASS_MAP: Dict[str, str] = {
    "Cube": "cube",
    "Cylinder": "cylinder",
    "Sphere": "sphere",
    "ProximitySensor": "sensor",
}

#: Heuristic prim-name → object_class. Last-resort classifier when
#: USD type alone is ambiguous (e.g., "/World/Bin" is a Cube prim but
#: semantically a bin). Right-anchored; matches the path tail.
NAME_CLASS_HEURISTICS: List[tuple] = [
    (re.compile(r"/\w*(?:Conveyor|Belt|Conv\d+)\w*$", re.I), "conveyor"),
    (re.compile(r"/\w*Bin\w*$", re.I), "bin"),
    (re.compile(r"/\w*Cube\w*$", re.I), "cube"),
    (re.compile(r"/\w*Sensor\w*$", re.I), "sensor"),
    (re.compile(r"/\w*Cylinder\w*$", re.I), "cylinder"),
    (re.compile(r"/\w*Franka\w*$", re.I), "franka_panda"),
    (re.compile(r"/\w*UR5e?\w*$", re.I), "ur5e"),
    (re.compile(r"/\w*UR10e?\w*$", re.I), "ur10e"),
]


def classify_prim(prim: Dict[str, Any]) -> Optional[str]:
    """Map a prim-dict to an object_class string, or None if unclassifiable.

    prim shape (subset; only fields we read):
        {
          "path": "/World/Franka",
          "type": "Xform" | "Cube" | "Cylinder" | ...,
          "reference_url": "omniverse://.../franka.usd"  (optional),
          "metadata": {...},  (optional, can carry role_hint)
        }
    """
    # 1) reference_url fingerprint
    ref = (prim.get("reference_url") or "").lower()
    for needle, cls in USD_REF_CLASS_MAP.items():
        if needle.lower() in ref:
            return cls

    # 2) prim-name heuristics (most informative for hand-built scenes)
    path = prim.get("path") or ""
    for pat, cls in NAME_CLASS_HEURISTICS:
        if pat.search(path):
            return cls

    # 3) USD type
    ptype = prim.get("type")
    if ptype in USD_TYPE_CLASS_MAP:
        return USD_TYPE_CLASS_MAP[ptype]

    return None


def _safe_name(path: str) -> str:
    """Convert /World/Foo_1 → Foo_1; ensure USD-identifier-safe."""
    tail = path.rsplit("/", 1)[-1] or "Unnamed"
    # Strip leading digit if present (prim path fragments must start with letter)
    if tail and not tail[0].isalpha():
        tail = "X" + tail
    # Replace any disallowed char
    return re.sub(r"[^A-Za-z0-9_]", "_", tail)


def prim_to_typed_object(prim: Dict[str, Any], object_class: str) -> TypedObject:
    """Convert a Kit-read prim dict + its classified object_class to a TypedObject.

    Position.x/y are read from prim["translate"] = [x, y, z]; size.w/h
    from prim["scale"] (or "size" or "bbox"). Defaults are conservative.
    """
    translate = prim.get("translate") or [0.0, 0.0, 0.0]
    px = float(translate[0]) if len(translate) > 0 else 0.0
    py = float(translate[1]) if len(translate) > 1 else 0.0

    # Size precedence: explicit "size", else "scale", else default
    sw = sh = 0.1
    if prim.get("size"):
        s = prim["size"]
        sw = float(s[0]) if len(s) > 0 else sw
        sh = float(s[1]) if len(s) > 1 else sh
    elif prim.get("scale"):
        s = prim["scale"]
        sw = float(s[0]) * 2.0 if len(s) > 0 else sw  # USD Cube scale → bbox/2
        sh = float(s[1]) * 2.0 if len(s) > 1 else sh

    sw = max(sw, 0.001)
    sh = max(sh, 0.001)

    rotation = float(prim.get("rotation_z_deg", 0.0)) % 360.0
    metadata = dict(prim.get("metadata") or {})
    role_hint = metadata.get("role_hint")
    name = _safe_name(prim.get("path", "/World/Unnamed"))

    return TypedObject(
        **{
            "class": object_class,
            "name": name,
            "position": Position(x=px, y=py),
            "size": Size(w=sw, h=sh),
            "rotation": rotation,
            "role_hint": role_hint,
            "metadata": metadata,
        }
    )


def prims_to_layout_spec(
    prims: List[Dict[str, Any]],
    *,
    pattern_hint: str = "pick_place",
    scope_prim: str = "/World/Layout",
) -> LayoutSpec:
    """Convert a list of Kit-read prim dicts to a LayoutSpec.

    Per IA Full Spec Phase 22:
    - reads prims under scope_prim (caller filters; this fn does NOT
      re-filter)
    - classifies each via classify_prim; unclassifiable prims are
      omitted with a log line
    - emits TypedObjects in stable input order
    - pattern_hint defaults to pick_place (heuristic per spec)
    - confidence is 1.0 (stage is ground truth)

    Returns:
        LayoutSpec with source.modality = "viewport", confidence 1.0
    """
    objects: List[TypedObject] = []
    bindings: Dict[str, RoleBinding] = {}

    for prim in prims:
        cls = classify_prim(prim)
        if cls is None:
            logger.debug(
                f"[stage_to_spec] omitting unclassifiable prim "
                f"path={prim.get('path')} type={prim.get('type')}"
            )
            continue
        obj = prim_to_typed_object(prim, cls)
        objects.append(obj)
        # Reverse-derived binding when prim carries a metadata.role_hint
        if obj.role_hint:
            bindings[obj.role_hint] = RoleBinding(
                object_id=obj.id,
                source="user_explicit",  # stage metadata is user-authored
                confidence=1.0,
                timestamp=datetime.now(timezone.utc),
            )

    return LayoutSpec(
        intent=Intent(pattern_hint=pattern_hint),
        source=Source(
            modality="viewport",
            confidence=1.0,
            timestamp=datetime.now(timezone.utc),
            metadata={"scope_prim": scope_prim, "n_prims_read": len(prims)},
        ),
        objects=objects,
        bindings=bindings or None,
        revision=1,
    )


async def sync_from_stage(
    list_prims_under: Callable[[str], Awaitable[List[Dict[str, Any]]]],
    scope_prim: str = "/World/Layout",
    *,
    pattern_hint: str = "pick_place",
) -> LayoutSpec:
    """Async wrapper: call Kit RPC to read prims under scope_prim, then
    classify and convert.

    Caller supplies the `list_prims_under` async callable that hits the
    Kit RPC (`/list_prims` filtered to scope_prim). This file does NOT
    depend on the Kit-RPC client — keeping the conversion logic pure and
    unit-testable.

    Returns:
        LayoutSpec from prims_to_layout_spec
    """
    prims = await list_prims_under(scope_prim)
    return prims_to_layout_spec(
        prims, pattern_hint=pattern_hint, scope_prim=scope_prim,
    )
