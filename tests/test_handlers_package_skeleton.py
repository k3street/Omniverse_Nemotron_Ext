"""Phase 2 + Phase 9 — handler package skeleton + dispatch contract.

Phase 2 (initial) asserted that every theme's `register()` was a no-op
shape placeholder. Phase 9 (2026-05-13) flipped that — `register()` now
populates the dispatch dicts.

These tests assert the post-Phase-9 contract:
  * every theme module imports cleanly
  * every theme module exposes a `register(data, codegen)` callable
  * calling `register_handlers(data, codegen)` populates both dicts
    with at least one entry across the suite
  * `_THEME_MODULES` references every themed module file in the package

Pre-Phase-9 versions of these tests asserted is_noop semantics; that
contract was retired with the dispatch swap.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# The 18 themed modules per IA_FULL_SPEC Phase 2 + Phase 9 additions.
# Phase 9 added: animation, pick_place, rendering.
# CRM-A2 added: compliance.
THEMED_MODULES = (
    "scene_authoring",
    "physics",
    "robot",
    "sensors",
    "sdg",
    "training",
    "ros2",
    "teleop",
    "scene_blueprints",
    "diagnostics",
    "arena",
    "workflow",
    "resolve",
    "vision",
    "animation",
    "pick_place",
    "rendering",
    "compliance",
    "contact_sequence",
)


@pytest.mark.parametrize("name", THEMED_MODULES)
def test_themed_module_imports(name):
    """Each themed module imports without error."""
    import importlib

    module = importlib.import_module(
        f"service.isaac_assist_service.chat.tools.handlers.{name}"
    )
    assert module is not None


@pytest.mark.parametrize("name", THEMED_MODULES)
def test_themed_module_has_register(name):
    """Each themed module exposes a `register(data, codegen)` callable."""
    import importlib

    module = importlib.import_module(
        f"service.isaac_assist_service.chat.tools.handlers.{name}"
    )
    assert hasattr(module, "register"), f"{name}.register is missing"
    assert callable(module.register), f"{name}.register is not callable"


@pytest.mark.parametrize("name", THEMED_MODULES)
def test_themed_module_register_returns_none(name):
    """`register()` returns None and mutates the passed dicts in-place."""
    import importlib

    module = importlib.import_module(
        f"service.isaac_assist_service.chat.tools.handlers.{name}"
    )
    data: dict = {}
    codegen: dict = {}
    result = module.register(data, codegen)
    assert result is None, f"{name}.register should return None"
    # Phase 9: register() populates dispatch (data and/or codegen).
    # A module is allowed to register only one side or the other,
    # but the union across all themes must be non-empty (asserted by
    # the central-registry test below).


def test_dispatch_central_registry_imports():
    """`_dispatch.register_handlers` must be importable and callable."""
    from service.isaac_assist_service.chat.tools.handlers import _dispatch

    assert hasattr(_dispatch, "register_handlers")
    assert callable(_dispatch.register_handlers)


def test_dispatch_central_registry_populates():
    """Phase 9 contract: `register_handlers(data, codegen)` populates
    both dispatch dicts with the union of every theme module's entries
    plus external registrators (multimodal / diagnose / bridges / ros2).
    """
    from service.isaac_assist_service.chat.tools.handlers import _dispatch

    data: dict = {}
    codegen: dict = {}
    _dispatch.register_handlers(data, codegen)
    # Floor thresholds — these grow over time. They exist to catch
    # the regression where Phase 9 silently reverts to a no-op.
    assert len(data) >= 100, (
        f"register_handlers should populate ≥100 data entries, got {len(data)}"
    )
    assert len(codegen) >= 100, (
        f"register_handlers should populate ≥100 codegen entries, got {len(codegen)}"
    )


def test_dispatch_iterates_all_themes():
    """The central registry must reference every themed module
    (so a forgotten theme can't silently miss registration later)."""
    from service.isaac_assist_service.chat.tools.handlers import _dispatch

    referenced = {mod.__name__.split(".")[-1] for mod in _dispatch._THEME_MODULES}
    expected = set(THEMED_MODULES)
    missing = expected - referenced
    extra = referenced - expected
    assert not missing, f"_dispatch._THEME_MODULES missing: {missing}"
    assert not extra, f"_dispatch._THEME_MODULES has unexpected entries: {extra}"


def test_tools_init_re_exports_handlers():
    """The tools package re-exports the handlers subpackage."""
    from service.isaac_assist_service.chat import tools

    assert hasattr(tools, "handlers")
    # Spot-check one themed module is reachable through the re-export
    assert hasattr(tools.handlers, "scene_authoring")
    assert hasattr(tools.handlers, "_dispatch")
