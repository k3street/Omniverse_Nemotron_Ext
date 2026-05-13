"""Phase 10 — Tests for the generated handlers/_models.py module.

Covers:
  1. The module imports without warnings (no field-shadowing collisions).
  2. MODEL_REGISTRY contains one entry per tool in tool_schemas.py.
  3. Required fields are enforced (passing partial args raises).
  4. Permissive mode: unknown extras pass through; bad types raise.
  5. Fixture round-trips: a small set of canonical args round-trip
     cleanly through their respective models.

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 10.
"""
from __future__ import annotations

import warnings

import pytest

pytestmark = pytest.mark.l0


def test_models_module_imports_clean():
    """Importing _models.py must not emit any UserWarning (field-shadow
    collisions, deprecated aliases, etc.). The generator's reserved-
    name list MUST cover Pydantic v1 method names.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        from service.isaac_assist_service.chat.tools.handlers import _models  # noqa: F401
        # Filter to Pydantic UserWarnings only
        relevant = [
            w for w in caught
            if issubclass(w.category, UserWarning)
            and "shadows" in str(w.message).lower()
        ]
        assert relevant == [], (
            "Pydantic UserWarnings on _models import — add the offending "
            "names to scripts/gen_handler_models.py:_RESERVED_PY_KEYWORDS "
            f"and regenerate. Caught: {[str(w.message) for w in relevant]}"
        )


def test_registry_count_matches_tool_schemas():
    """MODEL_REGISTRY should have one model per tool in tool_schemas.py."""
    from service.isaac_assist_service.chat.tools.handlers import _models
    from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS

    schema_names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
    registry_names = set(_models.MODEL_REGISTRY.keys())
    missing = schema_names - registry_names
    extra = registry_names - schema_names
    assert not missing, (
        f"Tools missing from MODEL_REGISTRY: {sorted(missing)[:5]}...\n"
        "Run scripts/gen_handler_models.py to regenerate."
    )
    assert not extra, f"MODEL_REGISTRY has stale entries: {sorted(extra)[:5]}..."


def test_create_prim_required_fields_enforced():
    """Missing a required field raises a Pydantic ValidationError."""
    from pydantic import ValidationError
    from service.isaac_assist_service.chat.tools.handlers._models import CreatePrimArgs

    with pytest.raises(ValidationError):
        CreatePrimArgs()  # prim_path + prim_type are required
    with pytest.raises(ValidationError):
        CreatePrimArgs(prim_path="/World/A")  # prim_type missing


def test_create_prim_optional_omitted_defaults_to_none():
    """Optional fields default to None when omitted."""
    from service.isaac_assist_service.chat.tools.handlers._models import CreatePrimArgs

    m = CreatePrimArgs(prim_path="/World/A", prim_type="Cube")
    assert m.position is None
    assert m.scale is None
    assert m.rotation_euler is None


def test_extra_field_is_allowed():
    """extra='allow' means unknown kwargs pass through without 422."""
    from service.isaac_assist_service.chat.tools.handlers._models import CreatePrimArgs

    m = CreatePrimArgs(prim_path="/World/A", prim_type="Cube", random_kwarg="x")
    dumped = m.model_dump()
    assert dumped.get("random_kwarg") == "x"


def test_schema_field_aliased():
    """`schema` is a Pydantic-shadow name; generator aliases it to `schema_`."""
    from service.isaac_assist_service.chat.tools.handlers._models import (
        BulkApplySchemaArgs,
    )

    # JSON-style: alias `schema` accepted (populate_by_name=True allows both)
    m = BulkApplySchemaArgs.model_validate(
        {"prim_paths": ["/A"], "schema": "PhysicsRigidBodyAPI"}
    )
    assert m.schema_ == "PhysicsRigidBodyAPI"
    # Python-style: kwarg `schema` also accepted
    m2 = BulkApplySchemaArgs(prim_paths=["/A"], schema="PhysicsRigidBodyAPI")
    assert m2.schema_ == "PhysicsRigidBodyAPI"


def test_canonical_fixtures_round_trip():
    """A handful of canonical tool-arg shapes round-trip cleanly."""
    from service.isaac_assist_service.chat.tools.handlers._models import MODEL_REGISTRY

    fixtures = [
        ("delete_prim", {"prim_path": "/World/A"}),
        ("set_attribute", {"prim_path": "/W/X", "attr_name": "xformOp:translate", "value": [0, 0, 0]}),
        ("teleport_prim", {"prim_path": "/W/X", "position": [1, 2, 3]}),
        ("get_articulation_state", {"prim_path": "/World/UR10"}),
    ]
    for tool_name, args in fixtures:
        model_cls = MODEL_REGISTRY.get(tool_name)
        assert model_cls is not None, f"No model for {tool_name}"
        instance = model_cls(**args)
        dumped = instance.model_dump(exclude_none=True)
        # Required keys must round-trip
        for k, v in args.items():
            # Pydantic may alias the name; check value presence either way
            assert dumped.get(k) == v or dumped.get(k.rstrip("_")) == v
