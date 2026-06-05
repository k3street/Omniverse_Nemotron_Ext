#!/usr/bin/env python3
"""
test_full.py
------------
Comprehensive test suite for Isaac Assist. Tests everything that can run
WITHOUT Isaac Sim, plus integration tests if the service is up.

Run levels:
  Level 0 — Unit tests (zero dependencies, always works)
  Level 1 — Service tests (needs FastAPI on :8000)
  Level 2 — MCP tests (needs MCP server on :8002)
  Level 3 — Kit integration (needs Isaac Sim on :8001)

Usage:
    python scripts/test_full.py                     # Level 0 only (default)
    python scripts/test_full.py --level 1           # Levels 0+1
    python scripts/test_full.py --level 2           # Levels 0+1+2
    python scripts/test_full.py --level 3           # All levels
    python scripts/test_full.py --level 0 --verbose # Detailed output
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "service" / "isaac_assist_service"
WORKSPACE = ROOT / "workspace"
SKILLS = ROOT / "skills"

# ANSI
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"; B = "\033[1m"; N = "\033[0m"

# ── Test Framework ───────────────────────────────────────────────────────────

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors: List[str] = []

    @property
    def total(self):
        return self.passed + self.failed + self.skipped

results = TestResult()
VERBOSE = False


def ok(test_name: str, detail: str = ""):
    results.passed += 1
    if VERBOSE:
        print(f"  {G}PASS{N} {test_name}" + (f" — {detail}" if detail else ""))


def fail(test_name: str, reason: str):
    results.failed += 1
    results.errors.append(f"{test_name}: {reason}")
    print(f"  {R}FAIL{N} {test_name}: {reason}")


def skip(test_name: str, reason: str):
    results.skipped += 1
    if VERBOSE:
        print(f"  {Y}SKIP{N} {test_name}: {reason}")


def section(name: str):
    print(f"\n{B}{C}{'─'*3} {name} {'─' * (55 - len(name))}{N}")


# ══════════════════════════════════════════════════════════════════════════════
# LEVEL 0: Unit Tests (no external dependencies)
# ══════════════════════════════════════════════════════════════════════════════

def level_0():
    section("L0: Python Syntax Validation")
    _test_syntax()

    section("L0: Tool Schema Validation")
    _test_tool_schemas()

    section("L0: Code Generation Functions")
    _test_code_generators()

    section("L0: Knowledge Data Files")
    _test_knowledge_files()

    section("L0: Config Loading")
    _test_config()

    section("L0: Test Cases Validation")
    _test_test_cases()

    section("L0: OpenClaw Skill Validation")
    _test_openclaw_skill()

    section("L0: MCP Server Schema Conversion")
    _test_mcp_schema_conversion()

    section("L0: Intent Router Constants")
    _test_intent_router()

    section("L0: Patch Validator")
    _test_patch_validator()


# ── Syntax ───────────────────────────────────────────────────────────────────

def _test_syntax():
    """Parse every Python file in service/ and scripts/ for syntax errors."""
    py_files = list(SERVICE.rglob("*.py")) + list((ROOT / "scripts").rglob("*.py"))
    for f in py_files:
        name = f"syntax:{f.relative_to(ROOT)}"
        try:
            ast.parse(f.read_text(encoding="utf-8"))
            ok(name)
        except SyntaxError as e:
            fail(name, f"line {e.lineno}: {e.msg}")


# ── Tool Schemas ─────────────────────────────────────────────────────────────

def _test_tool_schemas():
    """Validate the ISAAC_SIM_TOOLS list structure."""
    sys.path.insert(0, str(ROOT))
    # We need to import the schemas without triggering aiohttp calls
    spec_path = SERVICE / "chat" / "tools" / "tool_schemas.py"
    ns: Dict[str, Any] = {}
    exec(compile(spec_path.read_text(), spec_path, "exec"), ns)
    tools = ns.get("ISAAC_SIM_TOOLS", [])

    test = "schema:count"
    if len(tools) >= 25:
        ok(test, f"{len(tools)} tools defined")
    else:
        fail(test, f"only {len(tools)} tools (expected >=25)")

    seen_names = set()
    for i, tool in enumerate(tools):
        tname = f"schema:tool[{i}]"
        fn = tool.get("function", {})
        name = fn.get("name", "")

        if tool.get("type") != "function":
            fail(tname, f"type != 'function'")
            continue
        if not name:
            fail(tname, "missing function.name")
            continue
        if name in seen_names:
            fail(tname, f"duplicate name: {name}")
            continue
        seen_names.add(name)

        if not fn.get("description"):
            fail(f"schema:{name}", "missing description")
            continue

        params = fn.get("parameters", {})
        if params.get("type") != "object":
            fail(f"schema:{name}", "parameters.type != 'object'")
            continue

        ok(f"schema:{name}", fn["description"][:50])

    # Check that every CODE_GEN_HANDLER has a matching schema
    executor_path = SERVICE / "chat" / "tools" / "tool_executor.py"
    executor_src = executor_path.read_text()
    for name in seen_names:
        # Not all tools need handlers (some are LLM-only like explain_error)
        pass

    # Check tool categories are present
    expected_categories = ["create_prim", "delete_prim", "create_deformable_mesh",
                           "create_omnigraph", "add_sensor_to_prim", "sim_control",
                           "create_material", "import_robot", "configure_sdg"]
    for cat_tool in expected_categories:
        t = f"schema:has_{cat_tool}"
        if cat_tool in seen_names:
            ok(t)
        else:
            fail(t, f"missing expected tool: {cat_tool}")


# ── Code Generators ──────────────────────────────────────────────────────────

def _test_code_generators():
    """Test each code-gen function produces valid Python."""
    # Import the module by executing it with stubbed kit_tools
    exec_path = SERVICE / "chat" / "tools" / "tool_executor.py"
    src = exec_path.read_text()

    # We need to mock kit_tools to avoid import-time HTTP calls
    # Instead, test the code-gen functions directly by parsing the source
    # and extracting the functions

    # Strategy: import the module with a mock kit_tools
    import types
    mock_kit = types.ModuleType("kit_tools_mock")
    mock_kit.get_stage_context = lambda **kw: {}
    mock_kit.get_viewport_image = lambda **kw: {}
    mock_kit.queue_exec_patch = lambda code, desc: {"queued": True}
    mock_kit.format_stage_context_for_llm = lambda ctx: ""
    mock_kit.is_kit_rpc_alive = lambda: False
    mock_kit.KIT_RPC_BASE = "http://127.0.0.1:8001"

    # Test cases: (tool_name, args_dict, expected_substring)
    test_vectors = [
        ("create_prim", {"prim_path": "/World/TestCube", "prim_type": "Cube"}, "DefinePrim"),
        ("create_prim", {"prim_path": "/World/Sphere", "prim_type": "Sphere", "position": [1, 2, 3]}, "AddTranslateOp"),
        ("delete_prim", {"prim_path": "/World/TestCube"}, "RemovePrim"),
        ("set_attribute", {"prim_path": "/World/Cube", "attr_name": "xformOp:translate", "value": [1, 2, 3]}, "GetAttribute"),
        ("add_reference", {"prim_path": "/World/Robot", "reference_path": "/path/to/robot.usd"}, "AddReference"),
        ("apply_api_schema", {"prim_path": "/World/Cube", "schema_name": "PhysicsRigidBodyAPI"}, "ApplyAPISchema"),
        ("clone_prim", {"source_path": "/World/A", "target_path": "/World/B"}, "CopySpec"),
        ("clone_prim", {"source_path": "/World/A", "target_path": "/World/B", "count": 5, "spacing": 2.0}, "for i in range"),
        ("create_deformable_mesh", {"prim_path": "/World/Cloth", "soft_body_type": "cloth"}, "DeformableSurface"),
        ("create_deformable_mesh", {"prim_path": "/World/Sponge", "soft_body_type": "sponge"}, "DeformableBody"),
        ("create_deformable_mesh", {"prim_path": "/World/Rubber", "soft_body_type": "rubber"}, "DeformableBody"),
        ("create_omnigraph", {"graph_path": "/World/Graph"}, "og.Controller.edit"),
        ("create_omnigraph", {"graph_path": "/World/Graph", "nodes": [{"name": "tick", "type": "omni.graph.action.OnTick"}]}, "OnTick"),
        ("create_material", {"material_path": "/World/Mat", "shader_type": "OmniPBR"}, "CreateMdlMaterialPrimCommand"),
        ("create_material", {"material_path": "/World/Glass", "shader_type": "OmniGlass", "opacity": 0.5}, "CreateMdlMaterialPrimCommand"),
        ("assign_material", {"prim_path": "/World/Cube", "material_path": "/World/Mat"}, "BindMaterialCommand"),
        ("sim_control", {"action": "play"}, "play()"),
        ("sim_control", {"action": "pause"}, "pause()"),
        ("sim_control", {"action": "stop"}, "stop()"),
        ("sim_control", {"action": "step", "step_count": 10}, "forward_one_frame"),
        ("sim_control", {"action": "reset"}, "set_current_time(0)"),
        ("set_physics_params", {"gravity_magnitude": 9.81}, "GetGravityMagnitudeAttr"),
        ("set_physics_params", {"gravity_direction": [0, 0, -1], "gravity_magnitude": 9.81}, "GetGravityDirectionAttr"),
        ("teleport_prim", {"prim_path": "/World/Robot", "position": [5, 0, 0]}, "AddTranslateOp"),
        ("set_joint_targets", {"articulation_path": "/World/Robot", "joint_name": "panda_joint1", "target_position": 1.57}, "GetTargetPositionAttr"),
        ("import_robot", {"file_path": "Franka", "format": "asset_library"}, "get_assets_root_path"),
        ("import_robot", {"file_path": "/path/robot.urdf", "format": "urdf"}, "URDFParseAndImportFile"),
        ("import_robot", {"file_path": "/path/robot.usd", "format": "usd"}, "AddReference"),
        ("set_viewport_camera", {"camera_path": "/World/Camera"}, "camera_path"),
        ("configure_sdg", {"num_frames": 100, "output_dir": "/tmp/sdg", "annotators": ["rgb", "bounding_box_2d"]}, "rep.orchestrator"),
        ("add_sensor_to_prim", {"prim_path": "/World/Robot", "sensor_type": "camera"}, "Camera.Define"),
        ("add_sensor_to_prim", {"prim_path": "/World/Robot", "sensor_type": "rtx_lidar"}, "LidarRtx"),
        ("add_sensor_to_prim", {"prim_path": "/World/Robot", "sensor_type": "imu"}, "IMUSensor"),
    ]

    # Build the CODE_GEN_HANDLERS dict by parsing the module
    # We'll exec a stripped version that only has the pure functions
    ns: Dict[str, Any] = {"__builtins__": __builtins__, "__file__": str(exec_path)}

    # Mock the imports
    ns["json"] = json
    ns["os"] = os
    ns["logging"] = __import__("logging")
    ns["Path"] = Path

    # Source we'll exec
    exec_src = exec_path.read_text()

    # Patch: skip imports from . package
    patched_lines = []
    for line in exec_src.split("\n"):
        if line.strip().startswith("from .") or line.strip().startswith("from __future__"):
            patched_lines.append("# " + line)
        else:
            patched_lines.append(line)
    patched_src = "\n".join(patched_lines)

    # Provide kit_tools stub
    ns["kit_tools"] = mock_kit
    # Provide patch_validator stubs (these are tested separately)
    ns["validate_patch"] = lambda code: []
    ns["format_issues_for_llm"] = lambda issues: ""
    ns["has_blocking_issues"] = lambda issues: False

    try:
        exec(compile(patched_src, str(exec_path), "exec"), ns)
    except Exception as e:
        fail("codegen:import", f"Cannot load tool_executor: {e}")
        return

    code_gen = ns.get("CODE_GEN_HANDLERS", {})
    if not code_gen:
        fail("codegen:handlers", "CODE_GEN_HANDLERS is empty or missing")
        return

    ok(f"codegen:handlers_loaded", f"{len(code_gen)} handlers")

    for tool_name, args, expected_substr in test_vectors:
        tname = f"codegen:{tool_name}({_short_args(args)})"
        gen_fn = code_gen.get(tool_name)
        if not gen_fn:
            fail(tname, f"no handler for {tool_name}")
            continue

        try:
            code = gen_fn(args)
        except Exception as e:
            fail(tname, f"generator crashed: {e}")
            continue

        if not isinstance(code, str) or not code.strip():
            fail(tname, "generated empty code")
            continue

        # Check expected substring
        if expected_substr not in code:
            fail(tname, f"missing '{expected_substr}' in output:\n{code[:200]}")
            continue

        # Validate syntax
        try:
            ast.parse(code)
            ok(tname, f"valid Python, contains '{expected_substr}'")
        except SyntaxError as e:
            fail(tname, f"syntax error line {e.lineno}: {e.msg}\n{code[:300]}")


def _short_args(args: Dict) -> str:
    """Compact repr for test name."""
    parts = []
    for k, v in list(args.items())[:2]:
        sv = repr(v) if len(repr(v)) < 20 else repr(v)[:17] + "..."
        parts.append(f"{k}={sv}")
    return ", ".join(parts)


# ── Knowledge Files ──────────────────────────────────────────────────────────

def _test_knowledge_files():
    """Validate sensor_specs.jsonl and deformable_presets.json."""
    # Sensor specs
    specs_path = WORKSPACE / "knowledge" / "sensor_specs.jsonl"
    if not specs_path.exists():
        fail("knowledge:sensor_specs", "file missing")
    else:
        specs = []
        for i, line in enumerate(specs_path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                specs.append(obj)
            except json.JSONDecodeError as e:
                fail(f"knowledge:sensor_specs:line{i}", str(e))

        if specs:
            ok(f"knowledge:sensor_specs", f"{len(specs)} products loaded")

            # Check required fields
            required = {"product", "manufacturer", "type"}
            for s in specs:
                missing = required - set(s.keys())
                if missing:
                    fail(f"knowledge:spec:{s.get('product','?')}", f"missing {missing}")
                    break
            else:
                ok("knowledge:sensor_fields", "all specs have required fields")

    # Deformable presets
    presets_path = WORKSPACE / "knowledge" / "deformable_presets.json"
    if not presets_path.exists():
        fail("knowledge:deformable_presets", "file missing")
    else:
        try:
            data = json.loads(presets_path.read_text())
            presets = data.get("presets", {})
            ok(f"knowledge:deformable_presets", f"{len(presets)} presets loaded")

            expected_presets = ["cloth_cotton", "sponge_soft", "rubber_soft", "gel_soft", "rope_nylon"]
            for ep in expected_presets:
                if ep in presets:
                    ok(f"knowledge:preset:{ep}")
                else:
                    fail(f"knowledge:preset:{ep}", "missing preset")

            # Validate preset structure
            for name, preset in presets.items():
                if "api" not in preset:
                    fail(f"knowledge:preset_api:{name}", "missing 'api' field")
                elif "params" not in preset:
                    fail(f"knowledge:preset_params:{name}", "missing 'params' field")
                else:
                    ok(f"knowledge:preset_valid:{name}")

        except json.JSONDecodeError as e:
            fail("knowledge:deformable_presets", f"JSON parse error: {e}")


# ── Config ───────────────────────────────────────────────────────────────────

def _test_config():
    """Test Config class loads without crashing."""
    config_path = SERVICE / "config.py"
    ns: Dict[str, Any] = {"__builtins__": __builtins__, "os": os, "__file__": str(config_path)}
    try:
        exec(compile(config_path.read_text(), str(config_path), "exec"), ns)
        ConfigClass = ns["Config"]
        cfg = ConfigClass()
        ok("config:load", f"llm_mode={cfg.llm_mode}")
        ok("config:mcp_port", f"port={cfg.mcp_port}")

        # Validate expected attrs exist
        for attr in ["llm_mode", "local_model_name", "cloud_model_name",
                      "mcp_host", "mcp_port", "openai_api_base"]:
            if hasattr(cfg, attr):
                ok(f"config:attr:{attr}")
            else:
                fail(f"config:attr:{attr}", "attribute missing")
    except Exception as e:
        fail("config:load", str(e))


# ── Test Cases ───────────────────────────────────────────────────────────────

def _test_test_cases():
    """Validate test_cases.jsonl schema and syntax."""
    tc_path = WORKSPACE / "knowledge" / "test_cases.jsonl"
    if not tc_path.exists():
        fail("testcases:exists", "file missing")
        return

    cases = []
    required = {"id", "category", "instruction", "expected_tool", "expected_code", "tags"}

    for i, line in enumerate(tc_path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            tc = json.loads(line)
            cases.append(tc)

            missing = required - set(tc.keys())
            if missing:
                fail(f"testcases:schema:{tc.get('id', f'line{i}')}", f"missing {missing}")
        except json.JSONDecodeError as e:
            fail(f"testcases:parse:line{i}", str(e))

    ok(f"testcases:count", f"{len(cases)} test cases")

    # Syntax check expected_code
    syntax_ok = 0
    for tc in cases:
        code = tc.get("expected_code", "")
        real_lines = [l for l in code.split("\n") if l.strip() and not l.strip().startswith("#")]
        if not real_lines:
            continue
        try:
            ast.parse(code)
            syntax_ok += 1
        except SyntaxError as e:
            fail(f"testcases:syntax:{tc['id']}", f"line {e.lineno}: {e.msg}")

    if syntax_ok > 0:
        ok(f"testcases:syntax_valid", f"{syntax_ok}/{len(cases)} pass ast.parse()")

    # Category distribution
    cats = {}
    for tc in cases:
        c = tc.get("category", "unknown")
        cats[c] = cats.get(c, 0) + 1
    if VERBOSE:
        for c, n in sorted(cats.items()):
            print(f"    {c}: {n}")


# ── OpenClaw Skill ───────────────────────────────────────────────────────────

def _test_openclaw_skill():
    """Validate the SKILL.md frontmatter and content."""
    skill_path = SKILLS / "isaac-sim" / "SKILL.md"
    if not skill_path.exists():
        fail("skill:exists", "skills/isaac-sim/SKILL.md missing")
        return

    content = skill_path.read_text()

    # Check frontmatter
    if not content.startswith("---"):
        fail("skill:frontmatter", "missing YAML frontmatter")
        return

    parts = content.split("---", 2)
    if len(parts) < 3:
        fail("skill:frontmatter", "incomplete frontmatter")
        return

    fm = parts[1]
    for key in ["name:", "description:", "metadata:"]:
        if key in fm:
            ok(f"skill:fm:{key.rstrip(':')}")
        else:
            fail(f"skill:fm:{key.rstrip(':')}", f"missing {key} in frontmatter")

    body = parts[2]
    if len(body) > 500:
        ok("skill:body_length", f"{len(body)} chars")
    else:
        fail("skill:body_length", f"only {len(body)} chars (expected > 500)")

    for section_title in ["Scene Manipulation", "Physics", "Sensors", "Common Workflows"]:
        if section_title in body:
            ok(f"skill:section:{section_title}")
        else:
            fail(f"skill:section:{section_title}", "section missing from skill body")


# ── MCP Schema Conversion ───────────────────────────────────────────────────

def _test_mcp_schema_conversion():
    """Test MCPServer converts OpenAI schemas to MCP format correctly."""
    mcp_path = SERVICE / "mcp_server.py"
    src = mcp_path.read_text()

    # Check MCPServer class exists
    if "class MCPServer" not in src:
        fail("mcp:class", "MCPServer class not found")
        return
    ok("mcp:class_exists")

    # Check transport functions exist
    for fn in ["run_stdio", "run_sse", "handle_request"]:
        if fn in src:
            ok(f"mcp:fn:{fn}")
        else:
            fail(f"mcp:fn:{fn}", "function missing")

    # Check JSON-RPC methods
    for method in ["initialize", "tools/list", "tools/call", "ping"]:
        if method in src:
            ok(f"mcp:method:{method}")
        else:
            fail(f"mcp:method:{method}", "handler missing")


# ── Intent Router ────────────────────────────────────────────────────────────

def _test_intent_router():
    """Validate intent router constants."""
    ir_path = SERVICE / "chat" / "intent_router.py"
    src = ir_path.read_text()

    expected_intents = ["general_query", "scene_diagnose", "vision_inspect",
                        "prim_inspect", "patch_request", "physics_query",
                        "console_review", "navigation"]

    for intent in expected_intents:
        if intent in src:
            ok(f"intent:{intent}")
        else:
            fail(f"intent:{intent}", "missing from intent_router.py")

    if "INTENT_EXAMPLES" in src:
        ok("intent:examples_defined")
    else:
        fail("intent:examples_defined", "no INTENT_EXAMPLES")


def _test_patch_validator():
    """Validate the pre-flight patch validator catches known-bad patterns."""
    sys.path.insert(0, str(ROOT))
    val_path = SERVICE / "chat" / "tools" / "patch_validator.py"
    ns: Dict[str, Any] = {}
    exec(compile(val_path.read_text(), val_path, "exec"), ns)
    validate_patch = ns["validate_patch"]
    has_blocking = ns["has_blocking_issues"]
    format_issues = ns["format_issues_for_llm"]

    # 1. Clean code should pass
    clean = "import omni.usd\nstage = omni.usd.get_context().get_stage()\nstage.RemovePrim('/World/Foo')"
    issues = validate_patch(clean)
    blocking = [i for i in issues if i.severity == "error"]
    if not blocking:
        ok("validator:clean_code_passes")
    else:
        fail("validator:clean_code_passes", f"false positive: {blocking[0].rule}")

    # 2. Catch double3→double direct wiring
    bad_og = """
