"""Phase 0b — tests for the fork-divergence classifier.

Synthetic fixtures only — no real git invocation. The `classify()`
function is pure and easy to unit-test in isolation.
"""
import pytest

# Import via path manipulation since scripts/ isn't a package.
import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "audit_fork_divergence.py"


@pytest.fixture(scope="module")
def afd():
    spec = importlib.util.spec_from_file_location("audit_fork_divergence", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec_module — dataclasses uses
    # sys.modules.get(cls.__module__) during @dataclass processing and
    # raises AttributeError on None otherwise.
    sys.modules["audit_fork_divergence"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("audit_fork_divergence", None)


pytestmark = pytest.mark.l0


def test_only_spec_changes_classified_merged(afd):
    """A commit touching only specs/ is treated as already merged."""
    assert afd.classify("update spec", ["specs/IA_FULL_SPEC_2026-05-10.md"]) == "merged"
    assert (
        afd.classify("docs: clarify mandate", ["specs/foo.md", "specs/bar.md"])
        == "merged"
    )


def test_mixed_changes_with_code_not_merged(afd):
    """A commit touching specs/ AND code is NOT merged — needs review."""
    verdict = afd.classify(
        "feat: something", ["specs/x.md", "service/foo.py"]
    )
    assert verdict != "merged"


def test_adopt_keywords(afd):
    """Known IA-shaped extensions classify as adopt."""
    assert afd.classify("feat: deploy_rl_policy handler", ["service/foo.py"]) == "adopt"
    assert (
        afd.classify("Add preflight_check tool", ["service/x.py"]) == "adopt"
    )
    assert (
        afd.classify("isaac_ros_image_pipeline integration", ["foo.py"])
        == "adopt"
    )
    assert afd.classify("feat: RViz2 auto-launcher", ["foo.py"]) == "adopt"
    assert (
        afd.classify("multi-provider vision (Ollama + Gemini)", ["foo.py"])
        == "adopt"
    )


def test_defer_keywords(afd):
    """Vendor-specific or lower-priority work classifies as defer."""
    assert afd.classify("feat: lingbot ROS2 tools", ["x.py"]) == "defer"
    assert afd.classify("MediaPipe teleop wiring", ["x.py"]) == "defer"
    assert afd.classify("IRA actor control init", ["x.py"]) == "defer"
    assert afd.classify("cloud-LLM agent-swarm routing", ["x.py"]) == "defer"


def test_unknown_fallback(afd):
    """Subjects without keyword match fall through to unknown."""
    assert afd.classify("misc: random refactor", ["service/foo.py"]) == "unknown"
    assert afd.classify("bump version", ["package.json"]) == "unknown"


def test_case_insensitive_keyword_match(afd):
    """The classifier should not be confused by case differences in subjects."""
    assert afd.classify("Deploy_RL_Policy v2", ["x.py"]) == "adopt"
    assert afd.classify("LINGBOT updates", ["x.py"]) == "defer"


def test_empty_diff_files_falls_through(afd):
    """No files listed → cannot match merged; classifier proceeds to keyword rules."""
    assert afd.classify("deploy_rl_policy", []) == "adopt"
    assert afd.classify("misc", []) == "unknown"


def test_render_markdown_smoke(afd):
    """Smoke-test the report renderer against a tiny synthetic fixture."""
    commits = [
        afd.Commit(
            sha="aaaaaaaaaaaa",
            subject="feat: deploy_rl_policy handler",
            author="A",
            date="2026-05-01",
            files=["service/foo.py"],
            verdict="adopt",
        ),
        afd.Commit(
            sha="bbbbbbbbbbbb",
            subject="misc cleanup",
            author="B",
            date="2026-04-15",
            files=["x.py"],
            verdict="unknown",
        ),
        afd.Commit(
            sha="cccccccccccc",
            subject="docs: spec update",
            author="C",
            date="2026-04-10",
            files=["specs/x.md"],
            verdict="merged",
        ),
    ]
    out = afd.render_markdown(commits, "base/ref", "head/ref")
    assert "# Fork Divergence Audit" in out
    assert "**Base (working branch):** `base/ref`" in out
    assert "**Head (k3street fork):** `head/ref`" in out
    assert "**Total divergent commits:** 3" in out
    # Each verdict bucket present
    assert "## `adopt` (1)" in out
    assert "## `unknown` (1)" in out
    assert "## `merged` (1)" in out
    # sha shortened to 8 chars in table
    assert "`aaaaaaaa`" in out
    # hint included for known keyword
    assert "evaluate as Phase 79b sibling" in out
