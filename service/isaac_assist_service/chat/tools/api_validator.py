"""
api_validator.py
-----------------
Pre-flight AST validation of generated Python code before it's sent to Kit RPC.

Catches the most common hallucination pattern caught by Phase 12 personas:
Assist emits `from omni.isaac.urdf import _urdf` or `from isaacsim.app import
SimulationApp` — modules that don't exist on Isaac Sim 5.x. The generated code
then fails at runtime with `No module named`, wasting a tool-exec round.

This validator:
  1. Parses the code with `ast`.
  2. Extracts all `import X` and `from Y import Z` statements.
  3. Checks each against an allowlist of modules known to exist on Isaac Sim 5.1.
  4. Returns `(ok, issues)` — if `ok=False`, the caller should reject the code
     and re-prompt the LLM with a hint about the specific bad import.

The allowlist is seeded from a live introspection of Isaac Sim 5.1 (see
`/tmp/module_allowlist.json`) and can be extended by writing to
`workspace/knowledge/module_allowlist.txt`.
"""
from __future__ import annotations
import ast
import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ── Allowlist of real Isaac Sim 5.1 modules ─────────────────────────────────
# Seeded from live introspection. Extend via workspace/knowledge/module_allowlist.txt.
_CORE_ALLOWLIST = {
    # Standard Python / Python tools
    "os", "sys", "json", "re", "math", "time", "asyncio", "pathlib", "typing",
    "dataclasses", "functools", "itertools", "collections", "enum", "io",
    "numpy", "scipy", "torch", "cv2", "yaml", "pickle",
    # USD stack
    "pxr", "pxr.Usd", "pxr.UsdGeom", "pxr.UsdPhysics", "pxr.Sdf", "pxr.Gf",
    "pxr.UsdShade", "pxr.UsdLux", "pxr.Tf", "pxr.Vt", "pxr.Kind",
    "pxr.UsdSkel", "pxr.UsdRender", "pxr.UsdUtils", "pxr.UsdAnimation",
    # PhysX
    "pxr.PhysxSchema",
    # Kit / Omniverse (core — NOT the deprecated omni.isaac.*)
    "omni", "omni.usd", "omni.kit", "omni.kit.commands", "omni.kit.app",
    "omni.kit.viewport", "omni.kit.viewport.utility", "omni.kit.notification_manager",
    "omni.ui", "omni.ext", "omni.client", "omni.timeline", "omni.physx",
    "omni.physx.scripts", "omni.syntheticdata", "omni.replicator",
    "omni.replicator.core", "omni.log",
    # Isaac Sim 5.x namespace (isaacsim.*) — the CORRECT one
    "isaacsim", "isaacsim.core", "isaacsim.core.api", "isaacsim.core.utils",
    "isaacsim.core.utils.prims", "isaacsim.core.utils.stage",
    "isaacsim.core.utils.types", "isaacsim.core.utils.viewports",
    "isaacsim.core.utils.numpy", "isaacsim.core.utils.torch",
    "isaacsim.core.prims", "isaacsim.core.cloner",
    "isaacsim.simulation_app",  # correct path — NOT isaacsim.app
    "isaacsim.asset.importer.urdf", "isaacsim.asset.importer.mjcf",
    "isaacsim.sensors.rtx", "isaacsim.sensors.physics", "isaacsim.sensors.physx",
    "isaacsim.robot.manipulators", "isaacsim.robot.wheeled_robots",
    "isaacsim.robot.policy.examples", "isaacsim.robot_motion.motion_generation",
    "isaacsim.replicator.agent", "isaacsim.replicator.behavior",
    "isaacsim.cortex.framework", "isaacsim.ros2.bridge",
    "isaacsim.util.debug_draw", "isaacsim.gui.components",
    "isaacsim.examples.interactive",
    # IsaacLab
    "isaaclab", "isaaclab.envs", "isaaclab.envs.mdp", "isaaclab.scene",
    "isaaclab.managers", "isaaclab.utils", "isaaclab.utils.math",
    "isaaclab.assets", "isaaclab.sim", "isaaclab.sensors", "isaaclab.actuators",
    "isaaclab.terrains", "isaaclab.controllers",
    # ROS2
    "rclpy", "geometry_msgs", "sensor_msgs", "nav_msgs", "std_msgs",
    "tf2_ros", "tf_transformations",
    # Known-deprecated (flag as errors) — handled in _BLOCKED_MODULES below
}

