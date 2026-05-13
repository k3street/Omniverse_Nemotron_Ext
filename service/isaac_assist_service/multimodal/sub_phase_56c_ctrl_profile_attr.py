"""Phase 56c — ctrl:profile attribute + controller profile presets.

Extends the Phase 11c ctrl:* namespace with named scenario profiles so
that controllers can be switched between operational modes (e.g. from
development/debug to production_factory) without re-instantiation.

Each profile is a plain dict of ControllerAttrSet field overrides.  The
``profile`` field is always set to the profile name itself so the active
profile is self-describing when serialised to USD attrs.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 56c.
"""
from __future__ import annotations

from typing import Any, Dict, List

from service.isaac_assist_service.types.ctrl_namespace import ControllerAttrSet


PHASE_ID = "56c"
PHASE_TITLE = "ctrl profile attr"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 56c",
    }


# ---------------------------------------------------------------------------
# Named controller profile presets
# ---------------------------------------------------------------------------

#: Registry of named controller profiles.
#: Each value is a plain dict of ControllerAttrSet-compatible field overrides
#: (unknown keys are ignored by apply_profile for forward-compat).
#:
#: Profiles do NOT override ``adapter`` or ``phase`` — those are structural
#: and must be set by the controller itself.  They MAY set ``status``,
#: ``tick``, ``last_error``, and ``profile`` (always injected automatically
#: by ``apply_to_attrset``).
CONTROLLER_PROFILES: Dict[str, Dict[str, Any]] = {
    # ------------------------------------------------------------------
    # default — baseline settings, suitable for most interactive use
    # ------------------------------------------------------------------
    "default": {
        "profile": "default",
        "status": "ok",
        "last_error": None,
        # tick intentionally omitted — preserve whatever the controller has
    },
    # ------------------------------------------------------------------
    # high_precision — tighter tolerances, slower cycle time
    # Intended for tasks where positioning accuracy matters more than speed
    # (e.g. precision assembly, calibration procedures).
    # ------------------------------------------------------------------
    "high_precision": {
        "profile": "high_precision",
        "status": "ok",
        "last_error": None,
        # Downstream controllers read extra hint keys; ignored by attrset.
        "hint:tolerance_scale": 0.25,      # 25 % of default tolerance
        "hint:speed_scale": 0.40,          # 40 % of default speed
        "hint:replanning_enabled": True,
    },
    # ------------------------------------------------------------------
    # production_factory — fast cycle time, slightly looser tolerances
    # Optimised for throughput; suitable for established cell layouts
    # where geometry is known and collision margins are pre-validated.
    # ------------------------------------------------------------------
    "production_factory": {
        "profile": "production_factory",
        "status": "ok",
        "last_error": None,
        "hint:tolerance_scale": 1.5,
        "hint:speed_scale": 1.3,
        "hint:replanning_enabled": False,
    },
    # ------------------------------------------------------------------
    # development — extra diagnostics, conservative motion, verbose errors
    # ------------------------------------------------------------------
    "development": {
        "profile": "development",
        "status": "ok",
        "last_error": None,
        "hint:tolerance_scale": 1.0,
        "hint:speed_scale": 0.60,
        "hint:verbose_logging": True,
        "hint:replanning_enabled": True,
        "hint:telemetry_level": "debug",
    },
    # ------------------------------------------------------------------
    # safety_critical — hard stop on any anomaly; used in human-co-op cells
    # ------------------------------------------------------------------
    "safety_critical": {
        "profile": "safety_critical",
        "status": "ok",
        "last_error": None,
        "hint:tolerance_scale": 0.50,
        "hint:speed_scale": 0.30,
        "hint:hard_stop_on_fault": True,
        "hint:replanning_enabled": False,
        "hint:telemetry_level": "warn",
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_profile(name: str) -> Dict[str, Any]:
    """Return the preset dict for *name*.

    Raises
    ------
    KeyError
        If *name* is not a known profile.
    """
    if name not in CONTROLLER_PROFILES:
        known = sorted(CONTROLLER_PROFILES)
        raise KeyError(
            f"Unknown controller profile {name!r}. "
            f"Known profiles: {known}"
        )
    return dict(CONTROLLER_PROFILES[name])


def list_profiles() -> List[str]:
    """Return the sorted list of available profile names."""
    return sorted(CONTROLLER_PROFILES.keys())


def apply_to_attrset(attrset: ControllerAttrSet, profile_name: str) -> ControllerAttrSet:
    """Return a new :class:`ControllerAttrSet` with *profile_name* applied.

    Steps:

    1. Look up the preset via :func:`get_profile` (raises ``KeyError`` for
       unknown names).
    2. Call :meth:`ControllerAttrSet.apply_profile` — which filters out
       hint-prefixed and unknown keys so they don't cause Pydantic errors.
    3. Ensure the ``profile`` field is set to *profile_name* (the preset
       dict always includes this; the explicit override is a safety belt).

    Parameters
    ----------
    attrset:
        The base :class:`ControllerAttrSet` to copy and update.
    profile_name:
        Name of the profile preset to apply.

    Returns
    -------
    ControllerAttrSet
        A new frozen instance with the profile applied.
    """
    preset = get_profile(profile_name)
    # apply_profile filters to known model fields; hint:* keys are dropped.
    updated = attrset.apply_profile(preset)
    # Belt-and-suspenders: guarantee profile field is set even if the preset
    # dict somehow omitted it.
    if updated.profile != profile_name:
        updated = updated.with_profile(profile_name)
    return updated
