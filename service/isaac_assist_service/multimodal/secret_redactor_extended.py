"""Phase 90 — secret redactor: extended pattern set.

Adds detection of more credential shapes to the redactor: cloud-API
keys (AWS, GCP, Azure), GitHub tokens, NVIDIA NGC tokens, Slack
webhooks, and generic high-entropy strings.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 90.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List


PHASE_ID = 90
PHASE_TITLE = "Secret redactor extended pattern set"
PHASE_STATUS = "landed"


SECRET_PATTERNS: List[tuple] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret_key", re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9])")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("github_oauth", re.compile(r"gho_[A-Za-z0-9]{36}")),
    ("github_app", re.compile(r"ghs_[A-Za-z0-9]{36}")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+")),
    ("nvidia_ngc", re.compile(r"nvapi-[A-Za-z0-9_-]{60,}")),
    ("gcp_service_account_key", re.compile(r'"private_key_id"\s*:\s*"[a-f0-9]{40}"')),
    ("azure_subscription_key", re.compile(r"[a-f0-9]{32}")),
    ("generic_bearer", re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+")),
]


def redact(text: str) -> str:
    """Redact secrets in text. Each match becomes `<redacted-{kind}>`."""
    for kind, pattern in SECRET_PATTERNS:
        text = pattern.sub(f"<redacted-{kind}>", text)
    return text


def detect_kinds(text: str) -> List[str]:
    """Return list of detected secret kinds (no redaction)."""
    kinds = []
    for kind, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            kinds.append(kind)
    return kinds


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 90",
    }