# Modules that WERE in Isaac Sim 4.x but are removed/renamed in 5.x.
# If the LLM emits these, flag loudly with a hint.
_BLOCKED_MODULES = {
    "omni.isaac.urdf": "isaacsim.asset.importer.urdf",
    "omni.isaac.mjcf": "isaacsim.asset.importer.mjcf",
    "omni.isaac.sensor": "isaacsim.sensors.physics (for IMU/Contact) or isaacsim.sensors.rtx (for LidarRtx)",
    "omni.isaac.debug_draw": "isaacsim.util.debug_draw",
    "omni.isaac.core": "isaacsim.core.api or isaacsim.core.utils",
    "omni.isaac.kit": "isaacsim.simulation_app",
    "omni.isaac.dynamic_control": "isaacsim.core.prims.Articulation",
    "omni.isaac.franka": "isaacsim.robot.manipulators.examples.franka",
    "omni.isaac.wheeled_robots": "isaacsim.robot.wheeled_robots",
    "omni.isaac.motion_generation": "isaacsim.robot_motion.motion_generation",
    "omni.isaac.cloner": "isaacsim.core.cloner",
    "omni.isaac.manipulators": "isaacsim.robot.manipulators",
    "omni.isaac.range_sensor": "isaacsim.sensors.physx",
    "omni.isaac.synthetic_utils": "omni.replicator.core",
    # Classic hallucinated names
    "isaacsim.app": "isaacsim.simulation_app",
}


def _load_extra_allowlist() -> set:
    """User-extensible allowlist from workspace/knowledge/module_allowlist.txt."""
    p = Path(__file__).resolve().parents[3] / "workspace" / "knowledge" / "module_allowlist.txt"
    if not p.exists():
        return set()
    return {line.strip() for line in p.read_text().splitlines() if line.strip() and not line.startswith("#")}


def _module_allowed(module: str, allowlist: set) -> bool:
    """True if `module` or any prefix of it is in the allowlist."""
    if not module:
        return True
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in allowlist:
            return True
    return False


def validate_code(code: str) -> Tuple[bool, List[dict]]:
    """
    Parse `code` and validate all imports against the allowlist.

    Returns `(ok, issues)`:
      - ok = True if no blocking issues
      - issues = list of {module, line, severity, fix_hint} for flagged imports

    Syntax errors also return ok=False with severity='syntax'.
    """
    allowlist = _CORE_ALLOWLIST | _load_extra_allowlist()
    issues: List[dict] = []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [{
            "severity": "syntax",
            "line": e.lineno or 0,
            "module": "",
            "message": f"SyntaxError: {e.msg}",
            "fix_hint": "Fix the syntax before calling tools.",
        }]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                if mod in _BLOCKED_MODULES:
                    issues.append({
                        "severity": "deprecated",
                        "line": node.lineno,
                        "module": mod,
                        "message": f"'{mod}' is deprecated in Isaac Sim 5.x",
                        "fix_hint": f"Use `{_BLOCKED_MODULES[mod]}` instead.",
                    })
                elif not _module_allowed(mod, allowlist):
                    issues.append({
                        "severity": "unknown",
                        "line": node.lineno,
                        "module": mod,
                        "message": f"'{mod}' is not in the known-module allowlist",
                        "fix_hint": "Verify the module exists; if it does, add it to workspace/knowledge/module_allowlist.txt.",
                    })
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in _BLOCKED_MODULES:
                issues.append({
                    "severity": "deprecated",
                    "line": node.lineno,
                    "module": mod,
                    "message": f"'from {mod} import ...' uses a deprecated 4.x module",
                    "fix_hint": f"Use `from {_BLOCKED_MODULES[mod]} import ...` instead.",
                })
            elif mod and not _module_allowed(mod, allowlist):
                issues.append({
                    "severity": "unknown",
                    "line": node.lineno,
                    "module": mod,
                    "message": f"'from {mod} import ...' — module not in allowlist",
                    "fix_hint": "Verify the module exists; if it does, add it to workspace/knowledge/module_allowlist.txt.",
                })

    has_blocker = any(i["severity"] in ("syntax", "deprecated") for i in issues)
    return not has_blocker, issues


def format_issues_for_llm(issues: List[dict]) -> str:
    """Format issues as a concise hint the LLM can use to retry."""
    if not issues:
        return ""
    lines = ["Your generated code was rejected by the API validator:"]
    for i in issues:
        lines.append(f"  - line {i['line']}: {i['message']}. {i['fix_hint']}")
    lines.append("Regenerate the code using only modules that exist in Isaac Sim 5.x.")
    return "\n".join(lines)
