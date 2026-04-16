"""
L0 tests for the Collision Mesh Quality Addendum.

Covers:
  - analyze_collision_mesh           (DATA — Kit-backed analyzer)
  - validate_collision_setup         (DATA — Kit-backed walker)
  - suggest_collision_approximation  (DATA — pure-Python recommender)
  - apply_collision_approximation    (CODE_GEN)
  - generate_collision_audit_script  (CODE_GEN)

All tests are L0 — no Kit, no GPU, no PhysX runtime. Kit RPC is mocked.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _ANALYZE_MESH_TEMPLATE,
    _VALIDATE_SETUP_TEMPLATE,
    _VALID_APPROXIMATIONS,
    _gen_apply_collision_approximation,
    _gen_collision_audit_script,
    _handle_analyze_collision_mesh,
    _handle_suggest_collision_approximation,
    _handle_validate_collision_setup,
    _parse_kit_json_payload,
    _recommend_from_metrics,
    _suggest_decomp_params,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_valid_python(code: str, handler_name: str):
    try:
        compile(code, f"<generated:{handler_name}>", "exec")
    except SyntaxError as e:
        pytest.fail(f"{handler_name} generated invalid Python:\n{e}\n\nCode:\n{code}")


def _patch_kit_payload(monkeypatch, payload: dict):
    """Make `kit_tools.queue_exec_patch` return a fixed Kit-style payload."""
    import service.isaac_assist_service.chat.tools.tool_executor as te

    async def _fake(_code: str, _desc: str = ""):
        return payload

    monkeypatch.setattr(te.kit_tools, "queue_exec_patch", _fake)


NEW_TOOLS = [
    "analyze_collision_mesh",
    "validate_collision_setup",
    "suggest_collision_approximation",
    "apply_collision_approximation",
    "generate_collision_audit_script",
]


# ---------------------------------------------------------------------------
# Schema registration sanity
# ---------------------------------------------------------------------------

class TestSchemaRegistration:
    """Each new tool must be both in ISAAC_SIM_TOOLS and dispatch-registered."""

    def _all_tool_names(self):
        return {t["function"]["name"] for t in ISAAC_SIM_TOOLS}

    @pytest.mark.parametrize("name", NEW_TOOLS)
    def test_tool_in_schema(self, name):
        assert name in self._all_tool_names(), f"{name} not declared in ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", NEW_TOOLS)
    def test_tool_has_handler(self, name):
        assert (name in DATA_HANDLERS) or (name in CODE_GEN_HANDLERS), (
            f"{name} declared in schema but has no DATA / CODE_GEN handler"
        )

    def test_no_duplicate_registration(self):
        for name in NEW_TOOLS:
            both = (name in DATA_HANDLERS) and (name in CODE_GEN_HANDLERS)
            assert not both, f"{name} registered as both DATA and CODE_GEN"


# ---------------------------------------------------------------------------
# Pure-function recommendation kernel
# ---------------------------------------------------------------------------

class TestRecommendationKernel:
    def test_empty_mesh_returns_none(self):
        rec = _recommend_from_metrics(0, 0.0, False, False)
        assert rec["approximation"] == "none"

    def test_small_convex_returns_hull(self):
        rec = _recommend_from_metrics(800, 0.85, True, False)
        assert rec["approximation"] == "convexHull"

    def test_high_detail_concave_returns_sdf(self):
        rec = _recommend_from_metrics(75_000, 0.30, False, True)
        assert rec["approximation"] == "sdf"

    def test_concave_under_budget_returns_decomposition(self):
        rec = _recommend_from_metrics(8_000, 0.40, False, False)
        assert rec["approximation"] == "convexDecomposition"
        assert "0.40" in rec["rationale"]

    def test_decomp_params_scale_with_size(self):
        small = _suggest_decomp_params(200)
        large = _suggest_decomp_params(80_000)
        assert (
            small["physxConvexDecompositionCollision:maxConvexHulls"]
            < large["physxConvexDecompositionCollision:maxConvexHulls"]
        )


# ---------------------------------------------------------------------------
# Kit-payload parser
# ---------------------------------------------------------------------------

class TestParseKitPayload:
    def test_direct_payload(self):
        out = _parse_kit_json_payload({"triangle_count": 100, "prim_path": "/X"}, key_field="triangle_count")
        assert out["triangle_count"] == 100

    def test_stdout_field(self):
        payload = {"output": 'noise\n{"triangle_count": 42, "prim_path": "/Y"}\nmore noise\n'}
        out = _parse_kit_json_payload(payload, key_field="triangle_count")
        assert out["triangle_count"] == 42

    def test_no_payload_returns_none(self):
        assert _parse_kit_json_payload({"queued": True}, key_field="triangle_count") is None
        assert _parse_kit_json_payload(None, key_field="triangle_count") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# analyze_collision_mesh
# ---------------------------------------------------------------------------

class TestAnalyzeCollisionMesh:
    @pytest.mark.asyncio
    async def test_kit_payload_yields_recommendation(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {
            "prim_path": "/World/Cup",
            "triangle_count": 12_000,
            "vertex_count": 6_000,
            "bbox_volume_m3": 0.0008,
            "convexity_ratio": 0.42,
            "is_convex": False,
            "over_budget": True,
        })
        result = await _handle_analyze_collision_mesh({"prim_path": "/World/Cup", "max_triangles": 5_000})
        assert result["recommended_approximation"] in _VALID_APPROXIMATIONS
        assert result["over_budget"] is True
        assert "rationale" in result

    @pytest.mark.asyncio
    async def test_over_budget_flagged(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {
            "triangle_count": 10_000,
            "convexity_ratio": 0.5,
            "is_convex": False,
            "over_budget": True,
            "vertex_count": 5000,
            "bbox_volume_m3": 1.0,
        })
        result = await _handle_analyze_collision_mesh({"prim_path": "/World/Mesh"})
        assert result["over_budget"] is True

    @pytest.mark.asyncio
    async def test_kit_unavailable_returns_queued(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {"queued": True, "patch_id": "abc"})
        result = await _handle_analyze_collision_mesh({"prim_path": "/World/Cup"})
        assert result.get("queued") is True
        assert "kit_response" in result

    def test_analyzer_template_compiles(self):
        code = _ANALYZE_MESH_TEMPLATE.format(prim_path="/World/Cup", max_triangles=5000)
        _assert_valid_python(code, "_ANALYZE_MESH_TEMPLATE")
        # Path is repr'd safely
        code_q = _ANALYZE_MESH_TEMPLATE.format(prim_path="/World/Bob's Cup", max_triangles=100)
        _assert_valid_python(code_q, "_ANALYZE_MESH_TEMPLATE (quote)")


# ---------------------------------------------------------------------------
# validate_collision_setup
# ---------------------------------------------------------------------------

class TestValidateCollisionSetup:
    @pytest.mark.asyncio
    async def test_clean_setup(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {
            "prim_path": "/World/Robot",
            "colliders_checked": 5,
            "issues": [],
            "ready_for_simulation": True,
        })
        result = await _handle_validate_collision_setup({"prim_path": "/World/Robot"})
        assert result["ready_for_simulation"] is True
        assert result["colliders_checked"] == 5

    @pytest.mark.asyncio
    async def test_triangle_mesh_on_dynamic_flagged(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {
            "prim_path": "/World/Robot",
            "colliders_checked": 3,
            "issues": [
                {"prim": "/World/Robot/finger_L", "severity": "error",
                 "kind": "triangle_mesh_on_dynamic",
                 "message": "triangleMesh approximation on dynamic body."},
            ],
            "ready_for_simulation": False,
        })
        result = await _handle_validate_collision_setup({"prim_path": "/World/Robot"})
        assert result["ready_for_simulation"] is False
        kinds = {i["kind"] for i in result["issues"]}
        assert "triangle_mesh_on_dynamic" in kinds

    @pytest.mark.asyncio
    async def test_kit_unavailable_returns_queued(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {"queued": True})
        result = await _handle_validate_collision_setup({"prim_path": "/World"})
        assert result.get("queued") is True

    def test_validator_template_compiles(self):
        code = _VALIDATE_SETUP_TEMPLATE.format(prim_path="/World/Robot")
        _assert_valid_python(code, "_VALIDATE_SETUP_TEMPLATE")


# ---------------------------------------------------------------------------
# suggest_collision_approximation
# ---------------------------------------------------------------------------

class TestSuggestCollisionApproximation:
    @pytest.mark.asyncio
    async def test_graspable_intent_forces_decomposition(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {
            "prim_path": "/World/Cup",
            "triangle_count": 4_000,
            "convexity_ratio": 0.35,
            "is_convex": False,
            "over_budget": False,
            "vertex_count": 2000,
            "bbox_volume_m3": 0.0005,
        })
        result = await _handle_suggest_collision_approximation({
            "prim_path": "/World/Cup",
            "intent": "graspable",
        })
        assert result["approximation"] == "convexDecomposition"
        assert "physxConvexDecompositionCollision:maxConvexHulls" in result["params"]

    @pytest.mark.asyncio
    async def test_sensor_only_returns_none(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {
            "triangle_count": 1000, "convexity_ratio": 1.0, "is_convex": True,
            "over_budget": False, "vertex_count": 500, "bbox_volume_m3": 0.1,
        })
        result = await _handle_suggest_collision_approximation({
            "prim_path": "/World/Sensor",
            "intent": "sensor_only",
        })
        assert result["approximation"] == "none"
        assert result["params"] == {}

    @pytest.mark.asyncio
    async def test_static_environment_concave_uses_triangle_mesh(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {
            "triangle_count": 25_000, "convexity_ratio": 0.30, "is_convex": False,
            "over_budget": True, "vertex_count": 12000, "bbox_volume_m3": 4.0,
        })
        result = await _handle_suggest_collision_approximation({
            "prim_path": "/World/Warehouse",
            "intent": "static_environment",
        })
        assert result["approximation"] == "triangleMesh"

    @pytest.mark.asyncio
    async def test_unknown_intent_safe_fallback(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {"queued": True})
        result = await _handle_suggest_collision_approximation({
            "prim_path": "/World/X",
            "intent": "weird_intent",
        })
        assert result["approximation"] == "convexHull"

    @pytest.mark.asyncio
    async def test_dynamic_object_uses_kernel(self, monkeypatch):
        _patch_kit_payload(monkeypatch, {
            "triangle_count": 600, "convexity_ratio": 0.85, "is_convex": True,
            "over_budget": False, "vertex_count": 300, "bbox_volume_m3": 0.001,
        })
        result = await _handle_suggest_collision_approximation({
            "prim_path": "/World/Brick",
            "intent": "dynamic_object",
        })
        assert result["approximation"] == "convexHull"


# ---------------------------------------------------------------------------
# apply_collision_approximation (code-gen)
# ---------------------------------------------------------------------------

class TestApplyCollisionApproximation:
    def test_compiles_basic(self):
        code = _gen_apply_collision_approximation({
            "prim_path": "/World/Cup",
            "approximation": "convexHull",
        })
        _assert_valid_python(code, "apply_collision_approximation")
        assert "/World/Cup" in code
        assert "convexHull" in code

    def test_compiles_decomposition_with_params(self):
        code = _gen_apply_collision_approximation({
            "prim_path": "/World/Cup",
            "approximation": "convexDecomposition",
            "params": {
                "physxConvexDecompositionCollision:maxConvexHulls": 16,
                "physxConvexDecompositionCollision:voxelResolution": 500_000,
            },
        })
        _assert_valid_python(code, "apply_collision_approximation (decomp)")
        assert "PhysxConvexDecompositionCollisionAPI" in code
        assert "maxConvexHulls" in code
        assert "16" in code

    def test_compiles_sdf(self):
        code = _gen_apply_collision_approximation({
            "prim_path": "/World/Sphere",
            "approximation": "sdf",
            "params": {"physxSDFMeshCollision:sdfResolution": 256},
        })
        _assert_valid_python(code, "apply_collision_approximation (sdf)")
        assert "PhysxSDFMeshCollisionAPI" in code
        assert "256" in code

    def test_invalid_approximation_falls_back(self):
        code = _gen_apply_collision_approximation({
            "prim_path": "/World/X",
            "approximation": "magic_collider_42",
        })
        _assert_valid_python(code, "apply_collision_approximation (fallback)")
        # Falls back to safe default
        assert "convexHull" in code
        assert "magic_collider_42" not in code

    def test_path_injection_safe(self):
        code = _gen_apply_collision_approximation({
            "prim_path": "/World/Bob's Cup",
            "approximation": "convexHull",
        })
        _assert_valid_python(code, "apply_collision_approximation (quote)")

    def test_param_keys_filtered(self):
        # Stray non-namespaced keys must not become Python attribute calls
        code = _gen_apply_collision_approximation({
            "prim_path": "/World/Cup",
            "approximation": "convexDecomposition",
            "params": {"random_key": 999, "physxConvexDecompositionCollision:maxConvexHulls": 8},
        })
        _assert_valid_python(code, "apply_collision_approximation (filter)")
        assert "random_key" not in code
        assert "maxConvexHulls" in code


# ---------------------------------------------------------------------------
# generate_collision_audit_script (code-gen)
# ---------------------------------------------------------------------------

class TestGenerateCollisionAuditScript:
    def test_compiles_with_explicit_paths(self):
        code = _gen_collision_audit_script({
            "scope_path": "/World/Robot",
            "output_path": "workspace/audits/run1.json",
        })
        _assert_valid_python(code, "generate_collision_audit_script (explicit)")
        assert "/World/Robot" in code
        assert "workspace/audits/run1.json" in code
        assert "physics:approximation" in code

    def test_compiles_with_defaults(self):
        code = _gen_collision_audit_script({})
        _assert_valid_python(code, "generate_collision_audit_script (defaults)")
        assert "/World" in code
        assert "collision_audits" in code

    def test_writes_json_report(self):
        code = _gen_collision_audit_script({"scope_path": "/W"})
        assert "json.dumps" in code
        assert "out_path.write_text" in code
        assert "[audit] wrote" in code

    def test_path_injection_safe(self):
        code = _gen_collision_audit_script({
            "scope_path": "/World/Bob's Lab",
            "output_path": "workspace/it's-fine.json",
        })
        _assert_valid_python(code, "generate_collision_audit_script (quotes)")
