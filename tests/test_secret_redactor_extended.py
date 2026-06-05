"""Phase 90 — unit tests for the extended secret-redactor pattern set.

Verifies the five new pattern families added in Phase 90:
    1. Anthropic API keys (sk-ant-...)
    2. GCP service-account PEM private-key blocks
    3. Slack incoming-webhook URLs
    4. Stripe keys (sk_/pk_/rk_ * live_/test_)
    5. GitHub personal access tokens (classic ghp_ + fine-grained github_pat_)

Each test asserts:
    * The secret value is replaced with the existing [REDACTED_SECRET] placeholder.
    * The surrounding non-secret context (keys, prose) is preserved verbatim.

These tests are purely additive: they do not touch the existing AWS / OpenAI /
bearer-token patterns or their test coverage (test_routes.py::test_redact_text).
"""

import pytest

from service.isaac_assist_service.governance.secret_redactor import SecretRedactor


pytestmark = pytest.mark.l0

PLACEHOLDER = "[REDACTED_SECRET]"


def _redactor() -> SecretRedactor:
    """Fresh redactor with default GovernanceConfig (Phase 90 patterns always on)."""
    return SecretRedactor()


# ---------------------------------------------------------------------------
# 1. Anthropic API keys
# ---------------------------------------------------------------------------

def test_anthropic_api_key_is_redacted_in_json_log():
    secret = "sk-ant-api03-" "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_AbCdEfGhIj"
    log_line = (
        '{"event": "llm_call", "provider": "anthropic", '
        f'"api_key": "{secret}", "model": "claude-opus-4-7"}}'
    )

    redacted = _redactor().redact_text(log_line)

    assert secret not in redacted
    assert PLACEHOLDER in redacted
    # Non-secret context preserved
    assert '"event": "llm_call"' in redacted
    assert '"provider": "anthropic"' in redacted
    assert '"model": "claude-opus-4-7"' in redacted


def test_anthropic_api_key_without_api03_segment():
    # Liberal match: sk-ant-* without the optional api03- prefix.
    secret = "sk-ant-" "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    text = f"export ANTHROPIC_API_KEY={secret}"

    redacted = _redactor().redact_text(text)

    assert secret not in redacted
    assert PLACEHOLDER in redacted
    assert "export ANTHROPIC_API_KEY=" in redacted


# ---------------------------------------------------------------------------
# 2. GCP service-account private keys (PEM blocks)
# ---------------------------------------------------------------------------

def test_gcp_pkcs8_private_key_block_is_redacted_in_full():
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDJ4wxLN0V0w0lD\n"
        "Z3y/lp7yJfqkZkR5VxRgRjwRYK7bD5Yk1+example+base64+body+goes+here+\n"
        "+more+lines+of+base64+payload+for+the+private+key+material+here=\n"
        "-----END PRIVATE KEY-----"
    )
    text = (
        '{"type": "service_account", "project_id": "my-project", '
        f'"private_key": "{pem}", "client_email": "svc@my-project.iam.gserviceaccount.com"}}'
    )

    redacted = _redactor().redact_text(text)

    # Every line of the PEM body must be gone, including delimiters.
    assert "BEGIN PRIVATE KEY" not in redacted
    assert "END PRIVATE KEY" not in redacted
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDJ4wxLN0V0w0lD" not in redacted
    assert PLACEHOLDER in redacted
    # Surrounding JSON keys preserved.
    assert '"type": "service_account"' in redacted
    assert '"project_id": "my-project"' in redacted
    assert "svc@my-project.iam.gserviceaccount.com" in redacted


def test_gcp_pkcs1_rsa_private_key_block_is_redacted_in_full():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEArandomBase64ContentHereThatLooksLikeAnRSAKey1234\n"
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789==\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = f"legacy key dump:\n{pem}\nend of dump"

    redacted = _redactor().redact_text(text)

    assert "BEGIN RSA PRIVATE KEY" not in redacted
    assert "END RSA PRIVATE KEY" not in redacted
    assert "MIIEowIBAAKCAQEArandomBase64ContentHereThatLooksLikeAnRSAKey1234" not in redacted
    assert PLACEHOLDER in redacted
    assert "legacy key dump:" in redacted
    assert "end of dump" in redacted


# ---------------------------------------------------------------------------
# 3. Slack incoming-webhook URLs
# ---------------------------------------------------------------------------

def test_slack_webhook_url_is_redacted():
    secret = "https://hooks.slack.com/services/" "T01ABC2DEF3/B04GHI5JKL6/abcDEF123ghiJKL456mnoPQR"
    text = f"Configured webhook = {secret} for alerts channel"

    redacted = _redactor().redact_text(text)

    assert secret not in redacted
    assert "hooks.slack.com" not in redacted
    assert PLACEHOLDER in redacted
    # Context preserved.
    assert "Configured webhook = " in redacted
    assert "for alerts channel" in redacted


