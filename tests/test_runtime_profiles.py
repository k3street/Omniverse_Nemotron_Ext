import pytest
import json
from pathlib import Path

pytestmark = pytest.mark.l0


def test_detects_isaacsim_6_source_build_from_env(monkeypatch):
    from service.isaac_assist_service.runtime_profiles import get_runtime_profile

    monkeypatch.delenv("ISAAC_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("ISAAC_VERSION", raising=False)
    monkeypatch.delenv("ISAAC_SIM_PATH", raising=False)
    monkeypatch.setenv("ISAAC_SIM_ROOT", "/home/kimate/IsaacSim/_build/linux-x86_64/release")

    profile = get_runtime_profile()
    assert profile.key == "isaacsim-6.0"
    assert profile.code_pattern_version == "6.0.0"
    assert profile.ros2_omnigraph_namespace == "isaacsim.ros2.nodes"


def test_unscoped_metadata_defaults_to_51_only():
    from service.isaac_assist_service.runtime_profiles import (
        ISAAC_SIM_51,
        ISAAC_SIM_60,
        metadata_matches_profile,
    )

    legacy = {"task_id": "CP-01"}
    assert metadata_matches_profile(legacy, ISAAC_SIM_51)
    assert not metadata_matches_profile(legacy, ISAAC_SIM_60)


def test_explicit_60_metadata_matches_60():
    from service.isaac_assist_service.runtime_profiles import (
        ISAAC_SIM_51,
        ISAAC_SIM_60,
        metadata_matches_profile,
    )

    template = {"runtime_profiles": ["isaacsim-6.0"], "task_id": "SIM6-DEMO"}
    assert metadata_matches_profile(template, ISAAC_SIM_60)
    assert not metadata_matches_profile(template, ISAAC_SIM_51)


def test_api_validator_rejects_wrong_ros2_node_namespace_for_60():
    from service.isaac_assist_service.chat.tools.api_validator import validate_code

    code = "node_type = 'isaacsim.ros2.bridge.ROS2PublishClock'"
    ok, issues = validate_code(code, profile="6.0")

    assert not ok
    assert any(i["severity"] == "version_mismatch" for i in issues)


def test_api_validator_rejects_wrong_ros2_node_namespace_for_51():
    from service.isaac_assist_service.chat.tools.api_validator import validate_code

    code = "node_type = 'isaacsim.ros2.nodes.ROS2PublishClock'"
    ok, issues = validate_code(code, profile="5.1")

    assert not ok
    assert any(i["severity"] == "version_mismatch" for i in issues)


def test_runtime_scope_summary_has_resource_boundaries():
    from service.isaac_assist_service.runtime_profiles import (
        ISAAC_SIM_51,
        ISAAC_SIM_60,
        runtime_scope_summary,
    )

    s51 = runtime_scope_summary(ISAAC_SIM_51)
    s60 = runtime_scope_summary(ISAAC_SIM_60)

    assert s51["extension_folder"] == "exts/isaac_5.1"
    assert s60["extension_folder"] == "exts/isaac_6.0"
    assert "code_patterns_5.1.0.jsonl" in " ".join(s51["knowledge_files"])
    assert "code_patterns_6.0.0.jsonl" in " ".join(s60["knowledge_files"])
    assert s51["ros2_omnigraph_namespace"] == "isaacsim.ros2.bridge"
    assert s60["ros2_omnigraph_namespace"] == "isaacsim.ros2.nodes"


def test_machine_readable_runtime_scope_policy_matches_profiles():
    from service.isaac_assist_service.runtime_profiles import ISAAC_SIM_51, ISAAC_SIM_60

    policy_path = Path("workspace/knowledge/runtime_scopes.json")
    policy = json.loads(policy_path.read_text())
    profiles = policy["profiles"]

    assert policy["default_unscoped_profile"] == ISAAC_SIM_51.key
    assert profiles[ISAAC_SIM_51.key]["extension_folder"] == ISAAC_SIM_51.extension_folder
    assert profiles[ISAAC_SIM_60.key]["extension_folder"] == ISAAC_SIM_60.extension_folder
    assert (
        profiles[ISAAC_SIM_51.key]["api_scope"]["ros2_omnigraph_namespace"]
        == ISAAC_SIM_51.ros2_omnigraph_namespace
    )
    assert (
        profiles[ISAAC_SIM_60.key]["api_scope"]["ros2_omnigraph_namespace"]
        == ISAAC_SIM_60.ros2_omnigraph_namespace
    )
