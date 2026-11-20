"""MCP tools and FastAPI router for CAD-to-SimReady, Isaac Sim 7.0.0a1 dual-physics, and OpenUSD workcell composition."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
_PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "cad_to_sim_pipeline.py"


def _load_pipeline_module():
    spec = importlib.util.spec_from_file_location("isaac_assist_cad_to_sim_pipeline", _PIPELINE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CAD pipeline script at {_PIPELINE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CAD_TOOL_NAMES = frozenset(
    {
        "design_parametric_cad",
        "convert_cad_to_simready_usd",
        "verify_asset_dual_7a",
        "compose_omniverse_workcell_stage",
        "run_cad_to_sim_e2e",
    }
)


def mcp_cad_ecosystem_tool_schemas() -> List[Dict[str, Any]]:
    """Return MCP tool definitions for the CAD -> SimReady -> 7.0a -> Omniverse workcell pipeline."""
    return [
        {
            "name": "design_parametric_cad",
            "description": (
                "Generate a watertight parametric B-Rep CAD model (.step for Fusion 360/SolidWorks + .stl "
                "for Bambu Studio fabrication + exact B-Rep volume, mass, COM, and inertia) via headless FreeCAD."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "part_id": {"type": "string", "description": "Unique identifier for the generated CAD part."},
                    "template": {
                        "type": "string",
                        "enum": ["slotted_fixture", "machined_shaft", "parallel_gripper_finger", "part_tray_bin"],
                        "default": "slotted_fixture",
                    },
                    "material": {
                        "type": "string",
                        "enum": ["6061_t6_aluminum", "stainless_304", "pla", "abs_plastic", "tpu_95a"],
                        "default": "6061_t6_aluminum",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Optional parametric dimension overrides in millimeters.",
                    },
                },
                "required": ["part_id"],
            },
        },
        {
            "name": "convert_cad_to_simready_usd",
            "description": (
                "Convert a .step/.stp/.iges/.stl CAD file (or Stage-0 metadata JSON) into a layered OpenUSD "
                "SimReady asset (01_cad_geometry.usdc + 02_simready_physics.usda + VariantSets for "
                "PhysicsFidelity and MaterialFinish) and validate with usd-validation-nvidia."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cad_path": {"type": "string", "description": "Path to .step/.stp/.iges/.stl or metadata JSON."},
                    "part_id": {"type": "string"},
                    "material": {
                        "type": "string",
                        "enum": ["6061_t6_aluminum", "stainless_304", "pla", "abs_plastic", "tpu_95a"],
                        "default": "6061_t6_aluminum",
                    },
                    "default_physics_variant": {
                        "type": "string",
                        "enum": ["rigid_convex", "sdf_hydroelastic", "static_fixture"],
                        "default": "rigid_convex",
                    },
                },
                "required": ["cad_path"],
            },
        },
        {
            "name": "verify_asset_dual_7a",
            "description": (
                "Run zero-Kit Isaac Sim 7.0.0a1 dual-engine physics verification (ovphysx + ovnewton) "
                "on a SimReady USD asset without blocking the live Omniverse Kit GUI."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "usd_path": {"type": "string"},
                    "drop_height_m": {"type": "number", "default": 0.20},
                    "steps": {"type": "integer", "default": 180},
                    "promote_to_registry": {"type": "boolean", "default": True},
                },
                "required": ["usd_path"],
            },
        },
        {
            "name": "compose_omniverse_workcell_stage",
            "description": (
                "Compose a multi-sublayer LIVRPS OpenUSD digital-twin workcell combining environment/lighting, "
                "SimReady CAD fixture + workpiece, mutable agent governance sublayer, and optional live "
                "deployment to Isaac Sim + ROS 2 Jazzy (/clock and /tf over Zenoh)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workcell_id": {"type": "string"},
                    "fixture_usd_path": {"type": "string"},
                    "workpiece_usd_path": {"type": "string"},
                    "gsplat_backdrop_usd": {"type": "string"},
                    "deploy_live": {"type": "boolean", "default": False},
                },
                "required": ["workcell_id", "fixture_usd_path", "workpiece_usd_path"],
            },
        },
        {
            "name": "run_cad_to_sim_e2e",
            "description": (
                "Execute the full CAD -> SimReady USD -> 7.0.0a1 ovphysx+ovnewton verification -> "
                "governed OpenUSD workcell pipeline end-to-end."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workcell_id": {"type": "string", "default": "nemotron_cad_cell_v1"},
                    "fixture_material": {"type": "string", "default": "6061_t6_aluminum"},
                    "workpiece_material": {"type": "string", "default": "pla"},
                    "deploy_live": {"type": "boolean", "default": False},
                },
            },
        },
    ]


def handles_cad_ecosystem_tool(tool_name: str) -> bool:
    return tool_name in _CAD_TOOL_NAMES


def _execute_sync(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    mod = _load_pipeline_module()
    if tool_name == "design_parametric_cad":
        return mod.design_parametric_cad(
            part_id=arguments["part_id"],
            template=arguments.get("template", "slotted_fixture"),
            material=arguments.get("material", "6061_t6_aluminum"),
            parameters=arguments.get("parameters"),
        )
    if tool_name == "convert_cad_to_simready_usd":
        return mod.convert_cad_to_simready_usd(
            cad_or_metadata_path=arguments["cad_path"],
            part_id=arguments.get("part_id"),
            material=arguments.get("material", "6061_t6_aluminum"),
            default_physics_variant=arguments.get("default_physics_variant", "rigid_convex"),
        )
    if tool_name == "verify_asset_dual_7a":
        return mod.verify_asset_dual_7a(
            usd_path=arguments["usd_path"],
            drop_height_m=float(arguments.get("drop_height_m", 0.20)),
            steps=int(arguments.get("steps", 180)),
            promote_to_registry=bool(arguments.get("promote_to_registry", True)),
        )
    if tool_name == "compose_omniverse_workcell_stage":
        return mod.compose_omniverse_workcell_stage(
            workcell_id=arguments["workcell_id"],
            fixture_usd_path=arguments["fixture_usd_path"],
            workpiece_usd_path=arguments["workpiece_usd_path"],
            gsplat_backdrop_usd=arguments.get("gsplat_backdrop_usd"),
            deploy_live=bool(arguments.get("deploy_live", False)),
        )
    if tool_name == "run_cad_to_sim_e2e":
        workcell_id = arguments.get("workcell_id", "nemotron_cad_cell_v1")
        fix_mat = arguments.get("fixture_material", "6061_t6_aluminum")
        wp_mat = arguments.get("workpiece_material", "pla")
        deploy_live = bool(arguments.get("deploy_live", False))
        fix_cad = mod.design_parametric_cad("cad_slotted_fixture", "slotted_fixture", fix_mat)
        wp_cad = mod.design_parametric_cad("cad_machined_shaft", "machined_shaft", wp_mat)
        fix_usd = mod.convert_cad_to_simready_usd(fix_cad["step_path"], "cad_slotted_fixture", fix_mat)
        wp_usd = mod.convert_cad_to_simready_usd(wp_cad["step_path"], "cad_machined_shaft", wp_mat)
        fix_ver = mod.verify_asset_dual_7a(fix_usd["root_usd_path"], promote_to_registry=True)
        wp_ver = mod.verify_asset_dual_7a(wp_usd["root_usd_path"], promote_to_registry=True)
        workcell = mod.compose_omniverse_workcell_stage(
            workcell_id=workcell_id,
            fixture_usd_path=fix_usd["root_usd_path"],
            workpiece_usd_path=wp_usd["root_usd_path"],
            deploy_live=deploy_live,
        )
        return {
            "fixture": {"cad": fix_cad, "simready": fix_usd, "verification_7a": fix_ver},
            "workpiece": {"cad": wp_cad, "simready": wp_usd, "verification_7a": wp_ver},
            "workcell": workcell,
            "all_gates_passed": bool(fix_ver["passed"] and wp_ver["passed"]),
        }
    raise ValueError(f"Unsupported CAD ecosystem tool: {tool_name}")


async def call_cad_ecosystem_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_execute_sync, tool_name, arguments)


# ---------------------------------------------------------------------------
# FastAPI Router (/api/v1/cad_pipeline)
# ---------------------------------------------------------------------------

router = APIRouter()


class DesignCadRequest(BaseModel):
    part_id: str
    template: str = "slotted_fixture"
    material: str = "6061_t6_aluminum"
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ConvertCadRequest(BaseModel):
    cad_path: str
    part_id: Optional[str] = None
    material: str = "6061_t6_aluminum"
    default_physics_variant: str = "rigid_convex"


class Verify7aRequest(BaseModel):
    usd_path: str
    drop_height_m: float = 0.20
    steps: int = 180
    promote_to_registry: bool = True


class ComposeWorkcellRequest(BaseModel):
    workcell_id: str
    fixture_usd_path: str
    workpiece_usd_path: str
    gsplat_backdrop_usd: Optional[str] = None
    deploy_live: bool = False


class RunE2ERequest(BaseModel):
    workcell_id: str = "nemotron_cad_cell_v1"
    fixture_material: str = "6061_t6_aluminum"
    workpiece_material: str = "pla"
    deploy_live: bool = False


@router.post("/design")
async def api_design_cad(req: DesignCadRequest):
    try:
        return await call_cad_ecosystem_tool("design_parametric_cad", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/convert")
async def api_convert_cad(req: ConvertCadRequest):
    try:
        return await call_cad_ecosystem_tool("convert_cad_to_simready_usd", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/verify7a")
async def api_verify_7a(req: Verify7aRequest):
    try:
        return await call_cad_ecosystem_tool("verify_asset_dual_7a", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/compose_workcell")
async def api_compose_workcell(req: ComposeWorkcellRequest):
    try:
        return await call_cad_ecosystem_tool("compose_omniverse_workcell_stage", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/e2e")
async def api_run_e2e(req: RunE2ERequest):
    try:
        return await call_cad_ecosystem_tool("run_cad_to_sim_e2e", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