# ---------------------------------------------------------------------------
# 4. Stripe keys
# ---------------------------------------------------------------------------

def test_stripe_sk_live_is_redacted():
    secret = "sk_live_" "51HxYzABCDEFghijklmnopqrstuvwxyz0123456789ABCDE"
    text = f"STRIPE_SECRET_KEY={secret}\nSTRIPE_PUBLIC_KEY=pk_live_doesnotmatter"

    redacted = _redactor().redact_text(text)

    assert secret not in redacted
    assert PLACEHOLDER in redacted
    assert "STRIPE_SECRET_KEY=" in redacted


def test_stripe_all_prefix_variants_are_redacted():
    # Exercises sk_test_, pk_live_, rk_test_ — covers (sk|pk|rk)_(live|test) matrix.
    keys = [
        "sk_test_" "abcdef0123456789abcdef0123456789",
        "pk_live_" "zyxwvuABCDEFghijklmnopqrstuvwxyz",
        "rk_test_" "0123456789abcdefABCDEFghijklmnop",
    ]
    text = "config:\n" + "\n".join(f"  - {k}" for k in keys)

    redacted = _redactor().redact_text(text)

    for k in keys:
        assert k not in redacted, f"Stripe key {k!r} was not redacted"
    assert redacted.count(PLACEHOLDER) >= 3
    assert "config:" in redacted


# ---------------------------------------------------------------------------
# 5. GitHub personal access tokens
# ---------------------------------------------------------------------------

def test_github_classic_pat_is_redacted():
    # Classic PATs are `ghp_` + 36 alphanumerics.
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    text = f"GITHUB_TOKEN={secret}\necho hello"

    redacted = _redactor().redact_text(text)

    assert secret not in redacted
    assert PLACEHOLDER in redacted
    assert "GITHUB_TOKEN=" in redacted
    assert "echo hello" in redacted


def test_github_fine_grained_pat_is_redacted():
    # Fine-grained PATs begin `github_pat_` and contain underscores.
    secret = "github_pat_11ABCDE0Y0abcdEFGH" "_zyxwvutsrqponmlkjihgfedcba0987654321XYZ"
    text = f"Authorization: token {secret}"

    redacted = _redactor().redact_text(text)

    assert secret not in redacted
    assert PLACEHOLDER in redacted
    assert "Authorization: token " in redacted


# ---------------------------------------------------------------------------
# Cross-cutting: existing patterns and dict/list traversal still work.
# ---------------------------------------------------------------------------

def test_existing_openai_pattern_still_redacted_alongside_new_ones():
    # Sanity: extended patterns must not regress existing AWS / OpenAI / bearer.
    text = (
        "openai: sk-abcdefghijklmnopqrstuvwxyz123456ABCD\n"
        "anthropic: sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    )

    redacted = _redactor().redact_text(text)

    assert "sk-abcdefghijklmnopqrstuvwxyz123456ABCD" not in redacted
    assert "sk-ant-api03-" "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" not in redacted
    # Two distinct secrets => placeholder appears at least twice.
    assert redacted.count(PLACEHOLDER) >= 2
    assert "openai: " in redacted
    assert "anthropic: " in redacted


def test_redact_dict_walks_into_nested_values_for_new_patterns():
    payload = {
        "auth": {
            "anthropic_key": "sk-ant-api03-" "AbCdEfGhIjKlMnOpQrStUv0123456789ABCDEF",
            "github_pat": "ghp_" + "Z" * 40,
        },
        "webhooks": [
            "https://hooks.slack.com/services/" "T01ABC2DEF3/B04GHI5JKL6/secrettoken123abcDEF",
            "https://example.com/no-secret-here",
        ],
        "note": "plain text stays",
    }

    out = _redactor().redact_dict(payload)

    assert out["auth"]["anthropic_key"] == PLACEHOLDER
    assert out["auth"]["github_pat"] == PLACEHOLDER
    assert out["webhooks"][0] == PLACEHOLDER
    assert out["webhooks"][1] == "https://example.com/no-secret-here"
    assert out["note"] == "plain text stays"


def test_has_secrets_detects_new_patterns():
    r = _redactor()
    assert r.has_secrets("sk-ant-api03-" "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789") is True
    assert r.has_secrets("ghp_" + "Q" * 40) is True
    assert r.has_secrets(
        "https://hooks.slack.com/services/" "T01ABC2DEF3/B04GHI5JKL6/abcdefghijklmno"
    ) is True
    assert r.has_secrets("just a plain log line, nothing sensitive") is False
