"""CPU-only tests for the optional NVIDIA USD validation adapter."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from service.isaac_assist_service.analysis.validators import (
    create_all_validators,
    get_registered_validators,
)
from service.isaac_assist_service.analysis.validators import (
    nvidia_usd_validation as adapter,
)

pytestmark = pytest.mark.l0


def _report() -> dict:
    return {
        "status": "FAIL",
        "rules": [
            {
                "rule": {"type": "RULE", "name": "StageMetadataChecker"},
                "status": "FAIL",
                "issues": [
                    {
                        "type": "ISSUE",
                        "message": "Stage up axis is not authored.",
                        "severity": "WARNING",
                        "rule": {"type": "RULE", "name": "StageMetadataChecker"},
                        "at": {"type": "STAGE", "path": "/tmp/robot.usda"},
                        "suggestion": {
                            "type": "SUGGESTION",
                            "message": "Author an up axis.",
                        },
                        "suggestions": [
                            {"type": "SUGGESTION", "message": "Author an up axis."}
                        ],
                    }
                ],
            }
        ],
    }


def test_normalizes_nvidia_report_to_stage_finding():
    findings = adapter.normalize_nvidia_report(
        _report(), asset="/tmp/robot.usda", command_exit_code=1
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "nvidia_usd.stage_metadata_checker"
    assert finding.pack == "nvidia_usd_validation"
    assert finding.severity == "warning"
    assert finding.prim_path == "/tmp/robot.usda"
    assert finding.auto_fixable is False
    assert finding.evidence["command_exit_code"] == 1
    assert "Author an up axis" in finding.detail


def test_cli_exit_one_with_report_is_a_successful_run(monkeypatch, tmp_path: Path):
    executable = tmp_path / "nvidia_usd_validate"
    executable.write_text("placeholder")

    def fake_run(args, **kwargs):
        output = Path(args[args.index("--json-output") + 1])
        output.write_text(json.dumps(_report()))
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    findings = adapter.validate_asset(
        "/tmp/robot.usda", command=str(executable), timeout_seconds=1
    )
    assert [finding.rule_id for finding in findings] == [
        "nvidia_usd.stage_metadata_checker"
    ]


def test_missing_json_report_is_backend_error(monkeypatch, tmp_path: Path):
    executable = tmp_path / "nvidia_usd_validate"
    executable.write_text("placeholder")
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args, 2, stdout="", stderr="OpenUSD import failed"
        ),
    )
    findings = adapter.validate_asset(
        "/tmp/robot.usda", command=str(executable), timeout_seconds=1
    )
    assert findings[0].rule_id == "nvidia_usd.report_missing"
    assert findings[0].severity == "error"
    assert findings[0].evidence["output_tail"] == "OpenUSD import failed"


@pytest.mark.parametrize(
    ("stage_data", "expected"),
    [
        ({"stage_path": "/tmp/a.usda"}, "/tmp/a.usda"),
        ({"stage": {"stage_url": "/tmp/b.usdc"}}, "/tmp/b.usdc"),
        ({"stage_url": "file:///tmp/robot%20hand.usda"}, "/tmp/robot hand.usda"),
        ({"stage_url": "anon:0x123:World0.usda"}, None),
    ],
)
def test_stage_asset_path(stage_data, expected):
    assert adapter.stage_asset_path(stage_data) == expected


def test_external_pack_is_discoverable_but_not_default_enabled():
    assert "nvidia_usd_validation" in get_registered_validators()
    assert "nvidia_usd_validation" not in {
        rule.pack for rule in create_all_validators()
    }
    assert [
        rule.pack for rule in create_all_validators(["nvidia_usd_validation"])
    ] == ["nvidia_usd_validation"]


def test_review_queue_record_keeps_normalized_findings():
    record = adapter.findings_record(
        adapter.normalize_nvidia_report(
            _report(), asset="/tmp/robot.usda", command_exit_code=1
        )
    )
    assert record["backend"] == "usd-validation-nvidia"
    assert record["status"] == "passed_with_warnings"
    assert record["summary"] == {"error": 0, "warning": 1, "info": 0}
    assert record["findings"][0]["pack"] == "nvidia_usd_validation"
