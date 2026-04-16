"""
L0 tests for the Sim-to-Real Gap Analysis tools.

Tests cover:
  - analyze_sim_to_real_gap (DATA handler)
  - apply_domain_randomization (CODE_GEN handler, 4 types)
  - configure_actuator_model (CODE_GEN handler, 5 types)
  - generate_transfer_report (DATA handler)
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _handle_analyze_sim_to_real_gap,
    _handle_generate_transfer_report,
)


# ---------------------------------------------------------------------------
# Helper: compile check (same pattern as test_code_generators.py)
# ---------------------------------------------------------------------------

def _assert_valid_python(code: str, handler_name: str):
    """Verify the generated code is syntactically valid Python."""
    try:
        compile(code, f"<generated:{handler_name}>", "exec")
    except SyntaxError as e:
        pytest.fail(f"{handler_name} generated invalid Python:\n{e}\n\nCode:\n{code}")


# ===========================================================================
# CODE_GEN: apply_domain_randomization
# ===========================================================================

class TestApplyDomainRandomization:
    """Tests for the apply_domain_randomization code generator."""

    def test_physics_randomization(self):
        code = CODE_GEN_HANDLERS["apply_domain_randomization"]({
            "prim_path": "/World/Franka",
            "randomization_type": "physics",
            "params": {
                "friction_range": [0.4, 1.6],
                "mass_scale_range": [0.7, 1.3],
            },
        })
        _assert_valid_python(code, "apply_domain_randomization:physics")
        assert "PhysxSchema" in code
        assert "random.uniform" in code
        assert "friction" in code
        assert "MassAPI" in code
        assert "DriveAPI" in code

    def test_physics_default_params(self):
        code = CODE_GEN_HANDLERS["apply_domain_randomization"]({
            "prim_path": "/World/Robot",
            "randomization_type": "physics",
        })
        _assert_valid_python(code, "apply_domain_randomization:physics_defaults")
        assert "0.5, 1.5" in code  # default friction range
        assert "0.8, 1.2" in code  # default mass range

    def test_visual_randomization(self):
        code = CODE_GEN_HANDLERS["apply_domain_randomization"]({
            "prim_path": "/World/Table",
            "randomization_type": "visual",
            "params": {
                "roughness_range": [0.1, 0.9],
            },
        })
        _assert_valid_python(code, "apply_domain_randomization:visual")
        assert "UsdShade" in code
        assert "diffuse_color" in code
        assert "roughness" in code
        assert "random.uniform" in code

    def test_lighting_randomization(self):
        code = CODE_GEN_HANDLERS["apply_domain_randomization"]({
            "prim_path": "/World/Light",
            "randomization_type": "lighting",
            "params": {
                "intensity_range": [800, 3000],
                "color_temp_range": [3500, 6500],
            },
        })
        _assert_valid_python(code, "apply_domain_randomization:lighting")
        assert "UsdLux" in code
        assert "IntensityAttr" in code
        assert "ColorTemperature" in code
        assert "800, 3000" in code

    def test_sensor_noise_randomization(self):
        code = CODE_GEN_HANDLERS["apply_domain_randomization"]({
            "prim_path": "/World/Camera",
            "randomization_type": "sensor_noise",
            "params": {
                "noise_type": "gaussian",
                "noise_sigma": 0.02,
                "depth_noise_sigma": 0.01,
            },
        })
        _assert_valid_python(code, "apply_domain_randomization:sensor_noise")
        assert "replicator" in code
        assert "gaussian" in code
        assert "0.02" in code
        assert "UsdGeom" in code

    def test_sensor_noise_defaults(self):
        code = CODE_GEN_HANDLERS["apply_domain_randomization"]({
            "prim_path": "/World/Camera",
            "randomization_type": "sensor_noise",
        })
        _assert_valid_python(code, "apply_domain_randomization:sensor_noise_defaults")
        assert "0.01" in code  # default noise_sigma
        assert "0.005" in code  # default depth_noise_sigma

    def test_unknown_type_returns_comment(self):
        code = CODE_GEN_HANDLERS["apply_domain_randomization"]({
            "prim_path": "/World/Test",
            "randomization_type": "unknown_type",
        })
        assert "Unknown" in code or "unknown_type" in code


# ===========================================================================
# CODE_GEN: configure_actuator_model
# ===========================================================================

class TestConfigureActuatorModel:
    """Tests for the configure_actuator_model code generator."""

    def test_ideal_actuator(self):
        code = CODE_GEN_HANDLERS["configure_actuator_model"]({
            "robot_path": "/World/Franka",
            "actuator_type": "ideal",
        })
        _assert_valid_python(code, "configure_actuator_model:ideal")
        assert "UsdPhysics" in code
        assert "DriveAPI" in code
        assert "10000" in code  # high stiffness
        assert "inf" in code  # infinite torque

    def test_dc_motor_actuator(self):
        code = CODE_GEN_HANDLERS["configure_actuator_model"]({
            "robot_path": "/World/Franka",
            "actuator_type": "dc_motor",
            "params": {
                "stiffness": 500,
                "damping": 50,
                "max_torque": 87,
                "friction": 0.1,
            },
        })
        _assert_valid_python(code, "configure_actuator_model:dc_motor")
        assert "UsdPhysics" in code
        assert "PhysxSchema" in code
        assert "PhysxJointAPI" in code
        assert "JointFrictionAttr" in code
        assert "MaxForceAttr" in code
        assert "500" in code
        assert "50" in code

    def test_dc_motor_defaults(self):
        code = CODE_GEN_HANDLERS["configure_actuator_model"]({
            "robot_path": "/World/UR10",
            "actuator_type": "dc_motor",
        })
        _assert_valid_python(code, "configure_actuator_model:dc_motor_defaults")
        assert "1000" in code  # default stiffness
        assert "100" in code  # default damping

    def test_position_pid_actuator(self):
        code = CODE_GEN_HANDLERS["configure_actuator_model"]({
            "robot_path": "/World/Franka",
            "actuator_type": "position_pid",
            "params": {"stiffness": 750, "damping": 75, "max_torque": 100},
        })
        _assert_valid_python(code, "configure_actuator_model:position_pid")
        assert "UsdPhysics" in code
        assert "force" in code
        assert "750" in code
        assert "75" in code
        assert "MaxForceAttr" in code

    def test_velocity_pid_actuator(self):
        code = CODE_GEN_HANDLERS["configure_actuator_model"]({
            "robot_path": "/World/Franka",
            "actuator_type": "velocity_pid",
            "params": {"damping": 200, "max_torque": 50},
        })
        _assert_valid_python(code, "configure_actuator_model:velocity_pid")
        assert "UsdPhysics" in code
        assert "Stiffness" in code
        assert "0.0" in code  # No position control
        assert "200" in code  # velocity P-gain

    def test_implicit_spring_damper_actuator(self):
        code = CODE_GEN_HANDLERS["configure_actuator_model"]({
            "robot_path": "/World/Franka",
            "actuator_type": "implicit_spring_damper",
            "params": {"stiffness": 800, "damping": 80, "friction": 0.15},
        })
        _assert_valid_python(code, "configure_actuator_model:implicit_spring_damper")
        assert "UsdPhysics" in code
        assert "PhysxSchema" in code
        assert "PhysxJointAPI" in code
        assert "800" in code
        assert "80" in code

    def test_unknown_actuator_type(self):
        code = CODE_GEN_HANDLERS["configure_actuator_model"]({
            "robot_path": "/World/Franka",
            "actuator_type": "nonexistent_type",
        })
        assert "Unknown" in code or "nonexistent_type" in code


# ===========================================================================
# DATA: analyze_sim_to_real_gap
# ===========================================================================

class TestAnalyzeSimToRealGap:
    """Tests for the analyze_sim_to_real_gap data handler."""

    @pytest.mark.asyncio
    async def test_basic_gap_analysis(self):
        result = await _handle_analyze_sim_to_real_gap({
            "robot_path": "/World/Franka",
        })
        assert isinstance(result, dict)
        assert "overall_score" in result
        assert "gaps" in result
        assert "recommendations" in result
        assert isinstance(result["gaps"], list)
        assert len(result["gaps"]) > 0
        assert result["overall_score"] >= 0
        assert result["overall_score"] <= 100

    @pytest.mark.asyncio
    async def test_auto_detect_robot_type(self):
        result = await _handle_analyze_sim_to_real_gap({
            "robot_path": "/World/Franka",
        })
        assert result["real_robot_type"] == "franka"
        assert result["real_spec_available"] is True

    @pytest.mark.asyncio
    async def test_explicit_robot_type(self):
        result = await _handle_analyze_sim_to_real_gap({
            "robot_path": "/World/MyRobot",
            "real_robot_type": "ur10",
        })
        assert result["real_robot_type"] == "ur10"
        assert result["real_spec_available"] is True

    @pytest.mark.asyncio
    async def test_unknown_robot_type(self):
        result = await _handle_analyze_sim_to_real_gap({
            "robot_path": "/World/CustomBot",
        })
        assert result["real_robot_type"] == "unknown"
        assert result["real_spec_available"] is False

    @pytest.mark.asyncio
    async def test_gap_categories_present(self):
        result = await _handle_analyze_sim_to_real_gap({
            "robot_path": "/World/Franka",
        })
        categories = {g["category"] for g in result["gaps"]}
        assert "domain_randomization" in categories
        assert "sensor_noise" in categories
        assert "actuator_model" in categories
        assert "physics_fidelity" in categories
        assert "environment_fidelity" in categories

    @pytest.mark.asyncio
    async def test_gap_severity_values(self):
        result = await _handle_analyze_sim_to_real_gap({
            "robot_path": "/World/Franka",
        })
        for gap in result["gaps"]:
            assert gap["severity"] in ("high", "medium", "low")
            assert "detail" in gap

    @pytest.mark.asyncio
    async def test_recommendations_count_matches_gaps(self):
        result = await _handle_analyze_sim_to_real_gap({
            "robot_path": "/World/Franka",
        })
        assert len(result["recommendations"]) == len(result["gaps"])

    @pytest.mark.asyncio
    async def test_franka_specific_physics_detail(self):
        result = await _handle_analyze_sim_to_real_gap({
            "robot_path": "/World/Franka",
        })
        physics_gap = next(g for g in result["gaps"] if g["category"] == "physics_fidelity")
        assert "16" in physics_gap["detail"]  # recommended solver iterations
        assert "franka" in physics_gap["detail"]


# ===========================================================================
# DATA: generate_transfer_report
# ===========================================================================

class TestGenerateTransferReport:
    """Tests for the generate_transfer_report data handler."""

    @pytest.mark.asyncio
    async def test_summary_format(self):
        result = await _handle_generate_transfer_report({
            "robot_path": "/World/Franka",
            "output_format": "summary",
        })
        assert isinstance(result, dict)
        assert "transfer_readiness" in result
        assert result["transfer_readiness"] in ("HIGH", "MEDIUM", "LOW")
        assert "overall_score" in result
        assert "action_items" in result
        assert "summary_text" in result
        assert "Sim-to-Real Transfer Report" in result["summary_text"]

    @pytest.mark.asyncio
    async def test_detailed_format(self):
        result = await _handle_generate_transfer_report({
            "robot_path": "/World/Franka",
            "output_format": "detailed",
        })
        assert "gaps_detail" in result
        assert "all_recommendations" in result
        assert isinstance(result["gaps_detail"], list)

    @pytest.mark.asyncio
    async def test_json_format(self):
        result = await _handle_generate_transfer_report({
            "robot_path": "/World/Franka",
            "output_format": "json",
        })
        assert "transfer_readiness" in result
        assert "action_items" in result
        # json format should NOT have summary_text or gaps_detail
        assert "summary_text" not in result
        assert "gaps_detail" not in result

    @pytest.mark.asyncio
    async def test_default_format_is_summary(self):
        result = await _handle_generate_transfer_report({
            "robot_path": "/World/Franka",
        })
        assert "summary_text" in result

    @pytest.mark.asyncio
    async def test_action_items_sorted_by_severity(self):
        result = await _handle_generate_transfer_report({
            "robot_path": "/World/Franka",
        })
        items = result["action_items"]
        # High severity items should come first
        severities = [item["severity"] for item in items]
        severity_order = {"high": 0, "medium": 1, "low": 2}
        for i in range(len(severities) - 1):
            assert severity_order[severities[i]] <= severity_order[severities[i + 1]]

    @pytest.mark.asyncio
    async def test_severity_counts(self):
        result = await _handle_generate_transfer_report({
            "robot_path": "/World/Franka",
        })
        total = (
            result["high_severity_count"]
            + result["medium_severity_count"]
            + result["low_severity_count"]
        )
        assert total == result["gap_count"]

    @pytest.mark.asyncio
    async def test_readiness_level_low(self):
        """With all default gaps, score should be 30, readiness LOW."""
        result = await _handle_generate_transfer_report({
            "robot_path": "/World/Franka",
        })
        # Default score = 100 - 25 - 15 - 15 - 10 - 5 = 30
        assert result["overall_score"] == 30
        assert result["transfer_readiness"] == "LOW"


# ===========================================================================
# Registration: ensure handlers are properly registered
# ===========================================================================

class TestRegistration:
    """Verify all sim-to-real tools are registered in the correct dispatch maps."""

    def test_code_gen_handlers_registered(self):
        assert "apply_domain_randomization" in CODE_GEN_HANDLERS
        assert "configure_actuator_model" in CODE_GEN_HANDLERS

    def test_data_handlers_registered(self):
        assert "analyze_sim_to_real_gap" in DATA_HANDLERS
        assert "generate_transfer_report" in DATA_HANDLERS
