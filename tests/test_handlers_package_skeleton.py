"""Phase 2 — smoke test for the themed handler package skeleton.

Asserts that every theme module imports cleanly and exposes a no-op
`register()` that accepts the (data, codegen) signature. Spec says:

  > Test: smoke test `from service.isaac_assist_service.chat.tools.handlers
  > import scene_authoring, physics, ...` does not raise.

These tests will start carrying real weight in Phase 3-7 as each
theme's `register()` populates the dispatch dicts. For now they're
pure shape checks.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# The 14 themed modules per IA_FULL_SPEC Phase 2 (+ _dispatch).
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
def test_themed_module_register_is_noop(name):
    """Phase 2 invariant: every theme's `register()` must be a no-op
    and must not mutate the dicts it receives. Phase 3-7 lifts this
    invariant theme-by-theme.
    """
    import importlib

    module = importlib.import_module(
        f"service.isaac_assist_service.chat.tools.handlers.{name}"
    )
    data: dict = {}
    codegen: dict = {}
    result = module.register(data, codegen)
    assert result is None, f"{name}.register should return None"
    assert data == {}, f"{name}.register modified data dict (should be no-op)"
    assert codegen == {}, f"{name}.register modified codegen dict (should be no-op)"


def test_dispatch_central_registry_imports():
    """`_dispatch.register_handlers` must be importable and callable."""
    from service.isaac_assist_service.chat.tools.handlers import _dispatch

    assert hasattr(_dispatch, "register_handlers")
    assert callable(_dispatch.register_handlers)


def test_dispatch_central_registry_is_noop():
    """At Phase 2, calling `register_handlers(data, codegen)` does
    nothing (every theme's `register()` is a no-op)."""
    from service.isaac_assist_service.chat.tools.handlers import _dispatch

    data: dict = {}
    codegen: dict = {}
    _dispatch.register_handlers(data, codegen)
    assert data == {}
    assert codegen == {}


def test_dispatch_iterates_all_themes():
    """The central registry must reference all 14 themed modules
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
