"""MCP tools and FastAPI router for CAD-to-SimReady, Isaac Sim 7.0.0a1 dual-physics, and OpenUSD workcell composition."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
_PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "cad_to_sim_pipeline.py"
_R2_HYBRID_SCRIPT = REPO_ROOT / "scripts" / "r2_hybrid_ecosystem_pipeline.py"


def _load_pipeline_module():
    spec = importlib.util.spec_from_file_location("isaac_assist_cad_to_sim_pipeline", _PIPELINE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CAD pipeline script at {_PIPELINE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_r2_hybrid_module():
    spec = importlib.util.spec_from_file_location("isaac_assist_r2_hybrid_pipeline", _R2_HYBRID_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load R2 hybrid pipeline script at {_R2_HYBRID_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CAD_TOOL_NAMES = frozenset(
    {
        "design_parametric_cad",
        "convert_cad_to_simready_usd",
        "verify_asset_dual_7a",
        "compose_omniverse_workcell_stage",
        "render_or_simulate_in_blender_ovrtx",
        "run_cad_to_sim_e2e",
        "extract_cad_creator_r2_hybrid",
        "simulate_mujoco_r2_grasp_and_mate",
        "run_full_cad_mujoco_isaac_blender_showcase",
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
            "name": "render_or_simulate_in_blender_ovrtx",
            "description": (
                "Import one or more SimReady OpenUSD CAD assets into Blender 5.2+ with NVIDIA ovrtx-blender-example, "
                "convert physics via bpy.ops.ov.physics_convert_simready() (preserving authored mass, COM, inertia, "
                "friction, and convexDecomposition colliders), run external OVPhysX sidecar simulation, and render a "
                "path-traced OVRTX_EXAMPLE PNG frame + interactive .blend file."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "usd_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of SimReady .usda/.usdc/.usd paths to import into the Blender workcell.",
                    },
                    "assets": {
                        "type": "array",
                        "description": (
                            "Optional per-asset placement overrides: "
                            "[{usd_path, name, location:[x,y,z], rotation_euler_deg:[rx,ry,rz], role:'STATIC'|'DYNAMIC'|'KINEMATIC'}]."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "usd_path": {"type": "string"},
                                "name": {"type": "string"},
                                "location": {"type": "array", "items": {"type": "number"}},
                                "rotation_euler_deg": {"type": "array", "items": {"type": "number"}},
                                "role": {"type": "string", "enum": ["STATIC", "DYNAMIC", "KINEMATIC"], "default": "DYNAMIC"},
                            },
                            "required": ["usd_path"],
                        },
                    },
                    "job_id": {"type": "string", "description": "Optional output folder/blend identifier."},
                    "simulate_frames": {
                        "type": "integer",
                        "default": 48,
                        "description": "Frames to simulate in OVPhysX before rendering (0 or 1 skips physics simulation).",
                    },
                    "substeps": {"type": "integer", "default": 2},
                    "resolution": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "default": [1280, 720],
                        "description": "[width, height] render resolution in pixels.",
                    },
                    "samples": {"type": "integer", "default": 32},
                    "output_dir": {"type": "string"},
                    "launch_gui": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, also launch an interactive Blender + ovrtx GUI window on the saved .blend.",
                    },
                },
            },
        },
        {
            "name": "run_cad_to_sim_e2e",
            "description": (
                "Execute the full CAD -> SimReady USD -> 7.0.0a1 ovphysx+ovnewton verification -> "
                "governed OpenUSD workcell (and optional Blender ovrtx verification) pipeline end-to-end."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workcell_id": {"type": "string", "default": "nemotron_cad_cell_v1"},
                    "fixture_material": {"type": "string", "default": "6061_t6_aluminum"},
                    "workpiece_material": {"type": "string", "default": "pla"},
                    "deploy_live": {"type": "boolean", "default": False},
                    "include_blender_ovrtx": {"type": "boolean", "default": False},
                },
            },
        },
        {
            "name": "extract_cad_creator_r2_hybrid",
            "description": (
                "Read-only OpenCASCADE (OCP) extraction of CAD Creator's 9-link articulated R2 3-Finger Tactile Gripper "
                "(with Contactile 3DFBS-12T fingertip sensors) + B-Rep telemetry cartridge & chamfered mating dock into "
                "per-link .step/.stl, URDF, exact GProp mass/inertia, and B-Rep clearance verification."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "output_dir": {"type": "string"},
                    "cad_creator_root": {"type": "string"},
                },
            },
        },
        {
            "name": "simulate_mujoco_r2_grasp_and_mate",
            "description": (
                "Compile SHA-256-stamped MJCF from the CAD Creator R2 Gripper manifest and run closed-loop MuJoCo 3.11 "
                "3-finger tactile grasp, aerial lift, lateral transport, and chamfered dock insertion with 3 Contactile "
                "<touch> sensors + 1280x720 offscreen renders."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "manifest_path": {"type": "string"},
                    "output_dir": {"type": "string"},
                },
                "required": ["manifest_path"],
            },
        },
        {
            "name": "run_full_cad_mujoco_isaac_blender_showcase",
            "description": (
                "Run the complete 5-system showcase end-to-end: CAD Creator OpenCASCADE (R2 3-Finger Tactile Gripper + "
                "Workpiece) -> MuJoCo 3.11 closed-loop tactile grasp & insertion -> Isaac Sim 7.0.0a1 dual-engine "
                "verification + live Omniverse Kit & ROS 2 Jazzy /tf -> Blender 5.2.1 + ovrtx path-traced renders & .blend."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "deploy_live_isaac": {"type": "boolean", "default": True},
                    "blender_resolution": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "default": [1280, 720],
                    },
                    "blender_samples": {"type": "integer", "default": 32},
                    "output_dir": {"type": "string"},
                    "open_visible_at_end": {
                        "type": "boolean",
                        "default": True,
                        "description": "Pop open live MuJoCo 3.11 viewer, Blender 5.2.1 ovrtx GUI, Isaac Sim playback, and HTML Mission Control report at completion.",
                    },
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
    if tool_name == "render_or_simulate_in_blender_ovrtx":
        res = arguments.get("resolution") or [1280, 720]
        return mod.render_or_simulate_in_blender_ovrtx(
            usd_paths=arguments.get("usd_paths"),
            assets=arguments.get("assets"),
            job_id=arguments.get("job_id"),
            simulate_frames=int(arguments.get("simulate_frames", 48)),
            substeps=int(arguments.get("substeps", 2)),
            resolution=(int(res[0]), int(res[1])),
            samples=int(arguments.get("samples", 32)),
            output_dir=arguments.get("output_dir"),
            launch_gui=bool(arguments.get("launch_gui", False)),
        )
    if tool_name == "run_cad_to_sim_e2e":
        workcell_id = arguments.get("workcell_id", "nemotron_cad_cell_v1")
        fix_mat = arguments.get("fixture_material", "6061_t6_aluminum")
        wp_mat = arguments.get("workpiece_material", "pla")
        deploy_live = bool(arguments.get("deploy_live", False))
        include_blender = bool(arguments.get("include_blender_ovrtx", False))
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
        out: Dict[str, Any] = {
            "fixture": {"cad": fix_cad, "simready": fix_usd, "verification_7a": fix_ver},
            "workpiece": {"cad": wp_cad, "simready": wp_usd, "verification_7a": wp_ver},
            "workcell": workcell,
            "all_gates_passed": bool(fix_ver["passed"] and wp_ver["passed"]),
        }
        if include_blender:
            out["blender_ovrtx"] = mod.render_or_simulate_in_blender_ovrtx(
                usd_paths=[fix_usd["root_usd_path"], wp_usd["root_usd_path"]],
                job_id=f"{workcell_id}_ovrtx",
            )
        return out
    if tool_name == "extract_cad_creator_r2_hybrid":
        r2_mod = _load_r2_hybrid_module()
        return r2_mod.extract_cad_creator_r2_hybrid(
            output_dir=arguments.get("output_dir"),
            cad_creator_root=arguments.get("cad_creator_root"),
        )
    if tool_name == "simulate_mujoco_r2_grasp_and_mate":
        r2_mod = _load_r2_hybrid_module()
        return r2_mod.simulate_mujoco_r2_grasp_and_mate(
            manifest_or_path=arguments["manifest_path"],
            output_dir=arguments.get("output_dir"),
        )
    if tool_name == "run_full_cad_mujoco_isaac_blender_showcase":
        r2_mod = _load_r2_hybrid_module()
        res = arguments.get("blender_resolution") or [1280, 720]
        return r2_mod.run_full_cad_mujoco_isaac_blender_showcase(
            deploy_live_isaac=bool(arguments.get("deploy_live_isaac", True)),
            blender_resolution=(int(res[0]), int(res[1])),
            blender_samples=int(arguments.get("blender_samples", 32)),
            output_dir=arguments.get("output_dir"),
            open_visible_at_end=bool(arguments.get("open_visible_at_end", True)),
        )
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


class BlenderOvrtxRequest(BaseModel):
    usd_paths: Optional[List[str]] = None
    assets: Optional[List[Dict[str, Any]]] = None
    job_id: Optional[str] = None
    simulate_frames: int = 48
    substeps: int = 2
    resolution: List[int] = Field(default_factory=lambda: [1280, 720])
    samples: int = 32
    output_dir: Optional[str] = None
    launch_gui: bool = False


class RunE2ERequest(BaseModel):
    workcell_id: str = "nemotron_cad_cell_v1"
    fixture_material: str = "6061_t6_aluminum"
    workpiece_material: str = "pla"
    deploy_live: bool = False
    include_blender_ovrtx: bool = False


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


@router.post("/blender_ovrtx")
async def api_blender_ovrtx(req: BlenderOvrtxRequest):
    try:
        return await call_cad_ecosystem_tool("render_or_simulate_in_blender_ovrtx", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class R2ExtractRequest(BaseModel):
    output_dir: Optional[str] = None
    cad_creator_root: Optional[str] = None


class R2MujocoRequest(BaseModel):
    manifest_path: str
    output_dir: Optional[str] = None


class R2ShowcaseRequest(BaseModel):
    deploy_live_isaac: bool = True
    blender_resolution: List[int] = Field(default_factory=lambda: [1280, 720])
    blender_samples: int = 32
    output_dir: Optional[str] = None
    open_visible_at_end: bool = True


@router.post("/e2e")
async def api_run_e2e(req: RunE2ERequest):
    try:
        return await call_cad_ecosystem_tool("run_cad_to_sim_e2e", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/r2_extract")
async def api_r2_extract(req: R2ExtractRequest):
    try:
        return await call_cad_ecosystem_tool("extract_cad_creator_r2_hybrid", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/r2_mujoco")
async def api_r2_mujoco(req: R2MujocoRequest):
    try:
        return await call_cad_ecosystem_tool("simulate_mujoco_r2_grasp_and_mate", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/dashboard", response_class=HTMLResponse)
async def api_r2_dashboard():
    dash = REPO_ROOT / "workspace" / "r2_hybrid_showcase" / "r2_showcase_dashboard.html"
    if not dash.is_file():
        r2_mod = _load_r2_hybrid_module()
        summary_file = REPO_ROOT / "workspace" / "r2_hybrid_showcase" / "r2_five_system_summary.json"
        if not summary_file.is_file():
            raise HTTPException(status_code=404, detail="Run r2_showcase first to generate dashboard.")
        import json as _json
        r2_mod.generate_r2_visual_dashboard_html(_json.loads(summary_file.read_text(encoding="utf-8")), dash.parent)
    return HTMLResponse(content=dash.read_text(encoding="utf-8"))


@router.post("/r2_showcase")
async def api_r2_showcase(req: R2ShowcaseRequest):
    try:
        return await call_cad_ecosystem_tool("run_full_cad_mujoco_isaac_blender_showcase", req.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
