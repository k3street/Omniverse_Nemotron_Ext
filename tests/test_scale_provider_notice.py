import asyncio
from types import SimpleNamespace

import pytest

from service.isaac_assist_service.scale_providers import (
    cosmos_reasoner_status,
    isaac_automator_configured,
    scale_provider_notice,
)


pytestmark = pytest.mark.l0


def _cfg(**overrides):
    values = {
        "scale_provider": "local",
        "dgx_spark_cosmos_base_url": "",
        "cosmos3_mode": "disabled",
        "cosmos3_reasoner_base_url": "",
        "cosmos3_reasoner_model": "nvidia/cosmos3-nano-reasoner",
        "gemini_robotics_er_fallback": False,
        "gemini_robotics_er_model": "gemini-robotics-er-1.6-preview",
        "api_key_gemini": "",
        "brev_api_key": "",
        "brev_project_id": "",
        "brev_template_id": "",
        "isaac_automator_root": "",
        "isaac_automator_deployment": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_isaac_automator_configured_with_deployment_name():
    config = _cfg(
        isaac_automator_root="/does/not/need/to/exist/when/deployment/is/set",
        isaac_automator_deployment="isaac-assist-remote",
    )

    assert isaac_automator_configured(config) is True


def test_scale_notice_is_quiet_when_configured_but_no_heavy_job():
    config = _cfg(
        isaac_automator_root="/tmp/IsaacAutomator",
        isaac_automator_deployment="isaac-assist-remote",
    )

    notice = scale_provider_notice(config)

    assert notice["configured"] == ["isaac_automator"]
    assert notice["should_notify"] is False
    assert notice["requires_user_approval"] is True


def test_scale_notice_warns_for_heavy_job_with_automator_configured():
    config = _cfg(
        isaac_automator_root="/tmp/IsaacAutomator",
        isaac_automator_deployment="isaac-assist-remote",
    )

    notice = scale_provider_notice(config, job_kind="cosmos_reasoner")

    assert notice["configured"] == ["isaac_automator"]
    assert notice["job_kind"] == "cosmos_reasoner"
    assert notice["should_notify"] is True
    assert "may suggest" in notice["message"]


def test_scale_notice_warns_when_preferred_provider_is_not_local():
    config = _cfg(
        scale_provider="isaac_automator",
        isaac_automator_root="/tmp/IsaacAutomator",
        isaac_automator_deployment="isaac-assist-remote",
    )

    notice = scale_provider_notice(config)

    assert notice["preferred_provider"] == "isaac_automator"
    assert notice["should_notify"] is True


def test_cosmos_reasoner_status_prefers_dgx_spark_endpoint():
    config = _cfg(
        dgx_spark_cosmos_base_url="http://192.168.1.42:8081/",
        cosmos3_reasoner_base_url="http://127.0.0.1:8081",
        cosmos3_mode="remote",
    )

    status = cosmos_reasoner_status(config)

    assert status["configured"] is True
    assert status["provider"] == "dgx_spark"
    assert status["base_url"] == "http://192.168.1.42:8081"
    assert status["health_url"] == "http://192.168.1.42:8081/v1/health/live"
    assert status["models_url"] == "http://192.168.1.42:8081/v1/models"
    assert status["is_loopback"] is False


def test_cosmos_reasoner_status_reports_enabled_without_endpoint():
    config = _cfg(cosmos3_mode="remote")

    status = cosmos_reasoner_status(config)

    assert status["configured"] is False
    assert "no reasoner endpoint" in status["message"]


def test_cosmos_reasoner_status_reports_gemini_fallback():
    config = _cfg(
        gemini_robotics_er_fallback=True,
        api_key_gemini="test-key",
    )

    status = cosmos_reasoner_status(config)

    assert status["configured"] is True
    assert status["provider"] == "gemini_robotics_er"
    assert status["fallback_configured"] is True
    assert status["health_url"] == ""
    assert "fallback is configured" in status["message"]


def test_cosmos_reasoner_route_returns_static_status():
    from service.isaac_assist_service.settings import routes

    response = asyncio.run(routes.get_cosmos_reasoner())

    assert response["status"] == "success"
    assert "reasoner" in response
    assert "configured" in response["reasoner"]
    assert response["reasoner"]["requires_user_approval"] is True
