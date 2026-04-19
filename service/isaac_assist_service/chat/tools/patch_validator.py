"""
patch_validator.py
-------------------
Pre-flight validation of generated Python code before it reaches Kit RPC.

Catches known-bad patterns that cause OmniGraph failures, PhysX crashes,
and USD runtime errors — preventing the LLM retry loop where the model
generates broken patches and keeps retrying the same mistakes.

Each validator returns a list of PatchIssue objects. The caller can decide
whether to block (severity=error) or warn (severity=warning).
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PatchIssue:
    """A problem found in generated code."""
    severity: str        # "error" or "warning"
    rule: str            # short identifier
    message: str         # human-readable explanation
    fix_hint: str = ""   # suggestion for the LLM


# ---------------------------------------------------------------------------
# OmniGraph validators
# ---------------------------------------------------------------------------

# Detect direct double3→double wiring without Break3Vector
_RE_SUBSCRIBE_TWIST = re.compile(
    r"SubscribeTwist[^)]*\.outputs?:(?:linear|angular)Velocity", re.I
)
_RE_DIFF_CONTROLLER = re.compile(
    r"DiffController[^)]*\.inputs?:(?:linear|angular)Velocity", re.I
)
_RE_BREAK_VECTOR = re.compile(r"Break3?Vector|BreakVector3|Break3Vector", re.I)

# Detect both ends of a direct connection: ("...SubscribeTwist...Velocity", "...DiffController...Velocity")
_RE_DIRECT_TWIST_TO_DIFF = re.compile(
    r"""['"]([^'"]*SubscribeTwist[^'"]*(?:linear|angular)Velocity[^'"]*)['"]"""
    r"""\s*,\s*"""
    r"""['"]([^'"]*DiffController[^'"]*(?:linear|angular)Velocity[^'"]*)['"]""",
    re.I,
)


def _check_omnigraph_type_mismatch(code: str) -> List[PatchIssue]:
    """ROS2SubscribeTwist outputs double3; DifferentialController expects scalar double."""
    issues = []
    if _RE_DIRECT_TWIST_TO_DIFF.search(code):
        issues.append(PatchIssue(
            severity="error",
            rule="og_double3_to_double",
            message="Direct connection from ROS2SubscribeTwist (double3) to DifferentialController (double). "
                    "This causes an OmniGraph type mismatch error.",
            fix_hint="Insert Break3Vector nodes between SubscribeTwist and DiffController. "
                     "Wire linearVelocity→Break3Vector→x→DiffController.linearVelocity, "
                     "angularVelocity→Break3Vector→z→DiffController.angularVelocity.",
        ))
    return issues


# Detect legacy omni.isaac.* namespace that should be isaacsim.*
_RE_LEGACY_OG_NAMESPACE = re.compile(
    r"""['"]omni\.isaac\.(?:ros2_bridge|core_nodes|wheeled_robots)\.[A-Z]""",
)


def _check_omnigraph_legacy_namespace(code: str) -> List[PatchIssue]:
    """Isaac Sim 5.1 uses isaacsim.* namespace, not omni.isaac.*."""
    issues = []
    matches = _RE_LEGACY_OG_NAMESPACE.findall(code)
    if matches:
        issues.append(PatchIssue(
            severity="error",
            rule="og_legacy_namespace",
            message=f"Using legacy omni.isaac.* OmniGraph node types. "
                    f"Isaac Sim 5.1 requires isaacsim.* namespace. Found: {matches[:3]}",
            fix_hint="Replace omni.isaac.ros2_bridge.* with isaacsim.ros2.bridge.*, "
                     "omni.isaac.core_nodes.* with isaacsim.core.nodes.*, "
                     "omni.isaac.wheeled_robots.* with isaacsim.robot.wheeled_robots.*.",
        ))
    return issues


# Detect DiffController.outputs:execOut → ArticulationController.inputs:execIn
# (DifferentialController has no execution outputs)
_RE_DIFF_EXEC_OUT = re.compile(
    r"""['"][^'"]*DiffController[^'"]*\.outputs?:exec""", re.I
)


def _check_omnigraph_diff_exec_out(code: str) -> List[PatchIssue]:
    """DifferentialController has no execution output port."""
    issues = []
    if _RE_DIFF_EXEC_OUT.search(code):
        issues.append(PatchIssue(
            severity="error",
            rule="og_diff_no_exec_out",
            message="DifferentialController has no outputs:execOut port. "
                    "Cannot chain execution through DiffController.",
            fix_hint="Connect OnPlaybackTick.outputs:tick → each node's execIn independently, "
                     "or use a separate execution path.",
        ))
    return issues


