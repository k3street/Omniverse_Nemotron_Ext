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


# Detect ArticulationController without execIn connected to tick source
_RE_ART_CTRL = re.compile(r"ArticulationController", re.I)
_RE_ART_CTRL_EXEC_IN = re.compile(
    r"""['"][^'"]*(?:OnPlaybackTick|OnTick)[^'"]*\.outputs?:tick['"]"""
    r"""[^'"]*['"][^'"]*Art(?:iculation)?Ctrl(?:oller)?[^'"]*\.inputs?:execIn['"]""",
    re.I | re.DOTALL,
)


def _check_art_ctrl_exec_in(code: str) -> List[PatchIssue]:
    """ArticulationController.inputs:execIn must be connected to the tick source."""
    issues = []
    if _RE_ART_CTRL.search(code) and "og.Controller.edit" in code:
        if not _RE_ART_CTRL_EXEC_IN.search(code):
            issues.append(PatchIssue(
                severity="warning",
                rule="og_art_ctrl_no_exec_in",
                message="ArticulationController.inputs:execIn does not appear to be "
                        "connected to OnPlaybackTick.outputs:tick. Without this "
                        "connection the robot will NOT move.",
                fix_hint="Add connection: ('OnPlaybackTick.outputs:tick', "
                         "'ArtController.inputs:execIn') to the CONNECT list.",
            ))
    return issues


# ---------------------------------------------------------------------------
# ROS2 publisher completeness
# ---------------------------------------------------------------------------

_RE_ROS2_SUBSCRIBE = re.compile(r"ROS2Subscribe\w+", re.I)
_RE_ROS2_PUBLISH = re.compile(r"ROS2Publish\w+", re.I)


def _check_missing_ros2_publishers(code: str) -> List[PatchIssue]:
    """Graphs with ROS2 subscribers but no publishers are almost always incomplete."""
    issues = []
    if "og.Controller.edit" not in code:
        return issues
    has_sub = _RE_ROS2_SUBSCRIBE.search(code)
    has_pub = _RE_ROS2_PUBLISH.search(code)
    if has_sub and not has_pub:
        issues.append(PatchIssue(
            severity="warning",
            rule="og_subscriber_only_graph",
            message="Graph creates ROS2 subscriber nodes but no publisher nodes. "
                    "Typically you also need ROS2PublishOdometry, ROS2PublishClock, "
                    "or other publishers so the robot reports its state.",
            fix_hint="Add ROS2PublishOdometry + IsaacComputeOdometry + "
                     "ROS2PublishClock + IsaacReadSimulationTime nodes and "
                     "wire them to OnPlaybackTick and ROS2Context.",
        ))
    return issues


# ---------------------------------------------------------------------------
# Camera render-product validator
# ---------------------------------------------------------------------------

_RE_RENDER_PRODUCT_SET = re.compile(
    r"""renderProductPath['"],\s*['"]([^'"]+)['"]""",
)
_RE_CREATE_RENDER_PRODUCT = re.compile(
    r"IsaacCreateRenderProduct", re.I,
)


def _check_camera_raw_prim_as_render_product(code: str) -> List[PatchIssue]:
    """ROS2CameraHelper.inputs:renderProductPath must come from IsaacCreateRenderProduct,
    not a raw camera prim path."""
    issues = []
    if "og.Controller.edit" not in code:
        return issues
    matches = _RE_RENDER_PRODUCT_SET.findall(code)
    if not matches:
        return issues
    # If any renderProductPath is set to a literal prim path (starts with /)
    # AND there's no IsaacCreateRenderProduct node, that's an error.
    has_create_rp = _RE_CREATE_RENDER_PRODUCT.search(code)
    for path_val in matches:
        if path_val.startswith("/"):
            if not has_create_rp:
                issues.append(PatchIssue(
                    severity="error",
                    rule="og_camera_raw_prim_as_render_product",
                    message=f"renderProductPath is set to a raw prim path '{path_val}' "
                            "instead of being wired from IsaacCreateRenderProduct. "
                            "This will fail with 'Invalid renderProduct'.",
                    fix_hint="Create an IsaacCreateRenderProduct node, set its "
                             "inputs:cameraPrim to the camera prim (as [Sdf.Path(...)]), "
                             "and CONNECT its outputs:renderProductPath to the camera "
                             "helper's inputs:renderProductPath instead of SET_VALUES.",
                ))
                break  # One error is enough
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
    _check_create_attribute_signature,
    _check_art_ctrl_exec_in,
    _check_missing_ros2_publishers,
    _check_camera_raw_prim_as_render_product,
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
