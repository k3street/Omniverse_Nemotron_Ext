"""
L0 tests for the governance PolicyEngine.
Tests risk classification for different code patterns and config modes.
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.governance.policy_engine import PolicyEngine
from service.isaac_assist_service.governance.models import GovernanceConfig
from service.isaac_assist_service.planner.models import PatchAction


def _make_action(
    write_surface: str = "usd",
    target_path: str = "/World/Cube",
    new_value: str = "",
    confidence: float = 0.9,
    action_id: str = "a1",
) -> PatchAction:
    return PatchAction(
        action_id=action_id,
        order=1,
        write_surface=write_surface,
        target_path=target_path,
        action_type="set_property",
        new_value=new_value,
        confidence=confidence,
        reasoning="test",
    )


class TestEvaluateAction:

    def test_usd_low_risk(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(write_surface="usd")
        )
        assert risk == "low"

    def test_python_medium_risk(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(write_surface="python", target_path="/World/script.py")
        )
        assert risk == "medium"
        assert any("code" in r.lower() for r in reasons)

    def test_python_with_env_vars_high_risk(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(
                write_surface="python",
                target_path="/World/script.py",
                new_value="os.environ['SECRET'] = 'x'",
            )
        )
        assert risk == "high"
        assert any("environment" in r.lower() or "subprocess" in r.lower() for r in reasons)

    def test_python_with_subprocess_high_risk(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(
                write_surface="python",
                target_path="/World/script.py",
                new_value="subprocess.run(['rm', '-rf'])",
            )
        )
        assert risk == "high"

    def test_python_tmp_path_high_risk(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(write_surface="python", target_path="/tmp/evil.py")
        )
        assert risk == "high"

    def test_python_var_path_high_risk(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(write_surface="python", target_path="/var/log/app.py")
        )
        assert risk == "high"

    def test_settings_network_medium_risk(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(write_surface="settings", target_path="/config/network/proxy")
        )
        assert risk == "medium"
        assert any("network" in r.lower() for r in reasons)

    def test_low_confidence_bumps_to_medium(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(write_surface="usd", confidence=0.3)
        )
        assert risk == "medium"
        assert any("confidence" in r.lower() for r in reasons)

    def test_default_reason_for_safe_action(self, policy_engine):
        risk, reasons = policy_engine.evaluate_action(
            _make_action(write_surface="usd")
        )
        assert any("standard" in r.lower() for r in reasons)


class TestEvaluatePlan:

    def test_single_safe_action(self, policy_engine):
        result = policy_engine.evaluate_plan([_make_action()])
        assert result["overall_risk"] == "low"
        assert len(result["action_evaluations"]) == 1

    def test_mixed_risk_uses_highest(self, policy_engine):
        actions = [
            _make_action(write_surface="usd", action_id="a1"),
            _make_action(
                write_surface="python",
                target_path="/World/x.py",
                new_value="os.environ['X']='Y'",
                action_id="a2",
            ),
        ]
        result = policy_engine.evaluate_plan(actions)
        assert result["overall_risk"] == "high"

    def test_interactive_mode_always_requires_approval(self):
        cfg = GovernanceConfig(operational_mode="interactive")
        engine = PolicyEngine(cfg)
        result = engine.evaluate_plan([_make_action()])
        assert result["requires_approval"] is True

    def test_semi_autonomous_low_risk_no_approval(self):
        cfg = GovernanceConfig(operational_mode="semi_autonomous")
        engine = PolicyEngine(cfg)
        result = engine.evaluate_plan([_make_action()])
        assert result["requires_approval"] is False

    def test_semi_autonomous_medium_risk_requires_approval(self):
        cfg = GovernanceConfig(operational_mode="semi_autonomous")
        engine = PolicyEngine(cfg)
        result = engine.evaluate_plan([
            _make_action(write_surface="python", target_path="/World/x.py")
        ])
        assert result["requires_approval"] is True

    def test_empty_plan(self, policy_engine):
        result = policy_engine.evaluate_plan([])
        assert result["overall_risk"] == "low"
        assert result["action_evaluations"] == []