# Detect usePath attribute (doesn't exist in Isaac Sim 5.1 ArticulationController)
_RE_USE_PATH = re.compile(
    r"""ArticulationController[^'"]*\.inputs?:usePath""", re.I
)


def _check_omnigraph_use_path(code: str) -> List[PatchIssue]:
    issues = []
    if _RE_USE_PATH.search(code):
        issues.append(PatchIssue(
            severity="error",
            rule="og_use_path_missing",
            message="ArticulationController.inputs:usePath does not exist in Isaac Sim 5.1.",
            fix_hint="Use SET_VALUES with 'inputs:robotPath' instead of 'inputs:usePath'.",
        ))
    return issues


# Detect get_node_path / get_attribute_count (wrong OG node API)
_RE_BAD_OG_API = re.compile(
    r"""\.get_node_path\(\)|\.get_attribute_count\(\)|\.get_node_type_registry\(\)"""
)


def _check_omnigraph_bad_api(code: str) -> List[PatchIssue]:
    issues = []
    if _RE_BAD_OG_API.search(code):
        issues.append(PatchIssue(
            severity="error",
            rule="og_bad_api",
            message="Using non-existent OmniGraph API methods (get_node_path, get_attribute_count, etc.).",
            fix_hint="Use node.get_prim_path() for path and node.get_attributes() for attribute listing.",
        ))
    return issues


# Detect pipeline_stage with FLATCACHE_SHARED (wrong enum name)
_RE_FLATCACHE_SHARED = re.compile(r"GRAPH_BACKING_TYPE_FLATCACHE_SHARED")


def _check_omnigraph_backing_type(code: str) -> List[PatchIssue]:
    issues = []
    if _RE_FLATCACHE_SHARED.search(code):
        issues.append(PatchIssue(
            severity="warning",
            rule="og_backing_type_compat",
            message="GRAPH_BACKING_TYPE_FLATCACHE_SHARED may cause pipeline_stage errors "
                    "in some Isaac Sim builds.",
            fix_hint="Use hasattr() guard to try FABRIC_SHARED first, then FLATCACHING.",
        ))
    return issues


# ---------------------------------------------------------------------------
# Nova Carter validators
# ---------------------------------------------------------------------------

_RE_WRONG_CARTER_JOINTS = re.compile(
    r"""joint_drive_f[lr]|joint_front_left|joint_front_right|joint_rear_left|joint_rear_right""",
    re.I,
)


def _check_carter_joint_names(code: str) -> List[PatchIssue]:
    """Nova Carter uses joint_wheel_left/right, not joint_drive_fl/fr."""
    issues = []
    if _RE_WRONG_CARTER_JOINTS.search(code):
        # Only flag if this is Carter-related code
        if re.search(r"carter|nova.?carter|NovaCarter", code, re.I):
            issues.append(PatchIssue(
                severity="error",
                rule="carter_wrong_joints",
                message="Wrong Nova Carter joint names. Nova Carter does NOT have "
                        "joint_drive_fl/fr joints.",
                fix_hint="Use: joint_wheel_left, joint_wheel_right (front drive), "
                         "joint_caster_swivel_left/right, joint_caster_wheel_left/right (rear passive).",
            ))
    return issues


# ---------------------------------------------------------------------------
# PhysX validators
# ---------------------------------------------------------------------------

_RE_TRIANGLE_MESH_COLLISION = re.compile(
    r"""\.Set\s*\(\s*['"](?:triangle|triangleMesh|meshSimplification|none)['"]""", re.I
)
_RE_DYNAMIC_WHEEL = re.compile(
    r"""wheel|caster""", re.I
)


def _check_triangle_mesh_on_wheels(code: str) -> List[PatchIssue]:
    """PhysX rejects triangle mesh collision on dynamic bodies (wheels)."""
    issues = []
    if _RE_DYNAMIC_WHEEL.search(code) and _RE_TRIANGLE_MESH_COLLISION.search(code):
        issues.append(PatchIssue(
            severity="error",
            rule="physx_triangle_mesh_wheel",
            message="Triangle mesh collision on wheel/caster prims will fail. "
                    "PhysX does not support triangle mesh on dynamic bodies.",
            fix_hint="Set physics:approximation to 'convexHull' on all wheel and caster prims.",
        ))
    return issues


# ---------------------------------------------------------------------------
# USD validators
# ---------------------------------------------------------------------------

_RE_USES_OMNI_USD = re.compile(r"omni\.usd")
_RE_IMPORT_OMNI_USD = re.compile(r"import\s+omni\.usd|from\s+omni\.usd|import\s+omni\b")


def _check_missing_import_omni_usd(code: str) -> List[PatchIssue]:
    """Code uses omni.usd but doesn't import it."""
    issues = []
    if _RE_USES_OMNI_USD.search(code) and not _RE_IMPORT_OMNI_USD.search(code):
        issues.append(PatchIssue(
            severity="error",
            rule="missing_import_omni_usd",
            message="Code references omni.usd but does not import it. "
                    "Will fail with 'name omni is not defined'.",
            fix_hint="Add 'import omni.usd' at the top of the script.",
        ))
    return issues


_RE_RAW_LIST_SET = re.compile(
    r"""\.Set\s*\(\s*\[[\d.,\s-]+\]\s*\)"""
)
_RE_XFORM_SET = re.compile(
    r"""xformOp:translate|xformOp:scale|xformOp:rotate"""
)


def _check_raw_list_for_vec(code: str) -> List[PatchIssue]:
    """Setting xform attributes with raw lists instead of Gf.Vec3d causes type mismatch."""
    issues = []
    if _RE_XFORM_SET.search(code) and _RE_RAW_LIST_SET.search(code):
        issues.append(PatchIssue(
            severity="warning",
            rule="usd_raw_list_xform",
            message="Setting xform attribute with a raw list [x,y,z] instead of Gf.Vec3d. "
                    "This causes a type mismatch error.",
            fix_hint="Use Gf.Vec3d(x, y, z) or Gf.Vec3f(x, y, z) for xform attributes.",
        ))
    return issues


_RE_ADD_TRANSLATE_OP = re.compile(r"\.AddTranslateOp\(\)")
_RE_ADD_SCALE_OP = re.compile(r"\.AddScaleOp\(\)")
_RE_ADD_ROTATE_OP = re.compile(r"\.AddRotate\w+Op\(\)")
_RE_SAFE_SET = re.compile(r"_safe_set_translate|_safe_set_scale|_safe_set_rotate")
_RE_GET_ORDERED = re.compile(r"GetOrderedXformOps")


def _check_unsafe_add_xform_op(code: str) -> List[PatchIssue]:
    """Direct AddTranslateOp/AddScaleOp on referenced prims crashes."""
    issues = []
    has_add_op = (_RE_ADD_TRANSLATE_OP.search(code) or
                  _RE_ADD_SCALE_OP.search(code) or
                  _RE_ADD_ROTATE_OP.search(code))
    has_safe_guard = _RE_SAFE_SET.search(code) or _RE_GET_ORDERED.search(code)

    if has_add_op and not has_safe_guard:
        # Only warn — some fresh prims legitimately need these calls
        issues.append(PatchIssue(
            severity="warning",
            rule="usd_unsafe_add_xform_op",
            message="Using AddTranslateOp/AddScaleOp/AddRotateOp without checking "
                    "if xform ops already exist. Will crash on referenced prims.",
            fix_hint="Use _safe_set_translate() helpers or check GetOrderedXformOps() first.",
        ))
    return issues


# Detect Kit CreatePrim/CreateMeshPrim commands without explicit prim_path.
# These default to "/Cube", "/Cube_01", etc. at the ROOT (not /World) at origin.
# The agent then typically runs MovePrim/TransformPrimSRT on a DIFFERENT path
# (the intended one like /World/Cube_3) — silently creating orphaned prims at
# root + origin while confidently reporting "placed at <correct position>".
#
# Real failure observed 2026-04-19: agent emitted
#     omni.kit.commands.execute('CreateMeshPrimWithDefaultXform', prim_type='Cube')
#     omni.kit.commands.execute('MovePrim', path_from='/World/Cube', path_to='/World/Cube_3')
# This only works the FIRST time; second invocation creates /World/Cube again
# but MovePrim may fail silently, or the xform ends up on the wrong path.
_RE_UNSAFE_CREATE_PRIM_CMD = re.compile(
    r"""omni\.kit\.commands\.execute\s*\(\s*["'](Create(?:MeshPrimWithDefaultXform|MeshPrim|Prim))["']\s*,\s*([^)]*)\)""",
    re.I | re.S,
)


def _check_create_prim_default_path(code: str) -> List[PatchIssue]:
    """Block omni.kit.commands.execute('Create...Prim...') without prim_path=
    AND block CreateMeshPrimWithDefaultXform regardless of prim_path because
    it always authors mesh-geometry attributes (points/faceVertexCounts/normals)
    on top of the requested TypeName, producing a hybrid prim that Hydra
    renders as distorted garbage. Observed 2026-04-19: agent's Cube 3 had
    both TypeName='Cube' AND mesh-data attrs; the render was a warped mesh.
    """
    issues: List[PatchIssue] = []
    for match in _RE_UNSAFE_CREATE_PRIM_CMD.finditer(code):
        cmd_name = match.group(1)
        args_blob = match.group(2)
        # (1) No-path variant — always broken, regardless of command flavor.
        if "prim_path" not in args_blob:
            issues.append(PatchIssue(
                severity="error",
                rule="usd_create_prim_no_path",
                message=f"omni.kit.commands.execute('{cmd_name}', ...) called without "
                        f"prim_path=... — this defaults to /Cube, /Cube_01 at the stage "
                        f"root at origin (0,0,0), NOT the /World/<Name> path your xform "
                        f"code below targets. The prim lands orphaned while your reply "
                        f"claims it was placed correctly.",
                fix_hint="Use stage.DefinePrim('/World/<Name>', 'Cube') (or "
                         "UsdGeom.Cube.Define(stage, '/World/<Name>')), then set the "
                         "xform on the SAME path via _safe_set_translate. Do NOT use "
                         "Kit's Create*Prim command + MovePrim rename pattern — it's "
                         "unreliable and produces silent failures.",
            ))
            continue
        # (2) WithDefaultXform variant — even WITH prim_path, it authors mesh
        # geometry attrs (points, faceVertexCounts, normals, primvars:st,
        # subdivisionScheme) on top of a Cube TypeName. Hydra then renders
        # the mesh data, not the parametric Cube, and any Cube-TypeName
        # consumer (size attribute, extent) desyncs from what's visible.
        if cmd_name == "CreateMeshPrimWithDefaultXform":
            issues.append(PatchIssue(
                severity="error",
                rule="usd_create_mesh_prim_with_default_xform",
                message="CreateMeshPrimWithDefaultXform authors mesh-geometry "
                        "attributes (points, faceVertexCounts, normals, subdivisionScheme) "
                        "on top of the requested TypeName. The result is a hybrid prim "
                        "that renders as distorted geometry because Hydra picks the "
                        "mesh data while tools reading 'size' see the parametric type. "
                        "Observed 2026-04-19: Cube 3 rendered as a warped star shape.",
                fix_hint="Use UsdGeom.Cube.Define(stage, '/World/<Name>') and set size "
                         "via GetSizeAttr().Set(1.0). For spheres/cylinders/cones use "
                         "UsdGeom.Sphere.Define etc. Never CreateMeshPrimWithDefaultXform "
                         "for parametric shapes — reserved for actual mesh import.",
            ))
    return issues


# Detect DeletePrims(paths=[X]) followed by CreatePrim/DefinePrim targeting
# the same path in the SAME script. Observed 2026-04-19: DeletePrims leaves
# attribute specs in the session layer; recreating on the same path then
# composes the stale specs back onto the new prim, producing ghost mesh data
# that Hydra renders instead of the new geometry. The fix is either (a) use
# a fresh unique path name, or (b) explicitly wipe the session-layer spec
# before recreate.
_RE_DELETE_PRIMS = re.compile(
    r"""DeletePrims[^)]*?paths\s*=\s*\[([^\]]*)\]""", re.I | re.S,
)
_RE_PATH_LITERAL = re.compile(r"""["']([^"']+)["']""")


def _check_delete_then_create_same_path(code: str) -> List[PatchIssue]:
    """Warn when the same path is deleted and recreated in one script."""
    issues: List[PatchIssue] = []
    deleted_paths: set = set()
    for m in _RE_DELETE_PRIMS.finditer(code):
        for p in _RE_PATH_LITERAL.findall(m.group(1)):
            deleted_paths.add(p)
    if not deleted_paths:
        return issues

    # Find recreation patterns for any of the deleted paths.
    recreate_patterns = [
        re.compile(rf"""DefinePrim\s*\(\s*stage\s*,\s*["']{re.escape(p)}["']""")
        for p in deleted_paths
    ] + [
        re.compile(rf"""(?:CreatePrim|CreateMeshPrim|CreateMeshPrimWithDefaultXform)"""
                   rf"""[^)]*?prim_path\s*=\s*["']{re.escape(p)}["']""", re.I | re.S)
        for p in deleted_paths
    ] + [
        re.compile(rf"""Cube\.Define\s*\(\s*stage\s*,\s*["']{re.escape(p)}["']""")
        for p in deleted_paths
    ]
    for pat in recreate_patterns:
        if pat.search(code):
            issues.append(PatchIssue(
                severity="warning",
                rule="usd_delete_then_recreate_same_path",
                message="DeletePrims + recreate on the same path in one script. "
                        "Kit's DeletePrims does not wipe attribute specs from the "
                        "session layer, so session-layer opinions from the old prim "
                        "compose back onto the new prim — Hydra renders the ghost "
                        "geometry. This is what made Cube 3 render as a warped mesh "
                        "on 2026-04-19 even after rebuild.",
                fix_hint="Either (a) recreate on a fresh path ('/World/CubeThree' "
                         "instead of '/World/Cube3'), then optionally rename via "
                         "MovePrim AFTER verifying PrimStack is clean; or (b) walk "
                         "stage.GetLayerStack() and call layer.GetPrimAtPath(path).Clear() "
                         "on every non-root layer to wipe stale specs before recreate.",
            ))
            break  # one warning per script
    return issues


