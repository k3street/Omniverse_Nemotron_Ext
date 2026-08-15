"""Optional NVIDIA USD Validation adapter.

This pack runs NVIDIA's standalone validation engine in its own process and
converts its JSON report to the Stage Analyzer finding model. It is
deliberately opt-in: the tool has its own OpenUSD dependency and belongs in
``.venv-omniverse-tools`` rather than either Isaac Sim's or Newton's runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import unquote, urlparse

from .base import ValidationRule
from ..models import ValidationFinding

PACK = "nvidia_usd_validation"
DOCS_URL = "https://nvidia-omniverse.github.io/usd-validation-nvidia/validation/docs/rules.html"
REPO_ROOT = Path(__file__).resolve().parents[4]


def resolve_validator_command() -> list[str] | None:
    """Return the configured/dedicated NVIDIA validator invocation, if any.

    The project sidecar uses the official blocking Python API through a small
    bridge.  This avoids an observed ARM64 deadlock in the upstream CLI's
    asynchronous single-file path while retaining NVIDIA's engine/reporting.
    """
    configured = os.environ.get("NVIDIA_USD_VALIDATOR")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return [str(candidate.resolve())]
        found = shutil.which(configured)
        return [found] if found else None

    sidecar_python = REPO_ROOT / ".venv-omniverse-tools" / "bin" / "python"
    bridge = REPO_ROOT / "scripts" / "nvidia_usd_validate_bridge.py"
    if sidecar_python.is_file() and bridge.is_file():
        return [str(sidecar_python), str(bridge)]
    found = shutil.which("nvidia_usd_validate")
    return [found] if found else None


def stage_asset_path(stage_data: Dict[str, Any]) -> str | None:
    """Extract a local or resolver-backed USD asset URL from stage context."""
    candidates: list[Any] = [
        stage_data.get("stage_path"),
        stage_data.get("stage_url"),
        stage_data.get("file"),
    ]
    stage = stage_data.get("stage")
    if isinstance(stage, dict):
        candidates.extend((stage.get("stage_path"), stage.get("stage_url")))

    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if value.startswith("anon:"):
            continue
        if value.startswith("file://"):
            parsed = urlparse(value)
            return unquote(parsed.path)
        return value
    return None


def _finding_id(rule: str, prim_path: str | None, message: str) -> str:
    value = "\0".join((rule, prim_path or "", message))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _severity(value: Any) -> str:
    normalized = str(value or "INFO").upper()
    if normalized in {"FAILURE", "ERROR", "FATAL"}:
        return "error"
    if normalized in {"WARNING", "WARN"}:
        return "warning"
    return "info"


def _path_from_at(value: Any) -> str | None:
    if isinstance(value, dict):
        path = value.get("path")
        return str(path) if path else None
    if isinstance(value, list):
        for item in value:
            path = _path_from_at(item)
            if path:
                return path
    return None


def _rule_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "UnknownRule")
    return str(value or "UnknownRule")


def _rule_id(name: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index and chars[-1] != "_":
            chars.append("_")
        chars.append(char.lower() if char.isalnum() else "_")
    return "nvidia_usd." + "".join(chars).strip("_")


def _suggestion_messages(issue: Dict[str, Any]) -> list[str]:
    suggestions = issue.get("suggestions")
    if not isinstance(suggestions, list):
        suggestions = [issue.get("suggestion")] if issue.get("suggestion") else []
    return [
        str(item.get("message"))
        for item in suggestions
        if isinstance(item, dict) and item.get("message")
    ]


def normalize_nvidia_report(
    report: Dict[str, Any],
    *,
    asset: str,
    command_exit_code: int,
) -> List[ValidationFinding]:
    """Convert NVIDIA's rule-grouped JSON report into Stage Analyzer findings."""
    findings: list[ValidationFinding] = []
    rules = report.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("NVIDIA validation report has no 'rules' list")

    for rule_group in rules:
        if not isinstance(rule_group, dict):
            continue
        group_rule = _rule_name(rule_group.get("rule"))
        issues = rule_group.get("issues", [])
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            name = _rule_name(issue.get("rule") or group_rule)
            message = str(issue.get("message") or "NVIDIA USD validation issue")
            prim_path = _path_from_at(issue.get("at"))
            suggestions = _suggestion_messages(issue)
            detail = message
            if suggestions:
                detail += " Suggested action: " + " | ".join(suggestions)
            evidence = {
                "backend": "usd-validation-nvidia",
                "ruleset": os.environ.get(
                    "NVIDIA_USD_VALIDATION_RULESET", "robotics"
                ),
                "asset": asset,
                "nvidia_rule": name,
                "nvidia_severity": str(issue.get("severity") or "INFO"),
                "at": issue.get("at"),
                "requirement": issue.get("requirement"),
                "suggestions": suggestions,
                # Exit 1 is normal when the validator found issues.
                "command_exit_code": command_exit_code,
            }
            rule_id = _rule_id(name)
            findings.append(ValidationFinding(
                finding_id=_finding_id(rule_id, prim_path, message),
                rule_id=rule_id,
                pack=PACK,
                severity=_severity(issue.get("severity")),
                prim_path=prim_path,
                message=message,
                detail=detail,
                evidence=evidence,
                # NVIDIA suggestions remain review guidance.  This adapter
                # never invokes the CLI's mutating --fix path and does not
                # claim a Stage Analyzer structured FixSuggestion.
                auto_fixable=False,
                related_docs=[DOCS_URL],
            ))
    return findings


