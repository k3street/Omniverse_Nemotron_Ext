"""Phase 17b — tests for the mandate-guard scanner.

Verifies that ``scripts/lint_mandate.py`` enforces the RL/IA scope
boundary from ``specs/IA_FULL_SPEC_2026-05-10.md`` (§ "Scope discipline",
Phase 17b).

The scanner is imported as a module via ``importlib`` because
``scripts/`` is not a package. All fixtures are synthetic strings or
``tmp_path`` files — the test suite never depends on the real repo.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).parent.parent / "scripts" / "lint_mandate.py"
)


@pytest.fixture(scope="module")
def lint_mod() -> Any:
    """Load ``scripts/lint_mandate.py`` as a module."""
    spec = importlib.util.spec_from_file_location(
        "lint_mandate", _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["lint_mandate"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# scan_text — pure-string API (no disk)
# ---------------------------------------------------------------------------

class TestScanText:
    """Tests for the in-memory ``scan_text`` helper."""

    def test_clean_file_passes(self, lint_mod):
        """A file with no forbidden tokens produces zero violations."""
        text = (
            "import os\n"
            "def hello() -> str:\n"
            '    return "world"\n'
        )
        result = lint_mod.scan_text("fixtures/clean.py", text)
        assert result == []

    def test_abomstate_import_fails(self, lint_mod):
        """Acceptance #1: ``from rl_lib import ABOMState`` triggers a
        mandate violation naming ABOMState."""
        text = "from rl_lib import ABOMState\n"
        result = lint_mod.scan_text("fixtures/bad_import.py", text)
        assert len(result) == 1
        v = result[0]
        assert v.term == "ABOMState"
        assert v.line_no == 1
        rendered = v.render()
        assert "ERROR: mandate violation" in rendered
        assert "ABOMState" in rendered
        assert "IA must not depend on RL strategic content" in rendered
        assert "allow-rl-term:" in rendered

    def test_bare_abom_token_is_flagged(self, lint_mod):
        """The plain ``ABOM`` token (no ``State``/``Node``/``Edge`` suffix)
        is its own forbidden entry; the error message names ``ABOM``."""
        text = 'doc = """ABOM is RL\'s product topology."""\n'
        result = lint_mod.scan_text("fixtures/bare_abom.py", text)
        assert len(result) == 1
        assert result[0].term == "ABOM"
        assert 'term "ABOM"' in result[0].render()

    def test_allow_rl_term_comment_permits_token(self, lint_mod):
        """Acceptance #2: a forbidden token preceded by
        ``# allow-rl-term: <reason>`` on the same line is allowed."""
        text = (
            "x = 1  # allow-rl-term: porting wave 6 §3 shape only ABOM\n"
        )
        result = lint_mod.scan_text("fixtures/allowed.py", text)
        assert result == []

    def test_allow_rl_term_comment_after_token_also_permits(self, lint_mod):
        """The marker need only appear on the same line — order does
        not matter (the spec just says "same line")."""
        text = "ABOM  # allow-rl-term: documented exception\n"
        result = lint_mod.scan_text("fixtures/allowed_trailing.py", text)
        assert result == []

    def test_html_comment_allowlist_for_markdown(self, lint_mod):
        """Markdown contexts can use ``<!-- allow-rl-term: ... -->``."""
        text = (
            "Reference to ABOM here. "
            "<!-- allow-rl-term: glossary cross-link -->\n"
        )
        result = lint_mod.scan_text("docs/specs/foo.md", text)
        assert result == []

    def test_allow_comment_only_covers_its_own_line(self, lint_mod):
        """An allowlist comment on line N must not protect line N+1."""
        text = (
            "first = ABOM  # allow-rl-term: legitimate cross-ref\n"
            "second = ABOMState\n"
        )
        result = lint_mod.scan_text("fixtures/two_lines.py", text)
        assert len(result) == 1
        assert result[0].line_no == 2
        assert result[0].term == "ABOMState"

    def test_overlapping_term_reported_once(self, lint_mod):
        """``ABOMState`` matches the long entry; the bare ``ABOM`` rule
        has word-boundary lookarounds and must NOT double-fire."""
        text = "x = ABOMState()\n"
        result = lint_mod.scan_text("fixtures/long_token.py", text)
        assert len(result) == 1
        assert result[0].term == "ABOMState"

    def test_substring_in_larger_identifier_is_not_flagged(self, lint_mod):
        """``ABOMState`` would NOT match inside ``MyABOMStateX`` — the
        word-boundary lookarounds protect against incidental substring
        matches in unrelated IA identifiers."""
        text = "class MyABOMStateX:\n    pass\n"
        result = lint_mod.scan_text("fixtures/embedded.py", text)
        assert result == []

    def test_hyphenated_term_matches(self, lint_mod):
        """Hyphenated tokens like ``NSGA-II`` must be detected."""
        text = "# layout uses NSGA-II optimisation\n"
        result = lint_mod.scan_text("fixtures/hyphen.py", text)
        assert any(v.term == "NSGA-II" for v in result)

    def test_spaced_term_matches(self, lint_mod):
        """Multi-word tokens like ``Nord Pool`` must be detected."""
        text = "price = fetch('Nord Pool')\n"
        result = lint_mod.scan_text("fixtures/spaced.py", text)
        assert any(v.term == "Nord Pool" for v in result)

    def test_render_message_format(self, lint_mod):
        """The error string follows the spec's exact template:
        ``ERROR: mandate violation at <path>:<line> — term "X" appears``
        plus the multi-line continuation about routing through review."""
        text = 'value = ABOM\n'
        result = lint_mod.scan_text(
            "service/isaac_assist_service/multimodal/types.py", text,
        )
        assert len(result) == 1
        rendered = result[0].render()
        assert rendered.startswith(
            "ERROR: mandate violation at "
            "service/isaac_assist_service/multimodal/types.py:1 — "
            'term "ABOM" appears'
        )
        assert "IA must not depend on RL strategic content" in rendered
        assert "manual spec review" in rendered

    def test_mathcritic_is_not_flagged(self, lint_mod):
        """``MathCritic`` is IA's Phase 45 code-quality scorer; the spec
        explicitly carves it out of the forbidden set."""
        text = "from isaac_assist.scoring import MathCritic\n"
        result = lint_mod.scan_text("service/scoring.py", text)
        assert result == []


