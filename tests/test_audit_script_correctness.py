"""Validate that `scripts/qa/post_migration_health_check.py` correctly
classifies positive and negative fixtures for each check.

This is Fas 0 V1 from `docs/qa/PATH_TO_100PCT.md`: we can't run a 100%
audit without a 100%-correct audit script. Each check has a positive
fixture (must flag) and a negative fixture (must not flag).

The fixtures live in `tests/qa_audit_fixtures/`. Each fixture is plain
Python that the audit-script's AST walker reads directly.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "qa_audit_fixtures"
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "qa" / "post_migration_health_check.py"


@pytest.fixture(scope="module")
def audit_module():
    """Load the audit script as a module to call its check functions."""
    spec = importlib.util.spec_from_file_location("audit_module", AUDIT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_fixture(name: str) -> ast.AST:
    return ast.parse((FIXTURES_DIR / name).read_text(), filename=name)


# ---------------------------------------------------------------------------
# Q21 Section 19 honesty — positive fixture must flag, negative must not
# ---------------------------------------------------------------------------

def test_section_19_positive_flags_expected_violations(audit_module):
    """Positive fixture has 4 declared violations.

    Audit must flag ≥4 hits and they must reside in the positive fixture.
    """
    # Patch HANDLERS_ROOT to point at the fixtures dir so the check scans it
    import types
    audit_module.HANDLERS_ROOT = FIXTURES_DIR

    hits = audit_module.check_section_19_honesty()
    positive_hits = [h for h in hits if "section_19_positive" in h["file"]]

    # Expected violations from the fixture:
    # 1. _handle_bare_return_none — return None at line 12
    # 2. _handle_implicit_return — fall-through
    # 3. _handle_return_constant_none — return None at line 24
    # 4. _handle_mixed_with_nested — nested helper has bare return (MUST NOT flag)
    handler_names = {h["handler"] for h in positive_hits}

    assert "_handle_bare_return_none" in handler_names
    assert "_handle_implicit_return" in handler_names
    assert "_handle_return_constant_none" in handler_names

    # CRITICAL: the nested-scope handler must NOT be flagged
    nested_hit = next(
        (h for h in positive_hits if h["handler"] == "_handle_mixed_with_nested"),
        None,
    )
    assert nested_hit is None, (
        "_handle_mixed_with_nested has a bare return INSIDE a nested helper; "
        "audit must not flag that as a handler-level honesty hole. Got: "
        f"{nested_hit}"
    )


def test_section_19_negative_yields_zero_hits(audit_module):
    """Negative fixture has zero violations — audit must produce zero hits there."""
    audit_module.HANDLERS_ROOT = FIXTURES_DIR

    hits = audit_module.check_section_19_honesty()
    negative_hits = [h for h in hits if "section_19_negative" in h["file"]]

    assert negative_hits == [], (
        f"Expected zero hits in negative fixture, got {len(negative_hits)}: "
        f"{negative_hits}"
    )


# ---------------------------------------------------------------------------
# Q3 datetime.utcnow — positive fixture has known hits, negative has none
# ---------------------------------------------------------------------------

def test_q3_utcnow_positive(audit_module):
    """Positive fixture has 3 utcnow() call sites."""
    audit_module.SERVICE_ROOT = FIXTURES_DIR

    hits = audit_module.check_no_utcnow()
    positive_hits = [h for h in hits if "deprecation_positive" in h["file"]]

    # 3 calls: use_utcnow (1), use_utcnow_twice (2) = 3 total
    assert len(positive_hits) == 3, (
        f"Expected 3 utcnow hits in positive fixture, got {len(positive_hits)}"
    )


def test_q3_utcnow_negative(audit_module):
    """Negative fixture uses datetime.now(timezone.utc); zero hits."""
    audit_module.SERVICE_ROOT = FIXTURES_DIR

    hits = audit_module.check_no_utcnow()
    negative_hits = [h for h in hits if "deprecation_negative" in h["file"]]

    assert negative_hits == [], (
        f"Expected zero utcnow hits in negative fixture, got {len(negative_hits)}: "
        f"{negative_hits}"
    )


# ---------------------------------------------------------------------------
# Q4 asyncio.get_event_loop — positive (1 hit outside run_stdio), negative (0)
# ---------------------------------------------------------------------------

def test_q4_get_event_loop_positive(audit_module):
    """Positive fixture has 1 call outside run_stdio + 1 inside (whitelisted)."""
    audit_module.SERVICE_ROOT = FIXTURES_DIR

    hits = audit_module.check_no_get_event_loop()
    positive_hits = [h for h in hits if "deprecation_positive" in h["file"]]

    # Only the call in use_get_event_loop should be flagged.
    # The call inside run_stdio() is whitelisted.
    assert len(positive_hits) == 1, (
        f"Expected 1 get_event_loop hit (outside run_stdio), got {len(positive_hits)}"
    )


def test_q4_get_event_loop_negative(audit_module):
    """Negative fixture uses get_running_loop; zero hits."""
    audit_module.SERVICE_ROOT = FIXTURES_DIR

    hits = audit_module.check_no_get_event_loop()
    negative_hits = [h for h in hits if "deprecation_negative" in h["file"]]

    assert negative_hits == [], (
        f"Expected zero get_event_loop hits in negative fixture, got {len(negative_hits)}"
    )
