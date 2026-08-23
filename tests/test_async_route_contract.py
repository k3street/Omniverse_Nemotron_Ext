"""FastAPI handlers must not enter Isaac Python's broken worker-thread bridge."""
from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0
SERVICE_ROOT = Path(__file__).parents[1] / "service" / "isaac_assist_service"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _is_http_route(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in HTTP_METHODS
    )


def test_all_fastapi_route_handlers_are_native_async():
    synchronous_routes = []
    for path in SERVICE_ROOT.rglob("*.py"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and any(
                _is_http_route(decorator) for decorator in node.decorator_list
            ):
                synchronous_routes.append(f"{path.relative_to(SERVICE_ROOT)}:{node.lineno}:{node.name}")
    assert synchronous_routes == [], (
        "Synchronous FastAPI handlers use AnyIO worker threads, which hang in "
        f"Isaac Sim's bundled Python: {synchronous_routes}"
    )
