"""Scenario variant campaign planning for floor-plan LayoutSpecs.

This module deliberately stops at a deterministic execution plan.  Running the
plan locally, through Isaac Automator, or on Brev/DGX is the next layer; the
important contract here is that agents and tools can agree on exactly which
scene variants should exist before any expensive Kit jobs start.
"""
from __future__ import annotations

import json
from pprint import pformat
from pathlib import Path
from typing import Any, Dict, List, Optional

from .instantiator import instantiate
from .relation_reasoning import verify_relation_geometry


DEFAULT_WORKSPACE = Path("workspace") / "scenario_campaigns"


def _model_dump(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    return {}


def _list_value(data: Dict[str, Any], key: str, default: List[str]) -> List[str]:
    value = data.get(key)
    if not isinstance(value, list):
        return list(default)
    strings = [str(item) for item in value if str(item)]
    return strings or list(default)


def scenario_variant_summary(spec: Any) -> Dict[str, Any]:
    """Return normalized variant knobs for a LayoutSpec-like object."""
    data = _model_dump(getattr(spec, "scenario_variants", None))
    perturbations = _model_dump(data.get("perturbations"))
    validation = _model_dump(data.get("validation"))
    return {
        "enabled": bool(data.get("enabled", False)),
        "variant_count": max(1, min(500, int(data.get("variant_count", 1) or 1))),
        "seed": max(0, int(data.get("seed", 1) or 1)),
        "lighting": _list_value(data, "lighting", ["studio"]),
        "cameras": _list_value(data, "cameras", ["overhead"]),
        "actors": _list_value(data, "actors", []),
        "circumstances": _list_value(data, "circumstances", ["nominal"]),
        "perturbations": {
            "enabled": bool(perturbations.get("enabled", True)),
            "pose_jitter_m": float(perturbations.get("pose_jitter_m", 0.03) or 0.0),
            "rotation_jitter_deg": float(perturbations.get("rotation_jitter_deg", 5.0) or 0.0),
            "material_randomization": bool(perturbations.get("material_randomization", True)),
            "sensor_noise": bool(perturbations.get("sensor_noise", False)),
        },
        "validation": {
            "require_relations": bool(validation.get("require_relations", True)),
            "require_visibility": bool(validation.get("require_visibility", True)),
            "require_physics": bool(validation.get("require_physics", True)),
        },
    }


def build_campaign_plan(
    spec: Any,
    *,
    session_id: str,
    workspace_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Expand ``spec.scenario_variants`` into deterministic per-scene jobs."""
    summary = scenario_variant_summary(spec)
    root = Path(workspace_root) if workspace_root else DEFAULT_WORKSPACE
    campaign_id = f"{session_id}_rev{getattr(spec, 'revision', 0)}_seed{summary['seed']}"
    campaign_dir = root / campaign_id

    actors = summary["actors"] or ["none"]
    variants: List[Dict[str, Any]] = []
    for index in range(summary["variant_count"]):
        lighting = summary["lighting"][index % len(summary["lighting"])]
        camera = summary["cameras"][(index // len(summary["lighting"])) % len(summary["cameras"])]
        circumstance = summary["circumstances"][
            (index // max(1, len(summary["lighting"]) * len(summary["cameras"])))
            % len(summary["circumstances"])
        ]
        actor = actors[
            (index // max(1, len(summary["lighting"]) * len(summary["cameras"]) * len(summary["circumstances"])))
            % len(actors)
        ]
        variant_seed = summary["seed"] + index
        variant_id = f"{campaign_id}_v{index + 1:03d}"
        usd_path = campaign_dir / f"{variant_id}.usda"
        setup_script_path = campaign_dir / f"{variant_id}_setup.py"
        variants.append({
            "variant_id": variant_id,
            "index": index + 1,
            "seed": variant_seed,
            "lighting": lighting,
            "camera": camera,
            "actor": actor,
            "circumstance": circumstance,
            "perturbations": summary["perturbations"],
            "validation": summary["validation"],
            "usd_path": str(usd_path),
            "setup_script_path": str(setup_script_path),
            "launch_command": f"SCENE_SETUP_SCRIPT={setup_script_path} ./launch_canvas_scene.sh {usd_path}",
        })

    return {
        "campaign_id": campaign_id,
        "session_id": session_id,
        "revision": getattr(spec, "revision", 0),
        "enabled": summary["enabled"],
        "workspace_dir": str(campaign_dir),
        "variant_count": len(variants),
        "summary": summary,
        "relation_verification": verify_relation_geometry(spec),
        "variants": variants,
        "execution": {
            "status": "planned",
            "local_supported": True,
            "remote_supported": False,
            "remote_note": "Isaac Automator/Brev/DGX execution is planned behind this same plan contract.",
        },
    }


def _minimal_usda(variant: Dict[str, Any]) -> str:
    custom = {
        "isaac_assist:variant_id": variant["variant_id"],
        "isaac_assist:seed": str(variant["seed"]),
        "isaac_assist:lighting": variant["lighting"],
        "isaac_assist:camera": variant["camera"],
        "isaac_assist:actor": variant["actor"],
        "isaac_assist:circumstance": variant["circumstance"],
    }
    custom_lines = "\n".join(
        f"        string {json.dumps(key)} = {json.dumps(value)}"
        for key, value in custom.items()
    )
    return f"""#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
    customLayerData = {{
{custom_lines}
    }}
)

def Xform "World"
{{
}}
"""


def _variant_setup_script(base_code: str, variant: Dict[str, Any]) -> str:
    metadata = pformat(variant, sort_dicts=True, width=100)
    return f'''"""Isaac Assist scenario variant setup.

Generated from a floor-plan LayoutSpec campaign plan.  This script is intended
to run inside Isaac Sim through launch_isaac.sh's SCENE_SETUP_SCRIPT hook.
"""

VARIANT = {metadata}

print("[Isaac Assist] Applying scenario variant:", VARIANT["variant_id"])
print("[Isaac Assist] lighting=", VARIANT["lighting"],
      "camera=", VARIANT["camera"],
      "actor=", VARIANT["actor"],
      "circumstance=", VARIANT["circumstance"])

# Base scene materialization generated by the LayoutSpec instantiator.
{base_code}

try:
    from pxr import UsdGeom
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
except Exception as exc:
    print("[Isaac Assist] stage axis/unit metadata warning:", exc)

# Variant execution scaffold.  These metadata attributes make the run auditable
# before the dedicated lighting/camera/actor handlers are promoted to hard
# execution code.
try:
    from pxr import Sdf
    world = stage.GetPrimAtPath("/World")
    if world:
        world.SetCustomDataByKey("isaac_assist:variant_id", VARIANT["variant_id"])
        world.SetCustomDataByKey("isaac_assist:seed", VARIANT["seed"])
        world.SetCustomDataByKey("isaac_assist:lighting", VARIANT["lighting"])
        world.SetCustomDataByKey("isaac_assist:camera", VARIANT["camera"])
        world.SetCustomDataByKey("isaac_assist:actor", VARIANT["actor"])
        world.SetCustomDataByKey("isaac_assist:circumstance", VARIANT["circumstance"])
except Exception as exc:
    print("[Isaac Assist] variant metadata warning:", exc)
'''


async def materialize_campaign(
    spec: Any,
    *,
    session_id: str,
    workspace_root: Optional[Path] = None,
    template_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write campaign plan, minimal USDA files, and Kit setup scripts."""
    plan = build_campaign_plan(spec, session_id=session_id, workspace_root=workspace_root)
    campaign_dir = Path(plan["workspace_dir"])
    campaign_dir.mkdir(parents=True, exist_ok=True)

    instantiation = await instantiate(spec, template_id=template_id, dry_run=True)
    base_code = instantiation.generated_code or "# No generated code returned."

    spec_payload = spec.model_dump(mode="json") if hasattr(spec, "model_dump") else spec
    (campaign_dir / "layout_spec.json").write_text(
        json.dumps(spec_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    for variant in plan["variants"]:
        usd_path = Path(variant["usd_path"])
        setup_script_path = Path(variant["setup_script_path"])
        usd_path.parent.mkdir(parents=True, exist_ok=True)
        usd_path.write_text(_minimal_usda(variant), encoding="utf-8")
        setup_script_path.write_text(_variant_setup_script(base_code, variant), encoding="utf-8")

    manifest = {
        **plan,
        "execution": {
            **plan["execution"],
            "status": "materialized",
            "generated_code_status": instantiation.status,
            "relation_verification_status": (
                instantiation.relation_verification or {}
            ).get("status"),
        },
        "relation_verification": instantiation.relation_verification,
    }
    (campaign_dir / "campaign_plan.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest
