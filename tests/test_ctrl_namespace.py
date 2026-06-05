"""
L0 tests for the ctrl:* controller attribute namespace (spec Phase 11c).

Covers ControllerAttrSet validation, USD attr serialisation round-trip,
strict adapter validation, frozen-model contract, JSON round-trip, and
import-purity (the module pulls in only pydantic + typing).
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.l0

from service.isaac_assist_service.types.ctrl_namespace import (
    AdapterToken,
    ControllerAttrSet,
    StatusToken,
)


# ---------------------------------------------------------------------------
# to_usd_attrs — full / partial
# ---------------------------------------------------------------------------

class TestToUsdAttrs:
    def test_full_field_set_emits_six_keys(self):
        s = ControllerAttrSet(
            adapter="curobo",
            phase="planning",
            tick=42,
            status="ok",
            last_error="prev: timeout",
            profile="tabletop_pick",
        )
        attrs = s.to_usd_attrs()
        assert set(attrs.keys()) == {
            "ctrl:adapter",
            "ctrl:phase",
            "ctrl:tick",
            "ctrl:status",
            "ctrl:last_error",
            "ctrl:profile",
        }
        assert attrs["ctrl:adapter"] == "curobo"
        assert attrs["ctrl:phase"] == "planning"
        assert attrs["ctrl:tick"] == 42
        assert attrs["ctrl:status"] == "ok"
        assert attrs["ctrl:last_error"] == "prev: timeout"
        assert attrs["ctrl:profile"] == "tabletop_pick"

    def test_without_optional_fields_emits_four_keys(self):
        s = ControllerAttrSet(adapter="builtin_pp", phase="approach")
        attrs = s.to_usd_attrs()
        assert set(attrs.keys()) == {
            "ctrl:adapter",
            "ctrl:phase",
            "ctrl:tick",
            "ctrl:status",
        }
        # Defaults
        assert attrs["ctrl:tick"] == 0
        assert attrs["ctrl:status"] == "ok"

    def test_optional_fields_independently_included(self):
        # last_error set, profile None → 5 keys
        s = ControllerAttrSet(
            adapter="spline", phase="track", last_error="off-path"
        )
        attrs = s.to_usd_attrs()
        assert "ctrl:last_error" in attrs
        assert "ctrl:profile" not in attrs

        # profile set, last_error None → 5 keys
        s2 = ControllerAttrSet(
            adapter="spline", phase="track", profile="warehouse"
        )
        attrs2 = s2.to_usd_attrs()
        assert "ctrl:profile" in attrs2
        assert "ctrl:last_error" not in attrs2

    def test_tick_coerced_to_int(self):
        # Pydantic will accept int already; to_usd_attrs forces int() cast.
        s = ControllerAttrSet(adapter="curobo", phase="planning", tick=7)
        attrs = s.to_usd_attrs()
        assert isinstance(attrs["ctrl:tick"], int)
        assert attrs["ctrl:tick"] == 7


# ---------------------------------------------------------------------------
# from_usd_attrs — namespace handling, defaults, errors
# ---------------------------------------------------------------------------

class TestFromUsdAttrs:
    def test_round_trip_full_set(self):
        original = ControllerAttrSet(
            adapter="curobo",
            phase="planning",
            tick=42,
            status="ok",
            last_error="prev: timeout",
            profile="tabletop_pick",
        )
        reconstructed = ControllerAttrSet.from_usd_attrs(original.to_usd_attrs())
        assert reconstructed == original

    def test_round_trip_minimal_set(self):
        original = ControllerAttrSet(adapter="builtin_pp", phase="approach")
        reconstructed = ControllerAttrSet.from_usd_attrs(original.to_usd_attrs())
        assert reconstructed == original
        assert reconstructed.tick == 0
        assert reconstructed.status == "ok"
        assert reconstructed.last_error is None
        assert reconstructed.profile is None

    def test_accepts_prefixed_keys(self):
        attrs = {
            "ctrl:adapter": "constraint_pull",
            "ctrl:phase": "engaged",
            "ctrl:tick": 99,
            "ctrl:status": "stalled",
        }
        s = ControllerAttrSet.from_usd_attrs(attrs)
        assert s.adapter == "constraint_pull"
        assert s.phase == "engaged"
        assert s.tick == 99
        assert s.status == "stalled"

    def test_accepts_unprefixed_keys(self):
        # Callers that already stripped the namespace prefix should still work.
        attrs = {
            "adapter": "constraint_pull",
            "phase": "engaged",
            "tick": 99,
            "status": "fault",
        }
        s = ControllerAttrSet.from_usd_attrs(attrs)
        assert s.adapter == "constraint_pull"
        assert s.phase == "engaged"
        assert s.tick == 99
        assert s.status == "fault"

    def test_raises_when_adapter_missing(self):
        with pytest.raises(ValueError, match="missing 'ctrl:adapter'"):
            ControllerAttrSet.from_usd_attrs({"ctrl:phase": "planning"})

    def test_defaults_when_optional_keys_absent(self):
        attrs = {"ctrl:adapter": "curobo", "ctrl:phase": "planning"}
        s = ControllerAttrSet.from_usd_attrs(attrs)
        assert s.tick == 0
        assert s.status == "ok"
        assert s.last_error is None
        assert s.profile is None


# ---------------------------------------------------------------------------
# validate_strict_adapter — known vs unknown adapter tokens
# ---------------------------------------------------------------------------

class TestValidateStrictAdapter:
    @pytest.mark.parametrize(
        "token", ["curobo", "builtin_pp", "spline", "constraint_pull"]
    )
    def test_known_tokens_pass(self, token: str):
        s = ControllerAttrSet(adapter=token, phase="x")
        # Should NOT raise
        s.validate_strict_adapter()

    @pytest.mark.parametrize(
        "token", ["", "unknown", "curobo2", "BUILTIN_PP", "random"]
    )
    def test_unknown_tokens_raise(self, token: str):
        s = ControllerAttrSet(adapter=token, phase="x")
        with pytest.raises(ValueError, match="not in the known set"):
            s.validate_strict_adapter()


# ---------------------------------------------------------------------------
# Pydantic Literal / type-level validation
# ---------------------------------------------------------------------------

class TestPydanticValidation:
    def test_status_literal_rejects_unknown(self):
        with pytest.raises(ValidationError):
            ControllerAttrSet(adapter="curobo", phase="x", status="invalid")  # type: ignore[arg-type]

    def test_status_literal_accepts_known(self):
        for status in ("ok", "stalled", "fault"):
            s = ControllerAttrSet(
                adapter="curobo", phase="x", status=status  # type: ignore[arg-type]
            )
            assert s.status == status

    def test_adapter_accepts_arbitrary_string(self):
        # Forward-compat: schema-level adapter is `str`, not the Literal.
        s = ControllerAttrSet(adapter="some_future_adapter", phase="x")
        assert s.adapter == "some_future_adapter"

    def test_tick_rejects_non_int(self):
        # Pydantic v2 strict-ish int coercion: a float with decimal part
        # is rejected; a plain string that doesn't parse is rejected.
        with pytest.raises(ValidationError):
            ControllerAttrSet(adapter="curobo", phase="x", tick="not-an-int")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Frozen model — immutability contract
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_assignment_after_construction_raises(self):
        s = ControllerAttrSet(adapter="curobo", phase="planning")
        with pytest.raises(ValidationError):
            s.adapter = "builtin_pp"  # type: ignore[misc]

    def test_assignment_to_tick_raises(self):
        s = ControllerAttrSet(adapter="curobo", phase="planning", tick=1)
        with pytest.raises(ValidationError):
            s.tick = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------

class TestJsonRoundTrip:
    def test_full_field_set(self):
        s = ControllerAttrSet(
            adapter="curobo",
            phase="planning",
            tick=42,
            status="stalled",
            last_error="timeout",
            profile="tabletop_pick",
        )
        payload = s.model_dump_json()
        loaded = ControllerAttrSet.model_validate_json(payload)
        assert loaded == s

    def test_minimal_field_set(self):
        s = ControllerAttrSet(adapter="builtin_pp", phase="approach")
        payload = s.model_dump_json()
        loaded = ControllerAttrSet.model_validate_json(payload)
        assert loaded == s

    def test_json_payload_shape(self):
        s = ControllerAttrSet(
            adapter="spline", phase="track", tick=5, last_error="off-path"
        )
        payload = json.loads(s.model_dump_json())
        # Frozen + serialised dict contains all model fields incl. None for profile.
        assert payload["adapter"] == "spline"
        assert payload["phase"] == "track"
        assert payload["tick"] == 5
        assert payload["status"] == "ok"
        assert payload["last_error"] == "off-path"
        assert payload["profile"] is None


# ---------------------------------------------------------------------------
# Token alias re-exports
# ---------------------------------------------------------------------------

class TestTokenAliases:
    def test_adapter_token_alias_exists(self):
        # The Literal type should be importable and round-trip-stringable.
        assert AdapterToken is not None
        assert StatusToken is not None


# ---------------------------------------------------------------------------
# Import-purity smoke test (fresh subprocess)
# ---------------------------------------------------------------------------

class TestImportPurity:
    """Phase 11c maintains the Phase 8c zero-internal-deps contract:
    importing ctrl_namespace must not pull in any other IA module."""

    def test_ctrl_namespace_has_no_internal_ia_deps(self):
        script = (
            "import sys, json\n"
            "import service.isaac_assist_service.types.ctrl_namespace as cn\n"
            "_ = cn.ControllerAttrSet, cn.AdapterToken, cn.StatusToken\n"
            "ia_loaded = sorted(\n"
            "    m for m in sys.modules\n"
            "    if m == 'service' or m.startswith('service.')\n"
            ")\n"
            "print(json.dumps(ia_loaded))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"ctrl_namespace import failed:\n"
            f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        )

        ia_loaded = json.loads(proc.stdout.strip().splitlines()[-1])

        # Only the importing module's path-prefix ancestors plus the
        # module itself should appear. We DO NOT import the types package
        # root from the script — but Python's import system loads parent
        # packages automatically, so `service`, `service.isaac_assist_service`,
        # and `service.isaac_assist_service.types` will materialise.
        allowed = {
            "service",
            "service.isaac_assist_service",
            "service.isaac_assist_service.types",
            "service.isaac_assist_service.types.ctrl_namespace",
            # Importing the types/ __init__.py also pulls in its sibling
            # submodules (spatial/uncertainty/provenance/violations) via
            # the package's re-export chain. Those are inside the same
            # zero-internal-deps boundary and so are allowed.
            "service.isaac_assist_service.types.spatial",
            "service.isaac_assist_service.types.uncertainty",
            "service.isaac_assist_service.types.provenance",
            "service.isaac_assist_service.types.violations",
        }
        forbidden = [m for m in ia_loaded if m not in allowed]
        assert forbidden == [], (
            "Importing service.isaac_assist_service.types.ctrl_namespace "
            "must not pull in any other IA module. Got forbidden imports: "
            f"{forbidden}"
        )
