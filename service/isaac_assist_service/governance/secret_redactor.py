import re
import logging
from typing import List, Optional

from .models import GovernanceConfig

logger = logging.getLogger(__name__)


# Phase 90 — extended pattern set.
#
# These patterns are ALWAYS compiled in addition to whatever is configured in
# GovernanceConfig.secret_patterns, so callers cannot accidentally disable
# protection for these very common credential shapes by overriding the config.
# Adding a new shape is purely additive: append a (name, regex, flags) tuple.
#
# Notes on each pattern:
#   * anthropic_api_key — matches sk-ant-* keys (with the optional
#     `api03-` segment). Liberal length lower bound (20+ chars after the
#     prefix) tolerates future key formats. Disjoint from the existing
#     OpenAI `sk-[a-zA-Z0-9]{32,}` pattern because that one's character
#     class excludes the dashes inside `sk-ant-` keys.
#   * gcp_private_key — matches the full PEM block from BEGIN through END.
#     re.DOTALL is required so `.` spans newlines inside the base64 body.
#     Covers both "BEGIN PRIVATE KEY" (PKCS#8) and "BEGIN RSA PRIVATE KEY"
#     (PKCS#1) forms, since GCP-exported service-account JSON uses PKCS#8
#     but legacy keys may be PKCS#1.
#   * slack_webhook — full webhook URL (T<workspace>/B<bot>/<secret>).
#   * stripe_key — covers sk_live, sk_test, pk_live, pk_test, rk_live,
#     rk_test. Real Stripe keys are ~32 chars after the prefix; 16-char
#     lower bound is intentionally conservative to also catch test
#     fixtures and short-form keys.
#   * github_pat_classic — `ghp_` prefix, 36+ alphanumerics (real PATs
#     are exactly 36 chars, lower bound matches that).
#   * github_pat_fine_grained — `github_pat_` prefix, 20+ chars
#     (underscores allowed since real fine-grained PATs embed `_`).
EXTENDED_SECRET_PATTERNS: List[tuple] = [
    ("anthropic_api_key", r"sk-ant-(?:api03-)?[A-Za-z0-9_\-]{20,}", 0),
    (
        "gcp_private_key",
        r"-----BEGIN (?:RSA )?PRIVATE KEY-----.*?-----END (?:RSA )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    (
        "slack_webhook",
        r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+",
        0,
    ),
    ("stripe_key", r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}", 0),
    ("github_pat_classic", r"ghp_[A-Za-z0-9]{36,}", 0),
    ("github_pat_fine_grained", r"github_pat_[A-Za-z0-9_]{20,}", 0),
]


class SecretRedactor:
    """Uses regex patterns to prevent accidental credential leakage."""

    def __init__(self, config: GovernanceConfig = None):
        self.config = config or GovernanceConfig()
        # Config-driven patterns first, then the always-on extended set
        # (Phase 90). Order does not matter for correctness because every
        # match is replaced by the same `[REDACTED_SECRET]` placeholder.
        self.compiled_patterns = [re.compile(p) for p in self.config.secret_patterns]
        for _name, pattern, flags in EXTENDED_SECRET_PATTERNS:
            self.compiled_patterns.append(re.compile(pattern, flags))

    def redact_text(self, text: str) -> str:
        """
        Replaces matched secrets in the text with a [REDACTED] placeholder.
        """
        if not text:
            return text

        redacted_text = text
        for pattern in self.compiled_patterns:
            # We use a lambda to avoid exposing the matched string, just replace it entirely.
            # For a more advanced version, we might want to keep the context.
            redacted_text = pattern.sub("[REDACTED_SECRET]", redacted_text)

        return redacted_text

    def redact_dict(self, data: dict) -> dict:
        """
        Recursively redact secrets from dictionary values.
        """
        redacted_data = {}
        for k, v in data.items():
            if isinstance(v, str):
                redacted_data[k] = self.redact_text(v)
            elif isinstance(v, dict):
                redacted_data[k] = self.redact_dict(v)
            elif isinstance(v, list):
                redacted_data[k] = [
                    self.redact_text(item) if isinstance(item, str)
                    else self.redact_dict(item) if isinstance(item, dict)
                    else item
                    for item in v
                ]
            else:
                redacted_data[k] = v
        return redacted_data

    def has_secrets(self, text: str) -> bool:
        """
        Returns true if the text contains secrets.
        """
        if not text:
            return False

        for pattern in self.compiled_patterns:
            if pattern.search(text):
                return True
        return False
