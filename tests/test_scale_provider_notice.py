from types import SimpleNamespace

import pytest

from service.isaac_assist_service.scale_providers import (
    isaac_automator_configured,
    scale_provider_notice,
)


pytestmark = pytest.mark.l0


def _cfg(**overrides):
    values = {
        "scale_provider": "local",
        "dgx_spark_cosmos_base_url": "",
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
