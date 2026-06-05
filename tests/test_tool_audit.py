"""Phase 1 — tests for the tool inventory audit.

Asserts the invariants the spec lists for tool registration:
  - every schema name resolves to one of: real handler, callable None
    (allowlisted), composite (only `setup_pick_place_with_vision`),
    or is in the no-handler fixture.
  - no name appears in both DATA + CODE_GEN unless it's the allowed
    composite.
  - the allowlist itself is consistent (every name in the JSON is in
    fact None in DATA_HANDLERS or CODE_GEN_HANDLERS, or absent from
    both — i.e. the fixture is not stale).

Read-only assertions — no code mutations. Uses the same import pattern
as `test_tool_schemas.py`.
"""
from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# Live imports of the dispatch dicts + schemas.
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
)

_REPO_ROOT = Path(__file__).parent.parent
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "no_handler_tools.json"
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "audit_tools.py"


# Load the audit module to share its constants (INLINE_HANDLED, allowlist).
@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("audit_tools", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_tools"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("audit_tools", None)


@pytest.fixture(scope="module")
def allowlist():
    data = json.loads(_FIXTURE_PATH.read_text())
    return frozenset(data.get("none_handlers", []))


@pytest.fixture(scope="module")
def schema_names():
    return [t["function"]["name"] for t in ISAAC_SIM_TOOLS]


# ---------------------------------------------------------------------------
# Core invariants


def test_every_schema_name_resolves_to_known_status(audit, allowlist, schema_names):
    """The spec's central assertion: every schema name is one of:
      - in DATA_HANDLERS (real or allowlisted None)
      - in CODE_GEN_HANDLERS (real or allowlisted None)
      - inline-handled (in audit._INLINE_HANDLED)
      - (no fallback) — anything else is a ghost.
    """
    inline = audit._INLINE_HANDLED
    composites_allowed = audit._ALLOWED_COMPOSITES

    ghosts: list[str] = []
    for name in schema_names:
        in_data = name in DATA_HANDLERS
        in_code = name in CODE_GEN_HANDLERS
        data_callable = in_data and DATA_HANDLERS[name] is not None
        code_callable = in_code and CODE_GEN_HANDLERS[name] is not None

        if name in inline:
            continue
        if data_callable or code_callable:
            continue
        if (in_data or in_code) and name in allowlist:
            continue  # explicit None, allowlisted
        # Composite case (in both): only legitimate if allowlisted composite
        if in_data and in_code and name in composites_allowed:
            continue
        ghosts.append(name)

    assert not ghosts, (
        f"Phase 1 ghost-handler check: {len(ghosts)} schema names have "
        f"no handler registration and are not in the allowlist:\n"
        + "\n".join(f"  - {n}" for n in sorted(ghosts))
    )


def test_no_unexpected_composite_registrations(schema_names, audit):
    """Names appearing in BOTH DATA + CODE_GEN must be on the
    `_ALLOWED_COMPOSITES` short-list. Today only one is allowed:
    `setup_pick_place_with_vision`.
    """
    allowed = audit._ALLOWED_COMPOSITES
    composites = [
        name
        for name in schema_names
        if name in DATA_HANDLERS and name in CODE_GEN_HANDLERS
    ]
    unexpected = [c for c in composites if c not in allowed]
    assert not unexpected, (
        f"Unexpected composite registrations (in both DATA + CODE_GEN): "
        f"{unexpected}. If intentional, add to `_ALLOWED_COMPOSITES` in "
        f"`scripts/audit_tools.py`."
    )


def test_allowlist_is_not_stale(allowlist):
    """Every name in the allowlist must actually have a `None` handler
    somewhere (or be absent from both dicts entirely — the conditional-
    import case for ROS2 tools when ros-mcp IS installed). If a name is
    in the allowlist but has a callable handler in DATA_HANDLERS or
    CODE_GEN_HANDLERS, the fixture is stale.
    """
    stale: list[str] = []
    for name in allowlist:
        d_value = DATA_HANDLERS.get(name, "MISSING")
        c_value = CODE_GEN_HANDLERS.get(name, "MISSING")
        # Stale if a callable is registered (the allowlist says it
        # *should* be None or absent — finding a callable contradicts).
        if (d_value is not None and d_value != "MISSING") and callable(d_value):
            stale.append(f"{name} (DATA_HANDLERS has callable)")
        if (c_value is not None and c_value != "MISSING") and callable(c_value):
            stale.append(f"{name} (CODE_GEN_HANDLERS has callable)")
    assert not stale, (
        "Stale entries in tests/fixtures/no_handler_tools.json — these names "
        "have callable handlers, so they should not be in the allowlist:\n"
        + "\n".join(f"  - {s}" for s in stale)
    )


def test_classify_function_unit(audit):
    """The classifier is pure — exercise each branch."""
    al = frozenset(["explain_error", "ros2_connect"])

    # Inline-handled → real
    assert audit.classify("run_usd_script", False, False, False, False, al) == "real"
    # Callable in DATA only → real
    assert audit.classify("foo", True, True, False, False, al) == "real"
    # Callable in CODE only → real
    assert audit.classify("foo", False, False, True, True, al) == "real"
    # None in DATA, allowlisted → none_explicit
    assert audit.classify("explain_error", True, False, False, False, al) == "none_explicit"
    # None in DATA, NOT allowlisted → ghost_none
    assert audit.classify("foo", True, False, False, False, al) == "ghost_none"
    # In neither → ghost
    assert audit.classify("foo", False, False, False, False, al) == "ghost"
    # Composite allowed
    assert (
        audit.classify(
            "setup_pick_place_with_vision", True, True, True, True, al
        )
        == "composite"
    )
    # Composite unexpected
    assert audit.classify("foo", True, True, True, True, al) == "composite_unexpected"


# ---------------------------------------------------------------------------
# End-to-end: audit must run and produce a report


def test_audit_runs_end_to_end(audit, allowlist, tmp_path):
    """The full audit produces a markdown report against the live
    dispatch dicts."""
    report = audit.collect_inventory(allowlist)
    assert len(report.tools) > 100, "Sanity: expected >100 schema entries"
    md = audit.render_markdown(report)
    assert md.startswith("# Tool Audit")
    assert "Status counts" in md
    out = tmp_path / "tool_audit.md"
    out.write_text(md)
    assert out.exists() and out.stat().st_size > 100


# ---------------------------------------------------------------------------
# Phase 4 — CI gate: every tool resolves to a handler


def test_every_tool_resolves(audit, allowlist, schema_names):
    """Phase 4 CI gate (per IA_FULL_SPEC_2026-05-10.md Phase 4).

    The single load-bearing invariant for Epoch I + every later phase:
    every schema name either has a callable handler, or is on the
    curated allowlist in `tests/fixtures/no_handler_tools.json`.

    A new tool added to `tool_schemas.py` without a corresponding
    handler ⇒ this test fails. A handler removed without
    schema/allowlist update ⇒ this test fails. Phase 3-7 handler
    moves run safely behind this gate.

    The error message names the unresolved tools so the operator
    can either: (a) add a handler, (b) add a reasoned allowlist
    entry, or (c) remove the schema.
    """
    inline = audit._INLINE_HANDLED  # special-cased orchestrator-handled tools
    composites_allowed = audit._ALLOWED_COMPOSITES

    unresolved: list[str] = []
    for name in schema_names:
        in_data = name in DATA_HANDLERS
        in_code = name in CODE_GEN_HANDLERS
        data_callable = in_data and DATA_HANDLERS[name] is not None
        code_callable = in_code and CODE_GEN_HANDLERS[name] is not None

        if name in inline:
            continue
        if data_callable or code_callable:
            continue
        if (in_data or in_code) and name in allowlist:
            continue
        if in_data and in_code and name in composites_allowed:
            continue
        unresolved.append(name)

    assert not unresolved, (
        "Phase 4 CI gate failed: the following tool schemas have no "
        "handler and are not allowlisted. Either implement a handler, "
        "add a reasoned entry to tests/fixtures/no_handler_tools.json, "
        "or remove the schema:\n"
        + "\n".join(f"  - {n}" for n in sorted(unresolved))
    )


def test_allowlist_reasons_present_for_every_none_handler():
    """Phase 4 quality gate: every name in `none_handlers` must have
    a corresponding entry in `reasons` (so future audits can see WHY
    a tool is allowlisted rather than implemented).
    """
    data = json.loads(_FIXTURE_PATH.read_text())
    names = data.get("none_handlers", [])
    reasons = data.get("reasons", {})
    missing = [n for n in names if n not in reasons]
    assert not missing, (
        "Allowlist entries missing reasons (Phase 4 contract): "
        f"{missing}. Every name in `none_handlers` must appear in "
        "`reasons` with `reason`, `planned_status`, `owner` fields."
    )


def test_allowlist_reasons_have_required_fields():
    """Phase 4 quality gate: each reason entry must include the
    three fields the audit consumes (reason / planned_status / owner).
    """
    data = json.loads(_FIXTURE_PATH.read_text())
    reasons = data.get("reasons", {})
    required = {"reason", "planned_status", "owner"}
    for name, entry in reasons.items():
        if not isinstance(entry, dict):
            pytest.fail(f"reasons[{name!r}] must be a dict, got {type(entry).__name__}")
        missing = required - set(entry)
        assert not missing, (
            f"reasons[{name!r}] missing required fields {missing}; "
            f"expected: {required}"
        )