def _backend_finding(
    rule_suffix: str,
    severity: str,
    message: str,
    *,
    asset: str | None = None,
    evidence: Dict[str, Any] | None = None,
) -> ValidationFinding:
    rule_id = f"nvidia_usd.{rule_suffix}"
    merged = {"backend": "usd-validation-nvidia"}
    if asset:
        merged["asset"] = asset
    if evidence:
        merged.update(evidence)
    return ValidationFinding(
        finding_id=_finding_id(rule_id, None, message),
        rule_id=rule_id,
        pack=PACK,
        severity=severity,
        prim_path=None,
        message=message,
        detail=message,
        evidence=merged,
        auto_fixable=False,
        related_docs=[DOCS_URL],
    )


def validate_asset(
    asset: str,
    *,
    command: str | Sequence[str] | None = None,
    timeout_seconds: float | None = None,
) -> List[ValidationFinding]:
    """Run the NVIDIA validation subprocess and return normalized findings.

    The upstream-compatible process exits with status 1 when validation issues
    are present. A JSON report is therefore the success signal; the return
    code is retained as evidence rather than interpreted as a backend crash.
    """
    invocation = resolve_validator_command() if command is None else command
    if not invocation:
        return [_backend_finding(
            "validator_unavailable",
            "warning",
            "NVIDIA USD validation is not installed in the sidecar environment.",
            asset=asset,
            evidence={"expected_environment": ".venv-omniverse-tools"},
        )]

    command_parts = (
        [invocation] if isinstance(invocation, str) else list(invocation)
    )
    timeout = timeout_seconds
    if timeout is None:
        timeout = float(os.environ.get("NVIDIA_USD_VALIDATION_TIMEOUT", "60"))

    with tempfile.TemporaryDirectory(prefix="nvidia-usd-validation-") as tmp:
        report_path = Path(tmp) / "report.json"
        args = [
            *command_parts,
            "--process", "0",
            "--json-output", str(report_path),
            asset,
        ]
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return [_backend_finding(
                "backend_error",
                "error",
                f"NVIDIA USD validation backend failed: {exc}",
                asset=asset,
            )]

        if not report_path.is_file():
            output = (completed.stderr or completed.stdout or "").strip()[-800:]
            return [_backend_finding(
                "report_missing",
                "error",
                "NVIDIA USD validation did not produce a JSON report.",
                asset=asset,
                evidence={
                    "command_exit_code": completed.returncode,
                    "output_tail": output,
                },
            )]
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            return normalize_nvidia_report(
                report,
                asset=asset,
                command_exit_code=completed.returncode,
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return [_backend_finding(
                "report_invalid",
                "error",
                f"NVIDIA USD validation produced an invalid report: {exc}",
                asset=asset,
                evidence={"command_exit_code": completed.returncode},
            )]


def findings_record(findings: Iterable[ValidationFinding]) -> Dict[str, Any]:
    """Create the review-queue record stored beside the existing ingest report."""
    items = list(findings)
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in items:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    if counts["error"]:
        status = "failed"
    elif counts["warning"]:
        status = "passed_with_warnings"
    else:
        status = "passed"
    return {
        "backend": "usd-validation-nvidia",
        "status": status,
        "summary": counts,
        "findings": [finding.model_dump(mode="json") for finding in items],
    }


class NvidiaUsdValidationRule(ValidationRule):
    """Run NVIDIA's standalone USD validator against the stage root layer."""

    def __init__(self):
        super().__init__()
        self.rule_id = "nvidia_usd.validation"
        self.pack = PACK
        self.severity = "error"
        self.name = "NVIDIA USD validation"
        self.description = (
            "Runs usd-validation-nvidia in an isolated CPU sidecar environment."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        asset = stage_asset_path(stage_data)
        if not asset:
            return [_backend_finding(
                "stage_path_unavailable",
                "warning",
                "NVIDIA USD validation needs a saved stage root layer; save the anonymous stage first.",
            )]
        return validate_asset(asset)