# Detect fabricated Kit commands. Observed 2026-04-19: agent emitted
#   omni.kit.commands.execute("TransformPrimCommand", path=..., new_translation=...)
# which doesn't exist. Real names: "TransformPrimSRT" or "TransformPrim".
# The call silently failed (success=false) but the agent kept going and the
# user saw no viewport change.
_KNOWN_KIT_COMMANDS = {
    "CreatePrim", "CreateMeshPrim", "CreateMeshPrimWithDefaultXform",
    "DeletePrims", "DeletePrim", "MovePrim", "MovePrims",
    "TransformPrim", "TransformPrimSRT",
    "ChangeProperty", "ChangePropertyCommand",
    "CopyPrim", "CopyPrims", "GroupPrims", "UngroupPrims",
    "SetDefaultPrim", "ToggleActivePrims", "TogglePayLoadLoadSelectedPrims",
    "AddReference", "RemoveReference", "AddPayload", "RemovePayload",
    "BindMaterial", "UnbindMaterial", "CreateMaterial",
    "SetStageUpAxis", "SetStageMetersPerUnit",
    "Select", "SelectNone", "SelectPrims",
    "AddXformOp", "AddXformComposition",
}
# Matches omni.kit.commands.execute("Something", ...)
_RE_KIT_COMMAND_NAME = re.compile(
    r"""omni\.kit\.commands\.execute\s*\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""
)


def _check_kit_command_name(code: str) -> List[PatchIssue]:
    """Block fabricated Kit command names that silently fail at RPC time."""
    issues: List[PatchIssue] = []
    seen = set()
    for m in _RE_KIT_COMMAND_NAME.finditer(code):
        name = m.group(1)
        if name in _KNOWN_KIT_COMMANDS or name in seen:
            continue
        seen.add(name)
        # Heuristic for common LLM fabrications: "Command" suffix on valid name
        hint = ""
        # Specific fix hint wins over the generic suffix-strip hint.
        if name == "TransformPrimCommand":
            hint = " Use 'TransformPrimSRT' (translate/rotate/scale) or 'TransformPrim' (full matrix)."
        elif name.endswith("Command") and name[:-7] in _KNOWN_KIT_COMMANDS:
            hint = f" Did you mean '{name[:-7]}'?"
        issues.append(PatchIssue(
            severity="error",
            rule="kit_unknown_command",
            message=f"omni.kit.commands.execute('{name}', ...) — '{name}' is not a "
                    f"registered Kit command. The call will fail silently (success=false) "
                    f"at RPC time while the reply claims success.{hint}",
            fix_hint=(
                "Use the stage/USD API directly (UsdGeom.Xformable.AddTranslateOp, "
                "stage.DefinePrim, prim.GetAttribute().Set) instead of Kit commands, "
                "OR verify the command name against omni.kit.commands in the target build."
            ),
        ))
    return issues


# Detect og.Controller.edit(...) passing GraphBackingType as pipeline_stage.
# Observed 2026-04-19: both the LLM agent AND a human debugger hit this. The
# pipeline_stage parameter takes GraphPipelineStage (SIMULATION/ON_DEMAND/
# PRE_SIMULATION/POST_SIMULATION), NOT GraphBackingType (FABRIC_SHARED /
# FLATCACHING). The arg name looks close enough to GraphBackingType that
# models pattern-match wrongly. Error message is "incompatible function
# arguments" which is confusing — validator catches it at authoring time
# with the correct fix.
_RE_PIPELINE_STAGE_WRONG = re.compile(
    # Matches both kwarg form `pipeline_stage=GraphBackingType...`
    # and dict-literal form `"pipeline_stage": GraphBackingType...`
    r"""["']?pipeline_stage["']?\s*[=:]\s*(?:og\.)?GraphBackingType""",
)
_RE_PIPELINE_STAGE_WRONG_VAR = re.compile(
    r"""GraphBackingType\.GRAPH_BACKING_TYPE_(?:FABRIC_SHARED|FLATCACHE_SHARED|FLATCACHING)"""
)


def _check_pipeline_stage_enum_mismatch(code: str) -> List[PatchIssue]:
    """Block GraphBackingType where GraphPipelineStage is required."""
    issues: List[PatchIssue] = []
    # Direct `pipeline_stage=GraphBackingType.*` — always wrong.
    if _RE_PIPELINE_STAGE_WRONG.search(code):
        issues.append(PatchIssue(
            severity="error",
            rule="og_pipeline_stage_enum",
            message="og.Controller.edit(..., pipeline_stage=GraphBackingType.*) — "
                    "pipeline_stage expects GraphPipelineStage, not GraphBackingType. "
                    "Runtime error is 'incompatible function arguments'.",
            fix_hint=(
                "Use `pipeline_stage=og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_SIMULATION` "
                "(most common) or omit the pipeline_stage parameter entirely — Kit uses "
                "a sane default. GraphBackingType is the STORAGE backend (Fabric vs "
                "Flatcache), set separately via the graph's backing_type setting."
            ),
        ))
    # Indirect pattern: a _backing variable built from GraphBackingType is passed
    # as pipeline_stage. Same bug class, caught via the backing_* resolver idiom
    # the agent's conveyor code uses.
    if (_RE_PIPELINE_STAGE_WRONG_VAR.search(code)
            and re.search(r"""["']?pipeline_stage["']?\s*[=:]\s*_?backing""", code)):
        issues.append(PatchIssue(
            severity="error",
            rule="og_pipeline_stage_enum_indirect",
            message="A variable resolved from GraphBackingType.* is being passed as "
                    "pipeline_stage. Same enum-confusion bug as above.",
            fix_hint="Replace the pipeline_stage argument with "
                     "og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_SIMULATION, or drop it.",
        ))
    return issues


# Detect ClearXformOpOrder() used as a "force-reset" hack. Observed 2026-04-19:
# agent wrote `xform.ClearXformOpOrder(); xform.AddTranslateOp().Set(pos)` and
# then couldn't understand why the write didn't land — ClearXformOpOrder()
# leaves the existing xformOp:translate attribute spec intact but drops it
# from the ordered-ops list, so AddTranslateOp() may re-add it as a different
# op (precision mismatch) and the scene shows the stale original value.
# Safer pattern: find existing translate op via GetOrderedXformOps() and
# re-Set() its value.
_RE_CLEAR_XFORM_OP_ORDER = re.compile(r"\.ClearXformOpOrder\s*\(\s*\)")


def _check_clear_xform_op_order(code: str) -> List[PatchIssue]:
    """Warn on ClearXformOpOrder() — it's almost always the wrong tool."""
    issues: List[PatchIssue] = []
    if _RE_CLEAR_XFORM_OP_ORDER.search(code):
        issues.append(PatchIssue(
            severity="warning",
            rule="usd_clear_xform_op_order",
            message="ClearXformOpOrder() clears the xformOpOrder list but leaves "
                    "xformOp:translate / xformOp:scale / xformOp:rotate attribute "
                    "specs intact. Subsequent AddTranslateOp() may create a duplicate "
                    "op spec with different precision — the old value is still in USD "
                    "and can override the new one in the viewport. Duplicate ops are "
                    "the common cause of 'the write didn't land' bugs.",
            fix_hint=(
                "Use GetOrderedXformOps() to find the existing translate op, then "
                "call .Set(new_value) on it. Only fall through to AddTranslateOp() "
                "if no translate op already exists."
            ),
        ))
    return issues


# Detect omni.usd.get_context().new_stage() inside a run_usd_script patch.
# Observed 2026-04-19 during conveyor+Franka scenario: agent's "clean
# rebuild" attempt called new_stage() mid-script, which WIPES every prim
# created by earlier successful patches in the same turn. Only the last
# patch's creations survive. The agent then reports "scene setup" success
# based on the final patch alone, and the user sees only a fraction of
# the intended cell. This is never what the user wants within a run_usd_script;
# a full stage reset should come from the user or /undo, not mid-patch.
_RE_NEW_STAGE = re.compile(
    r"""(?:omni\.usd\.get_context\(\)\.new_stage|get_context\(\)\.new_stage)\s*\("""
)


def _check_new_stage_mid_patch(code: str) -> List[PatchIssue]:
    """Block destructive new_stage() calls inside authoring patches."""
    if _RE_NEW_STAGE.search(code):
        return [PatchIssue(
            severity="error",
            rule="usd_destructive_new_stage",
            message="omni.usd.get_context().new_stage() inside run_usd_script WIPES "
                    "every prim created by earlier patches this turn. The user typically "
                    "loses all previous work from the same prompt, and the agent then "
                    "reports success based only on this patch. If you need a clean stage, "
                    "ask the user to use /undo or start a new scene — never call "
                    "new_stage() from a mutation script.",
            fix_hint=(
                "Remove the new_stage() call. Build on top of what's already in the "
                "stage. If duplicate prims at the same paths are the problem, use "
                "omni.kit.commands.execute('DeletePrims', paths=[...]) for the "
                "specific paths you want to replace."
            ),
        )]
    return []


# Detect the specific wrong Franka URL pattern. The agent routinely
# guesses "Isaac/Robots/Franka/franka.usd" but the correct 5.1 path is
# "Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd". The 5.1 endpoint
# 404s the shorter form. Caught repeatedly 2026-04-19.
_RE_WRONG_FRANKA_URL = re.compile(
    r"""Isaac/Robots/Franka(?!Robotics)/franka\.usd|"""
    r"""Isaac/Robots/FrankaEmika/"""
)


def _check_wrong_franka_url(code: str) -> List[PatchIssue]:
    """Block known-404 Franka asset URL patterns."""
    if _RE_WRONG_FRANKA_URL.search(code):
        return [PatchIssue(
            severity="error",
            rule="asset_wrong_franka_url",
            message="The Franka asset URL pattern you used returns HTTP 404 on the "
                    "Isaac Sim 5.1 asset endpoint. Isaac/Robots/Franka/franka.usd and "
                    "Isaac/Robots/FrankaEmika/panda/panda.usd both 404 — the correct "
                    "5.1 path is Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd.",
            fix_hint=(
                "Use: f'{assets_root}/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd' "
                "OR call lookup_api_deprecation(query='franka panda') to get the "
                "canonical URL + post-import verification recipe. After adding the "
                "reference, verify len(list(prim.GetAllChildren())) >= 10 before "
                "claiming the robot loaded."
            ),
        )]
    return []


# Detect deprecated omni.isaac.core.utils.* imports that are aliased in
# some Kit builds but not all. Safer to use the Isaac Sim 5.x
# isaacsim.core.* equivalents OR the pxr Usd APIs directly.
_RE_DEPRECATED_CORE_UTILS = re.compile(
    r"""from\s+omni\.isaac\.core\.utils\.(?:nucleus|stage|prims)\s+import|"""
    r"""import\s+omni\.isaac\.core\.utils\."""
)


def _check_deprecated_core_utils(code: str) -> List[PatchIssue]:
    """Warn on deprecated omni.isaac.core.utils.* imports."""
    if _RE_DEPRECATED_CORE_UTILS.search(code):
        return [PatchIssue(
            severity="warning",
            rule="deprecated_isaac_core_utils",
            message="omni.isaac.core.utils.{nucleus,stage,prims} is the 4.x utility "
                    "namespace. Some Isaac Sim 5.x builds alias it for compatibility; "
                    "others raise ModuleNotFoundError. Relying on this path is fragile.",
            fix_hint=(
                "Use the 5.x isaacsim.core.utils.* equivalents, OR author "
                "references directly via pxr: "
                "prim = stage.DefinePrim(path, 'Xform'); "
                "prim.GetReferences().AddReference(usd_url). "
                "For asset-root discovery in 5.x: "
                "carb.settings.get_settings().get('/persistent/isaac/asset_root/default')."
            ),
        )]
    return []


# Detect IsA() called on an Applied-API schema (e.g. UsdLux.LightAPI).
# IsA tests the prim TYPE (DomeLight, DistantLight, etc.) — it returns False
# for every light in the scene because lights are Dome/DistantLight TYPES
# that HAVE the LightAPI APPLIED, they aren't "of type LightAPI".
# Observed 2026-04-19: agent ran `if p.IsA(UsdLux.LightAPI)`, got 0 lights
# even though /World/DomeLight existed, and then fabricated "Isaac Sim must
# be using an automatic headlight" to rationalize the wrong data.
_RE_ISA_API_SCHEMA = re.compile(
    r"""\.IsA\s*\(\s*(Usd\w+|Physx\w+)\.(\w+API)\s*\)"""
)


def _check_isa_on_api_schema(code: str) -> List[PatchIssue]:
    """IsA() tests prim type; Applied-API schemas need HasAPI()."""
    issues: List[PatchIssue] = []
    for m in _RE_ISA_API_SCHEMA.finditer(code):
        module = m.group(1)
        api_name = m.group(2)
        issues.append(PatchIssue(
            severity="error",
            rule="usd_isa_on_api_schema",
            message=f"IsA({module}.{api_name}) always returns False. "
                    f"{api_name} is an Applied-API schema, not a prim type. "
                    "The check silently filters out every real prim that has the "
                    "API applied, then the caller rationalizes the empty result.",
            fix_hint=(
                f"Use prim.HasAPI({module}.{api_name}) to test for an applied API. "
                "For lights, the TYPE-side alternatives are UsdLux.DomeLight, "
                "UsdLux.DistantLight, UsdLux.SphereLight, UsdLux.RectLight — "
                "check via IsA(UsdLux.DomeLight) etc. or use UsdLux.LightAPI via HasAPI."
            ),
        ))
    return issues


# Detect CreateAttribute with wrong signature (Python type instead of Sdf.ValueTypeName)
_RE_CREATE_ATTR_BAD = re.compile(
    r"""\.CreateAttribute\s*\([^)]*,\s*(?:float|int|str|bool|list|tuple|Gf\.)\b"""
)


def _check_create_attribute_signature(code: str) -> List[PatchIssue]:
    """CreateAttribute requires Sdf.ValueTypeName, not Python types or Gf types."""
    issues = []
    if _RE_CREATE_ATTR_BAD.search(code):
        issues.append(PatchIssue(
            severity="error",
            rule="usd_create_attr_signature",
            message="CreateAttribute called with wrong type argument. "
                    "Requires Sdf.ValueTypeNames.*, not Python types or Gf classes.",
            fix_hint="Use Sdf.ValueTypeNames.Float, Sdf.ValueTypeNames.Double3, etc.",
        ))
    return issues


# ---------------------------------------------------------------------------
# Aggregate validator
# ---------------------------------------------------------------------------

_ALL_VALIDATORS = [
    _check_omnigraph_type_mismatch,
    _check_omnigraph_legacy_namespace,
    _check_omnigraph_diff_exec_out,
    _check_omnigraph_use_path,
    _check_omnigraph_bad_api,
    _check_omnigraph_backing_type,
    _check_carter_joint_names,
    _check_triangle_mesh_on_wheels,
    _check_missing_import_omni_usd,
    _check_raw_list_for_vec,
    _check_unsafe_add_xform_op,
    _check_create_prim_default_path,
    _check_delete_then_create_same_path,
    _check_kit_command_name,
    _check_pipeline_stage_enum_mismatch,
    _check_clear_xform_op_order,
    _check_new_stage_mid_patch,
    _check_wrong_franka_url,
    _check_deprecated_core_utils,
    _check_isa_on_api_schema,
    _check_create_attribute_signature,
]


def validate_patch(code: str) -> List[PatchIssue]:
    """
    Run all validators against a generated code patch.
    Returns a list of PatchIssue objects (empty = clean).
    """
    issues: List[PatchIssue] = []
    for validator in _ALL_VALIDATORS:
        try:
            issues.extend(validator(code))
        except Exception as e:
            logger.warning(f"Validator {validator.__name__} crashed: {e}")

    # API allowlist validation — catches hallucinated imports like
    # `from omni.isaac.urdf import _urdf` (4.x deprecated) or
    # `from isaacsim.app import SimulationApp` (fabricated module name).
    try:
        from .api_validator import validate_code as _api_validate
        _ok, _api_issues = _api_validate(code)
        for i in _api_issues:
            severity = "error" if i["severity"] in ("deprecated", "syntax") else "warning"
            issues.append(PatchIssue(
                severity=severity,
                rule=f"api_{i['severity']}",
                message=i["message"],
                fix_hint=i["fix_hint"],
            ))
    except Exception as e:
        logger.warning(f"api_validator crashed: {e}")

    return issues


def format_issues_for_llm(issues: List[PatchIssue]) -> str:
    """
    Format validation issues as a compact error message to feed back
    to the LLM so it can self-correct.
    """
    if not issues:
        return ""

    lines = ["PRE-FLIGHT VALIDATION FAILED — do NOT retry the same pattern:"]
    for i, issue in enumerate(issues, 1):
        lines.append(f"  {i}. [{issue.severity.upper()}] {issue.rule}: {issue.message}")
        if issue.fix_hint:
            lines.append(f"     FIX: {issue.fix_hint}")
    return "\n".join(lines)


def has_blocking_issues(issues: List[PatchIssue]) -> bool:
    """Return True if any issue has severity='error'."""
    return any(i.severity == "error" for i in issues)
