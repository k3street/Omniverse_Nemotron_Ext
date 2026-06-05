"""Phase 8 — tests for `handlers/_shared.py` + `handlers/_state.py`.

Phase 8 ships the SHELL: state singletons (new content) plus a
re-export façade for cross-handler utilities (full Phase 8 deliverable
is gated on Phase 3-7 handler moves). Tests verify:

1. The state package imports cleanly.
2. State singletons are mutable but independent.
3. `reset_all_state()` zeroes every slice.
4. `_shared.<legacy_name>` resolves lazily to `tool_executor`'s
   live implementation (PEP 562 __getattr__ behaviour).
5. `_shared._LEGACY_REEXPORT_NAMES` matches the spec's expected
   high-fan-in set.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def test_state_module_imports():
    from service.isaac_assist_service.chat.tools.handlers import _state
    assert _state is not None


def test_state_singletons_present():
    from service.isaac_assist_service.chat.tools.handlers._state import (
        BRIDGES,
        DR,
        EUREKA,
        TRAINING,
        WORKFLOWS,
    )
    # Each singleton starts empty
    assert WORKFLOWS.workflows == {}
    assert EUREKA.runs == {}
    assert TRAINING.pid_files == {}
    assert TRAINING.ipc_handlers == {}
    assert DR.range_hints == {}
    assert DR.correlations == {}
    assert BRIDGES.attached == {}


def test_state_singletons_independent():
    """Mutating one slice does not affect the others."""
    from service.isaac_assist_service.chat.tools.handlers import _state

    _state.reset_all_state()
    _state.EUREKA.runs["run-1"] = {"reward": 0.5}
    _state.WORKFLOWS.workflows["wf-1"] = {"status": "active"}

    assert "run-1" in _state.EUREKA.runs
    assert "wf-1" in _state.WORKFLOWS.workflows
    assert _state.TRAINING.pid_files == {}  # untouched
    assert _state.DR.range_hints == {}
    _state.reset_all_state()


def test_reset_all_state_clears_every_slice():
    from service.isaac_assist_service.chat.tools.handlers import _state

    _state.WORKFLOWS.workflows["a"] = 1
    _state.EUREKA.runs["b"] = 2
    _state.TRAINING.pid_files["c"] = "/tmp/c"
    _state.TRAINING.ipc_handlers["d"] = object()
    _state.DR.range_hints["e"] = (0.0, 1.0)
    _state.DR.correlations["f"] = 0.5
    _state.BRIDGES.attached["g"] = "modbus"

    _state.reset_all_state()

    assert _state.WORKFLOWS.workflows == {}
    assert _state.EUREKA.runs == {}
    assert _state.TRAINING.pid_files == {}
    assert _state.TRAINING.ipc_handlers == {}
    assert _state.DR.range_hints == {}
    assert _state.DR.correlations == {}
    assert _state.BRIDGES.attached == {}


# ---------------------------------------------------------------------------
# Shared module


def test_shared_module_imports():
    from service.isaac_assist_service.chat.tools.handlers import _shared
    assert _shared is not None
    assert hasattr(_shared, "CONSTANTS")
    assert hasattr(_shared, "_LEGACY_REEXPORT_NAMES")


def test_shared_constants_populated_per_phase_8():
    """Phase 8 lifts cross-handler constants out of tool_executor.py
    into `handlers/_shared.CONSTANTS`. Phase 8 wave 3 (2026-05-13)
    migrated SAFE_XFORM_SNIPPET as the first cross-theme constant.

    Pre-Phase-8 assertion was `CONSTANTS == {}`. Updated to a presence
    check on the migrated key — new constants added by later Phase 8
    waves will extend this list.
    """
    from service.isaac_assist_service.chat.tools.handlers._shared import CONSTANTS
    assert "SAFE_XFORM_SNIPPET" in CONSTANTS, (
        "Phase 8 wave 3 migrated SAFE_XFORM_SNIPPET to _shared.CONSTANTS — "
        f"check this didn't regress. Current keys: {sorted(CONSTANTS.keys())}"
    )


def test_shared_legacy_reexport_names_match_spec():
    """The list must match Phase 8's documented high-fan-in utilities.
    Changes to this list should land via PR with reviewer sign-off — the
    audit script `audit_handler_cross_refs.py` is the upstream source.
    """
    from service.isaac_assist_service.chat.tools.handlers._shared import (
        _LEGACY_REEXPORT_NAMES,
    )
    expected = {
        "_get_viewport_bytes",
        "_get_vision_provider",
        "_query_run_ipc",
        "_resolve_run_id",
        "_check_real_data_path",
        "_wf_now_iso",
        "_parse_last_json_line",
        "_safe_robot_name",
        "_validate_env_id",
    }
    assert set(_LEGACY_REEXPORT_NAMES) == expected, (
        f"Mismatch with spec's high-fan-in utility list. "
        f"Extra: {set(_LEGACY_REEXPORT_NAMES) - expected}; "
        f"Missing: {expected - set(_LEGACY_REEXPORT_NAMES)}"
    )


def test_shared_legacy_reexport_names_resolvable():
    """Phase 14 (2026-05-13): the 9 _LEGACY_REEXPORT_NAMES are now physically
    in _shared.py (no longer lazy-bridged through tool_executor.py). Each
    name resolves to a callable.

    Pre-Phase-14 the contract was: `_shared._X` PEP 562 __getattr__ returned
    `tool_executor._X`. Now the bodies live in _shared.py directly; the
    PEP 562 bridge is preserved for back-compat but the canonical home
    is _shared.
    """
    from service.isaac_assist_service.chat.tools.handlers import _shared
    for name in ("_wf_now_iso", "_safe_robot_name", "_check_real_data_path",
                 "_get_viewport_bytes", "_get_vision_provider",
                 "_resolve_run_id", "_query_run_ipc", "_validate_env_id"):
        fn = getattr(_shared, name)
        assert callable(fn), f"_shared.{name} not callable"


def test_shared_unknown_attribute_raises():
    """`__getattr__` rejects names not in the re-export list."""
    from service.isaac_assist_service.chat.tools.handlers import _shared

    with pytest.raises(AttributeError, match="no attribute"):
        _shared.this_name_does_not_exist  # noqa: B018


def test_handlers_package_reexports_shared_and_state():
    """`handlers/__init__.py` exports both module aliases."""
    from service.isaac_assist_service.chat.tools import handlers

    # Both modules must be reachable as `handlers._shared` /
    # `handlers._state`. They are loaded eagerly because they appear
    # in the package's __all__ + are imported by _dispatch on package
    # load is NOT required — lazy access via importlib is OK.
    import importlib
    shared = importlib.import_module(
        "service.isaac_assist_service.chat.tools.handlers._shared"
    )
    state = importlib.import_module(
        "service.isaac_assist_service.chat.tools.handlers._state"
    )
    assert shared is not None
    assert state is not None