# ---------------------------------------------------------------------------
# Scope filtering — _is_in_scope
# ---------------------------------------------------------------------------

class TestIsInScope:
    """Tests for the path-eligibility predicate."""

    def test_service_python_file_is_in_scope(self, lint_mod):
        assert lint_mod._is_in_scope(
            "service/isaac_assist_service/main.py",
        )

    def test_scripts_python_file_is_in_scope(self, lint_mod):
        assert lint_mod._is_in_scope("scripts/audit_tools.py")

    def test_web_typescript_file_is_in_scope(self, lint_mod):
        assert lint_mod._is_in_scope("web/src/App.tsx")

    def test_exts_python_file_is_in_scope(self, lint_mod):
        assert lint_mod._is_in_scope(
            "exts/isaac_5.1/omni.isaac.assist/foo.py",
        )

    def test_docs_specs_markdown_is_in_scope(self, lint_mod):
        assert lint_mod._is_in_scope(
            "docs/specs/2026-05-11-stack-evaluation-spec.md",
        )

    def test_docs_specs_non_markdown_is_out_of_scope(self, lint_mod):
        """Phase 17b limits docs scanning to *.md only."""
        assert not lint_mod._is_in_scope(
            "docs/specs/some_notes.txt",
        )

    def test_full_spec_is_excluded(self, lint_mod):
        """Acceptance #3: the full-spec file itself enumerates the
        forbidden terms in its scope-discipline clause and must never
        be flagged."""
        assert not lint_mod._is_in_scope(
            "specs/IA_FULL_SPEC_2026-05-10.md",
        )

    def test_scanner_script_is_excluded(self, lint_mod):
        """Acceptance #4: the scanner's own source holds the
        FORBIDDEN_TERMS list as data — it must never flag itself."""
        assert not lint_mod._is_in_scope("scripts/lint_mandate.py")

    def test_agent_worktree_is_excluded(self, lint_mod):
        """The .claude/worktrees/ tree holds throwaway agent worktrees,
        not real source. Skipping keeps full-tree scans fast and
        avoids re-flagging duplicates."""
        assert not lint_mod._is_in_scope(
            ".claude/worktrees/agent-foo/service/main.py",
        )

    def test_arbitrary_root_is_out_of_scope(self, lint_mod):
        """Files outside CODE_ROOTS and DOC_ROOTS are silently skipped."""
        assert not lint_mod._is_in_scope("README.md")
        assert not lint_mod._is_in_scope("data/foo.json")
        assert not lint_mod._is_in_scope("tests/test_lint_mandate.py")


# ---------------------------------------------------------------------------
# scan_file + CLI integration via tmp_path
# ---------------------------------------------------------------------------

class TestScanFile:
    """Tests that touch the filesystem via ``tmp_path``."""

    def test_scan_file_reads_disk(self, lint_mod, tmp_path):
        """``scan_file`` resolves repo-relative paths against repo_root
        and returns violations."""
        (tmp_path / "service").mkdir()
        target = tmp_path / "service" / "bad.py"
        target.write_text("from rl_lib import ABOMState\n")
        result = lint_mod.scan_file(tmp_path, "service/bad.py")
        assert len(result) == 1
        assert result[0].term == "ABOMState"

    def test_scan_file_clean(self, lint_mod, tmp_path):
        (tmp_path / "service").mkdir()
        target = tmp_path / "service" / "good.py"
        target.write_text("print('hello world')\n")
        assert lint_mod.scan_file(tmp_path, "service/good.py") == []

    def test_scan_file_missing_returns_empty(self, lint_mod, tmp_path):
        """Missing or unreadable files yield no violations (the file
        list is the source of truth, not an existence check)."""
        assert lint_mod.scan_file(tmp_path, "service/missing.py") == []