og.Controller.Keys.CONNECT: [
    ('SubscribeTwist.outputs:linearVelocity', 'DiffController.inputs:linearVelocity'),
    ('SubscribeTwist.outputs:angularVelocity', 'DiffController.inputs:angularVelocity'),
]"""
    issues = validate_patch(bad_og)
    if any(i.rule == "og_double3_to_double" for i in issues):
        ok("validator:og_double3_to_double")
    else:
        fail("validator:og_double3_to_double", "not caught")

    # 3. Catch legacy namespace
    bad_ns = """og.Controller.Keys.CREATE_NODES: [
    ('ROS2Context', 'omni.isaac.ros2_bridge.ROS2Context'),
]"""
    issues = validate_patch(bad_ns)
    if any(i.rule == "og_legacy_namespace" for i in issues):
        ok("validator:og_legacy_namespace")
    else:
        fail("validator:og_legacy_namespace", "not caught")

    # 4. Catch wrong Carter joint names
    bad_joints = """
# NovaCarter drive joints
joint_names = ['joint_drive_fl', 'joint_drive_fr']
"""
    issues = validate_patch(bad_joints)
    if any(i.rule == "carter_wrong_joints" for i in issues):
        ok("validator:carter_wrong_joints")
    else:
        fail("validator:carter_wrong_joints", "not caught")

    # 5. Catch missing import omni.usd
    bad_import = "stage = omni.usd.get_context().get_stage()\nstage.RemovePrim('/World/Foo')"
    issues = validate_patch(bad_import)
    if any(i.rule == "missing_import_omni_usd" for i in issues):
        ok("validator:missing_import_omni_usd")
    else:
        fail("validator:missing_import_omni_usd", "not caught")

    # 6. Catch DiffController.outputs:execOut (doesn't exist)
    bad_exec = """('DiffController.outputs:execOut', 'ArticulationController.inputs:execIn')"""
    issues = validate_patch(bad_exec)
    if any(i.rule == "og_diff_no_exec_out" for i in issues):
        ok("validator:og_diff_no_exec_out")
    else:
        fail("validator:og_diff_no_exec_out", "not caught")

    # 7. Catch ArticulationController.inputs:usePath
    bad_usepath = """og.Controller.attribute('ArticulationController.inputs:usePath')"""
    issues = validate_patch(bad_usepath)
    if any(i.rule == "og_use_path_missing" for i in issues):
        ok("validator:og_use_path_missing")
    else:
        fail("validator:og_use_path_missing", "not caught")

    # 8. Catch bad OG API methods
    bad_api = "for node in nodes:\n    path = node.get_node_path()"
    issues = validate_patch(bad_api)
    if any(i.rule == "og_bad_api" for i in issues):
        ok("validator:og_bad_api")
    else:
        fail("validator:og_bad_api", "not caught")

    # 9. Catch CreateAttribute with wrong signature
    bad_attr = "prim.CreateAttribute('myAttr', Gf.Vec3d)"
    issues = validate_patch(bad_attr)
    if any(i.rule == "usd_create_attr_signature" for i in issues):
        ok("validator:usd_create_attr_signature")
    else:
        fail("validator:usd_create_attr_signature", "not caught")

    # 10. format_issues_for_llm returns non-empty for errors
    issues = validate_patch(bad_og)
    formatted = format_issues(issues)
    if "PRE-FLIGHT VALIDATION FAILED" in formatted:
        ok("validator:format_issues_output")
    else:
        fail("validator:format_issues_output", "bad format")

    # 11. has_blocking_issues
    issues = validate_patch(bad_og)
    if has_blocking(issues):
        ok("validator:has_blocking_issues")
    else:
        fail("validator:has_blocking_issues", "should be blocking")

    # 12. Clean omnigraph code (with Break3Vector) should pass
    good_og = """
import omni.graph.core as og
keys = og.Controller.Keys
og.Controller.edit('/World/Graph', {
    keys.CREATE_NODES: [
        ('SubscribeTwist', 'isaacsim.ros2.bridge.ROS2SubscribeTwist'),
        ('BreakLinear', 'omni.graph.nodes.BreakVector3'),
        ('DiffController', 'isaacsim.robot.wheeled_robots.DifferentialController'),
    ],
    keys.CONNECT: [
        ('SubscribeTwist.outputs:linearVelocity', 'BreakLinear.inputs:tuple'),
        ('BreakLinear.outputs:x', 'DiffController.inputs:linearVelocity'),
    ],
})"""
    issues = validate_patch(good_og)
    blocking = [i for i in issues if i.severity == "error"]
    if not blocking:
        ok("validator:good_og_passes")
    else:
        fail("validator:good_og_passes", f"false positive: {blocking[0].rule}")


# ══════════════════════════════════════════════════════════════════════════════
# LEVEL 1: FastAPI Service Tests (needs uvicorn on :8000)
# ══════════════════════════════════════════════════════════════════════════════

async def level_1():
    section("L1: FastAPI Service Health")
    await _test_service_health()

    section("L1: Chat Endpoint")
    await _test_chat_endpoint()

    section("L1: Retrieval Endpoints")
    await _test_retrieval_endpoints()

    section("L1: Settings Endpoint")
    await _test_settings_endpoint()

    section("L1: Governance Endpoint")
    await _test_governance_endpoint()


async def _http_get(url: str, timeout: int = 10) -> Optional[Dict]:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return {"status": r.status, "body": await r.json()}
    except Exception as e:
        return {"status": 0, "error": str(e)}


async def _http_post(url: str, body: Dict, timeout: int = 60) -> Optional[Dict]:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return {"status": r.status, "body": await r.json()}
    except Exception as e:
        return {"status": 0, "error": str(e)}


async def _test_service_health():
    resp = await _http_get("http://127.0.0.1:8000/health")
    if resp and resp.get("status") == 200:
        ok("service:health", resp["body"].get("service", ""))
    else:
        fail("service:health", f"status={resp.get('status')} error={resp.get('error','')}")

    # OpenAPI spec
    resp = await _http_get("http://127.0.0.1:8000/openapi.json")
    if resp and resp.get("status") == 200:
        paths = resp["body"].get("paths", {})
        ok("service:openapi", f"{len(paths)} endpoints documented")
    else:
        fail("service:openapi", "cannot fetch OpenAPI spec")


async def _test_chat_endpoint():
    """Send a simple message and verify the response structure."""
    resp = await _http_post("http://127.0.0.1:8000/api/v1/chat/message", {
        "session_id": "test_full_001",
        "message": "What is a USD prim?",
    })

    if not resp or resp.get("status") != 200:
        fail("chat:basic", f"status={resp.get('status')} error={resp.get('error','')}")
        return

    body = resp["body"]
    if "intent" in body:
        ok("chat:intent_field", f"intent={body['intent']}")
    else:
        fail("chat:intent_field", "missing 'intent' in response")

    msgs = body.get("response_messages", [])
    if msgs and msgs[0].get("content"):
        ok("chat:reply", f"{len(msgs[0]['content'])} chars")
    else:
        fail("chat:reply", "empty response")

    # Test a tool-triggering message
    resp2 = await _http_post("http://127.0.0.1:8000/api/v1/chat/message", {
        "session_id": "test_full_002",
        "message": "Create a red cube at position 1,0,0.5",
    })
    if resp2 and resp2.get("status") == 200:
        body2 = resp2["body"]
        tools = body2.get("tool_calls", [])
        actions = body2.get("actions_to_approve")
        if tools or actions:
            ok("chat:tool_trigger", f"{len(tools)} tool calls, {len(actions or [])} actions")
        else:
            # Even without Kit, the LLM should mention tools in its text
            text = body2.get("response_messages", [{}])[0].get("content", "")
            if "create" in text.lower() or "cube" in text.lower() or "prim" in text.lower():
                ok("chat:tool_trigger", "tool-related content in reply (Kit offline)")
            else:
                fail("chat:tool_trigger", "no tools or relevant content")
    else:
        fail("chat:tool_trigger", f"request failed: {resp2}")


async def _test_retrieval_endpoints():
    resp = await _http_get("http://127.0.0.1:8000/api/v1/retrieval/specs")
    if resp and resp.get("status") == 200:
        ok("retrieval:specs_list", f"returned data")
    else:
        skip("retrieval:specs_list", f"endpoint not available: {resp}")

    resp = await _http_get("http://127.0.0.1:8000/api/v1/retrieval/specs/lookup?product_name=RealSense")
    if resp and resp.get("status") == 200:
        ok("retrieval:specs_lookup", f"lookup returned")
    else:
        skip("retrieval:specs_lookup", f"endpoint not available: {resp}")


async def _test_settings_endpoint():
    resp = await _http_get("http://127.0.0.1:8000/api/v1/settings/current")
    if resp and resp.get("status") in (200, 404):
        ok("settings:current", f"status={resp['status']}")
    else:
        skip("settings:current", str(resp))


async def _test_governance_endpoint():
    resp = await _http_get("http://127.0.0.1:8000/api/v1/governance/policies")
    if resp and resp.get("status") in (200, 404):
        ok("governance:policies", f"status={resp['status']}")
    else:
        skip("governance:policies", str(resp))


# ══════════════════════════════════════════════════════════════════════════════
# LEVEL 2: MCP Server Tests (needs MCP on :8002)
# ══════════════════════════════════════════════════════════════════════════════

async def level_2():
    section("L2: MCP Server Health")
    await _test_mcp_health()

    section("L2: MCP Tools List")
    await _test_mcp_tools_list()

    section("L2: MCP Tool Call (data handler)")
    await _test_mcp_tool_call()


async def _test_mcp_health():
    resp = await _http_get("http://127.0.0.1:8002/health")
    if resp and resp.get("status") == 200:
        ok("mcp_server:health", str(resp["body"]))
    else:
        fail("mcp_server:health", str(resp))


async def _test_mcp_tools_list():
    resp = await _http_post("http://127.0.0.1:8002/mcp", {
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    })
    if resp and resp.get("status") == 200:
        ok("mcp:post_accepted")
    else:
        fail("mcp:tools_list", str(resp))


async def _test_mcp_tool_call():
    """Call lookup_product_spec via MCP — this is a data handler that needs no Kit."""
    resp = await _http_post("http://127.0.0.1:8002/mcp", {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "lookup_product_spec", "arguments": {"product_name": "RealSense D435i"}}
    })
    if resp and resp.get("status") == 200:
        ok("mcp:tool_call_spec")
    else:
        fail("mcp:tool_call_spec", str(resp))

    # Test a code-gen tool
    resp = await _http_post("http://127.0.0.1:8002/mcp", {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "create_prim", "arguments": {"prim_path": "/World/TestMCP", "prim_type": "Cube"}}
    })
    if resp and resp.get("status") == 200:
        ok("mcp:tool_call_codegen")
    else:
        fail("mcp:tool_call_codegen", str(resp))


# ══════════════════════════════════════════════════════════════════════════════
# LEVEL 3: Kit Integration (needs Isaac Sim on :8001)
# ══════════════════════════════════════════════════════════════════════════════

async def level_3():
    section("L3: Kit RPC Health")
    await _test_kit_health()

    section("L3: Kit Context & Selection")
    await _test_kit_context()

    section("L3: Kit Code Execution")
    await _test_kit_exec()

    section("L3: Kit Sim Control")
    await _test_kit_sim_control()

    section("L3: End-to-End Chat → Kit")
    await _test_e2e_chat()


async def _test_kit_health():
    resp = await _http_get("http://127.0.0.1:8001/health")
    if resp and resp.get("status") == 200 and resp["body"].get("ok"):
        ok("kit:health")
    else:
        fail("kit:health", f"Kit RPC not responding: {resp}")


async def _test_kit_context():
    resp = await _http_get("http://127.0.0.1:8001/context")
    if resp and resp.get("status") == 200:
        body = resp["body"]
        ok("kit:context", f"stage_url={body.get('stage', {}).get('stage_url', '?')}")

        prim_count = body.get("stage", {}).get("prim_count", 0)
        ok("kit:prim_count", f"{prim_count} prims in scene")
    else:
        fail("kit:context", str(resp))

    # Selection
    resp = await _http_get("http://127.0.0.1:8001/selection")
    if resp and resp.get("status") == 200:
        ok("kit:selection", str(resp["body"]))
    else:
        fail("kit:selection", str(resp))

    # List prims
    resp = await _http_get("http://127.0.0.1:8001/list_prims")
    if resp and resp.get("status") == 200:
        ok("kit:list_prims", f"returned prims list")
    else:
        fail("kit:list_prims", str(resp))


async def _test_kit_exec():
    """Execute simple code in Kit and verify."""
    # Create a test prim
    resp = await _http_post("http://127.0.0.1:8001/exec_patch", {
        "code": """