class TestMainCli:
    """Integration tests against the ``main()`` entry point."""

    def test_main_clean_returns_zero(self, lint_mod, tmp_path, capsys):
        (tmp_path / "service").mkdir()
        clean = tmp_path / "service" / "clean.py"
        clean.write_text("x = 1\n")
        rc = lint_mod.main([
            "--repo-root", str(tmp_path),
            "service/clean.py",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_main_violation_returns_one_with_error(
        self, lint_mod, tmp_path, capsys,
    ):
        """Acceptance #1 end-to-end: feeding the CLI a file with
        ``ABOMState`` exits 1 and writes the spec-format error to
        stderr."""
        (tmp_path / "service").mkdir()
        bad = tmp_path / "service" / "bad.py"
        bad.write_text("from rl_lib import ABOMState\n")
        rc = lint_mod.main([
            "--repo-root", str(tmp_path),
            "service/bad.py",
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "ERROR: mandate violation" in err
        assert "ABOMState" in err
        assert "manual spec review" in err

    def test_main_skips_out_of_scope_args(
        self, lint_mod, tmp_path, capsys,
    ):
        """Passing an out-of-scope path explicitly is silently ignored
        (the pre-commit hook will hand the scanner whatever files
        changed; we shouldn't fail on files we don't own)."""
        bad = tmp_path / "README.md"  # not in DOC_ROOTS
        bad.write_text("This document mentions ABOMState somewhere.\n")
        rc = lint_mod.main([
            "--repo-root", str(tmp_path),
            "README.md",
        ])
        assert rc == 0

    def test_main_refuses_to_flag_full_spec_path(
        self, lint_mod, tmp_path, capsys,
    ):
        """Acceptance #3 end-to-end: explicitly passing the full-spec
        path on the CLI still yields exit 0 — the hard-exclude wins."""
        (tmp_path / "specs").mkdir()
        spec = tmp_path / "specs" / "IA_FULL_SPEC_2026-05-10.md"
        spec.write_text(
            "## Scope\nThis spec mentions ABOM and NSGA-II by name.\n",
        )
        rc = lint_mod.main([
            "--repo-root", str(tmp_path),
            "specs/IA_FULL_SPEC_2026-05-10.md",
        ])
        assert rc == 0

    def test_main_refuses_to_flag_scanner_itself(
        self, lint_mod, tmp_path,
    ):
        """Acceptance #4 end-to-end: the scanner's own path is
        hard-excluded even when its content trivially mentions the
        terms (as data)."""
        (tmp_path / "scripts").mkdir()
        scanner = tmp_path / "scripts" / "lint_mandate.py"
        scanner.write_text(
            'FORBIDDEN = {"ABOM", "ABOMState", "NSGA-II"}\n',
        )
        rc = lint_mod.main([
            "--repo-root", str(tmp_path),
            "scripts/lint_mandate.py",
        ])
        assert rc == 0

    def test_real_scanner_passes_self_scan(self, lint_mod):
        """Sanity: pointing the live scanner at ``scripts/lint_mandate.py``
        in the real repo must return zero (the file is hard-excluded)."""
        repo_root = Path(__file__).resolve().parent.parent
        rc = lint_mod.main([
            "--repo-root", str(repo_root),
            "scripts/lint_mandate.py",
        ])
        assert rc == 0

    def test_real_scanner_skips_full_spec(self, lint_mod):
        """Sanity: same self-scan check, this time on the spec file."""
        repo_root = Path(__file__).resolve().parent.parent
        rc = lint_mod.main([
            "--repo-root", str(repo_root),
            "specs/IA_FULL_SPEC_2026-05-10.md",
        ])
        assert rc == 0


# ---------------------------------------------------------------------------
# Forbidden-set contract
# ---------------------------------------------------------------------------

class TestForbiddenSetContract:
    """Lock down the forbidden-set spec so accidental edits trip a test."""

    def test_canonical_terms_present(self, lint_mod):
        """All spec-listed terms (verbatim) must be in the set."""
        expected = {
            "ABOM", "ABOMState", "ABOMNode", "ABOMEdge",
            "NSGA-II", "NSGA-III", "NSGA2", "nsga2",
            "make_or_buy", "make-or-buy", "MakeOrBuyDecision",
            "flip_point", "operating_mode", "ScenarioEngine",
            "SiteProfile", "macro_env", "Nord Pool", "nord_pool",
            "MachinePosition", "fac_get_machine", "WeightSpec",
        }
        assert expected.issubset(set(lint_mod.FORBIDDEN_TERMS))

    def test_mathcritic_is_not_forbidden(self, lint_mod):
        """IA's Phase 45 code-quality scorer reuses the MathCritic
        name deliberately. Adding it to the forbidden set would
        break Phase 45 acceptance — keep this test as a guardrail."""
        assert "MathCritic" not in lint_mod.FORBIDDEN_TERMS