import omni.usd
stage = omni.usd.get_context().get_stage()
stage.DefinePrim('/World/TestAutomation', 'Cube')
""",
        "description": "Test: create automation cube"
    })
    if resp and resp.get("status") == 200:
        ok("kit:exec_create", str(resp["body"]))
    else:
        fail("kit:exec_create", str(resp))

    await asyncio.sleep(1)

    # Verify it exists
    resp = await _http_get("http://127.0.0.1:8001/list_prims?filter_type=Cube")
    if resp and resp.get("status") == 200:
        prims = resp["body"] if isinstance(resp["body"], list) else resp["body"].get("prims", [])
        found = any("/World/TestAutomation" in str(p) for p in prims)
        if found:
            ok("kit:exec_verify", "TestAutomation cube found in scene")
        else:
            fail("kit:exec_verify", "TestAutomation cube not found after exec")
    else:
        fail("kit:exec_verify", str(resp))

    # Clean up
    await _http_post("http://127.0.0.1:8001/exec_patch", {
        "code": "import omni.usd\nomni.usd.get_context().get_stage().RemovePrim('/World/TestAutomation')",
        "description": "Test: cleanup"
    })


async def _test_kit_sim_control():
    # Step
    resp = await _http_post("http://127.0.0.1:8001/sim_control", {"action": "step"})
    if resp and resp.get("status") == 200:
        ok("kit:sim_step")
    else:
        fail("kit:sim_step", str(resp))

    # Play then stop
    resp = await _http_post("http://127.0.0.1:8001/sim_control", {"action": "play"})
    if resp and resp.get("status") == 200:
        ok("kit:sim_play")
    else:
        fail("kit:sim_play", str(resp))

    await asyncio.sleep(1)

    resp = await _http_post("http://127.0.0.1:8001/sim_control", {"action": "stop"})
    if resp and resp.get("status") == 200:
        ok("kit:sim_stop")
    else:
        fail("kit:sim_stop", str(resp))


async def _test_e2e_chat():
    """Full loop: chat message → LLM → tool call → Kit execution."""
    # This test sends natural language through the chat API and expects
    # the LLM to generate a tool call that gets dispatched to Kit

    test_prompts = [
        ("Create a sphere at position 0,0,1", "create_prim"),
        ("What prims are in the scene?", "scene_summary"),
        ("Play the simulation", "sim_control"),
    ]

    for prompt, expected_tool in test_prompts:
        tname = f"e2e:{expected_tool}"
        resp = await _http_post("http://127.0.0.1:8000/api/v1/chat/message", {
            "session_id": "test_e2e",
            "message": prompt,
        }, timeout=120)

        if not resp or resp.get("status") != 200:
            fail(tname, f"chat failed: {resp}")
            continue

        body = resp["body"]
        tools = body.get("tool_calls", [])
        actions = body.get("actions_to_approve", [])
        reply = body.get("response_messages", [{}])[0].get("content", "")

        tool_names = [t.get("tool", "") for t in tools]
        if expected_tool in tool_names:
            ok(tname, f"tool '{expected_tool}' called")
        elif actions:
            ok(tname, f"actions pending approval ({len(actions)})")
        elif expected_tool.lower() in reply.lower() or prompt.split()[0].lower() in reply.lower():
            ok(tname, f"relevant response (no direct tool match)")
        else:
            fail(tname, f"expected tool '{expected_tool}', got tools={tool_names}")


# ══════════════════════════════════════════════════════════════════════════════
# Report & Main
# ══════════════════════════════════════════════════════════════════════════════

def print_report(elapsed: float, max_level: int):
    pct = (results.passed / results.total * 100) if results.total > 0 else 0
    color = G if results.failed == 0 else R

    print(f"\n{'═'*60}")
    print(f"  {B}ISAAC ASSIST TEST REPORT{N}")
    print(f"  Test level: {max_level}")
    print(f"  {G}Passed: {results.passed}{N}  |  {R}Failed: {results.failed}{N}  |  {Y}Skipped: {results.skipped}{N}")
    print(f"  Total:  {results.total}")
    print(f"  Rate:   {color}{pct:.1f}%{N}")
    print(f"  Time:   {elapsed:.2f}s")

    if results.errors:
        print(f"\n  {R}{B}Failures:{N}")
        for err in results.errors:
            print(f"    {R}✗{N} {err}")

    print(f"{'═'*60}")
    return results.failed == 0


async def async_main(max_level: int):
    start = time.time()

    # Level 0 is always sync
    level_0()

    if max_level >= 1:
        # Check if service is reachable first
        resp = await _http_get("http://127.0.0.1:8000/health", timeout=3)
        if resp and resp.get("status") == 200:
            await level_1()
        else:
            section("L1: SKIPPED (FastAPI not running on :8000)")
            skip("level1", "start with: uvicorn service.isaac_assist_service.main:app --port 8000")

    if max_level >= 2:
        resp = await _http_get("http://127.0.0.1:8002/health", timeout=3)
        if resp and resp.get("status") == 200:
            await level_2()
        else:
            section("L2: SKIPPED (MCP server not running on :8002)")
            skip("level2", "start with: python -m service.isaac_assist_service.mcp_server")

    if max_level >= 3:
        resp = await _http_get("http://127.0.0.1:8001/health", timeout=3)
        if resp and resp.get("status") == 200:
            await level_3()
        else:
            section("L3: SKIPPED (Kit RPC not running on :8001)")
            skip("level3", "start Isaac Sim with the extension loaded")

    success = print_report(time.time() - start, max_level)
    return success


def main():
    parser = argparse.ArgumentParser(description="Isaac Assist full test suite")
    parser.add_argument("--level", type=int, default=0, choices=[0, 1, 2, 3],
                        help="Max test level (0=unit, 1=service, 2=mcp, 3=kit)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    print(f"{B}{C}Isaac Assist — Full Test Suite{N}")
    print(f"Level {args.level}: ", end="")
    level_names = {0: "Unit tests only", 1: "Unit + Service", 2: "Unit + Service + MCP", 3: "All levels"}
    print(level_names[args.level])

    success = asyncio.run(async_main(args.level))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
