#!/usr/bin/env python3
"""Phase 17b — Mandate-guard CI scanner.

Walks files in IA's code roots (``service/``, ``exts/``, ``web/``,
``scripts/``) and tracked Markdown under ``docs/specs/`` looking for
tokens that belong to the *Robotics Lab* (RL) strategic-content layer.
IA must remain independent of RL strategy per the scope-discipline
clause of ``specs/IA_FULL_SPEC_2026-05-10.md`` (§ "Scope discipline",
lines 24-46).

Usage
-----
    # Scan specific files (typical pre-commit hook invocation)
    python scripts/lint_mandate.py path/to/file.py path/to/other.md

    # Scan all in-scope tracked files in the repo
    python scripts/lint_mandate.py

    # Help
    python scripts/lint_mandate.py --help

Allowlist
---------
A forbidden token may be retained on a line that carries a one-line
justification comment on the same line, in either of these forms::

    foo = ABOMState()  # allow-rl-term: porting wave 6 §3 shape only
    <!-- allow-rl-term: glossary cross-reference --> ABOM

Allow-rl-term comments are reviewed quarterly per Phase 96. Stale
entries (>90 days) raise a warning during that review.

Exit codes
----------
0 — no violations found.
1 — one or more violations found (details on stderr).
2 — usage error.

See also
--------
- ``docs/architecture/mandate_boundary.md`` — prose explanation,
  process for extending ``FORBIDDEN_TERMS``.
- ``tests/test_lint_mandate.py`` — fixture-based tests.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


# ---------------------------------------------------------------------------
# The mandate boundary
#
# Forbidden tokens originate from Robotics Lab. Each entry carries a
# one-line note explaining what RL concept it represents — this is the
# review surface when extending the set. Adding a new entry requires
# spec-level reviewer sign-off (see docs/architecture/mandate_boundary.md).
#
# NB: "MathCritic" is deliberately reused by IA's Phase 45 code-quality
# scorer and is therefore NOT in this set.
# ---------------------------------------------------------------------------
FORBIDDEN_TERMS: frozenset[str] = frozenset({
    # ABOM — RL's product-topology graph (Assemblies / Bills / Op Modes).
    "ABOM", "ABOMState", "ABOMNode", "ABOMEdge",
    # NSGA-II/III — RL's multi-objective evolutionary layout optimiser.
    "NSGA-II", "NSGA-III", "NSGA2", "nsga2",
    # make_or_buy — RL's procurement-vs-fabrication decision node.
    "make_or_buy", "make-or-buy", "MakeOrBuyDecision",
    # flip_point — RL's break-even volume in make-or-buy.
    "flip_point",
    # operating_mode — RL's ABOM operating-mode field.
    "operating_mode",
    # ScenarioEngine — RL's L3 strategic-brain causal-scenarios driver.
    "ScenarioEngine",
    # SiteProfile — RL's site-level macro-economic input bundle.
    "SiteProfile",
    # macro_env / Nord Pool / nord_pool — RL's macroeconomic data sources.
    "macro_env", "Nord Pool", "nord_pool",
    # MachinePosition — RL's layout-coordinate type (IA uses LayoutSpec).
    "MachinePosition",
    # fac_get_machine — RL's brain MCP tool name.
    "fac_get_machine",
    # WeightSpec — RL's frozen critic-weights container.
    "WeightSpec",
})


# Same-line allowlist marker — matches either Python/shell `#` comment
# or HTML-style `<!-- ... -->` comment on the same line as the term.
ALLOW_COMMENT_RE: re.Pattern[str] = re.compile(
    r"(?:#|<!--)\s*allow-rl-term:\s*\S",
)


# Code roots in IA's source tree that get scanned in full.
CODE_ROOTS: tuple[str, ...] = ("service", "exts", "web", "scripts")

# Documentation roots — only tracked *.md files are scanned here.
DOC_ROOTS: tuple[str, ...] = ("docs/specs",)

# Paths that must never be flagged, even if explicitly passed on the CLI.
# The spec file itself enumerates the forbidden terms in its scope-discipline
# clause; the scanner script holds them as data. Both are by design.
HARD_EXCLUDES: tuple[str, ...] = (
    "specs/IA_FULL_SPEC_2026-05-10.md",
    "scripts/lint_mandate.py",
)

# Agent-worktree noise — these live under .claude/worktrees/ and are not
# part of the real source tree.
WORKTREE_PREFIX: str = ".claude/worktrees/"


# Source-file extensions inside CODE_ROOTS that we bother scanning.
# Binary / build artefacts in those trees are ignored.
SOURCE_EXTS: frozenset[str] = frozenset({
    ".py", ".pyi", ".md", ".rst", ".txt",
    ".json", ".yaml", ".yml", ".toml",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".html", ".css",
    ".sh", ".bash",
    ".cfg", ".ini",
})


# ---------------------------------------------------------------------------
# Pattern compilation
# ---------------------------------------------------------------------------

def _compile_patterns(terms: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Compile each forbidden term to a word-boundaried regex.

    Forbidden terms come in mixed shapes: ``ABOMState`` (CamelCase),
    ``make_or_buy`` (snake), ``NSGA-II`` (hyphenated), ``Nord Pool``
    (spaced). A plain ``\\b`` won't do — ``re.escape("NSGA-II")`` would
    expose hyphen boundaries that ``\\b`` mishandles.

    We use explicit lookarounds asserting the character on either side is
    NOT a Python identifier character (``[A-Za-z0-9_]``). Hyphens and
    spaces are non-identifier so they are valid boundaries.

    Sorting longest-first means a line like ``ABOMState`` matches the
    ``ABOMState`` rule, not the shorter ``ABOM`` rule (which the strict
    trailing boundary would reject anyway).
    """
    sorted_terms = sorted(terms, key=len, reverse=True)
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for term in sorted_terms:
        pattern = (
            r"(?<![A-Za-z0-9_])"
            + re.escape(term)
            + r"(?![A-Za-z0-9_])"
        )
        compiled.append((term, re.compile(pattern)))
    return compiled


_COMPILED_PATTERNS: list[tuple[str, re.Pattern[str]]] = _compile_patterns(FORBIDDEN_TERMS)


# ---------------------------------------------------------------------------
# Violation record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Violation:
    """A single forbidden-term hit."""

    path: str
    line_no: int  # 1-based
    term: str
    line_text: str

    def render(self) -> str:
        """Format per the spec's error-message template."""
        return (
            f"ERROR: mandate violation at {self.path}:{self.line_no} — "
            f'term "{self.term}" appears\n'
            f"       in IA code. IA must not depend on RL strategic content.\n"
            f"       To allow this token, add a comment on the same line:\n"
            f"         # allow-rl-term: <one-line justification>\n"
            f"       and route the change through manual spec review.\n"
            f"       Line: {self.line_text.rstrip()}"
        )


# ---------------------------------------------------------------------------
# Path eligibility
# ---------------------------------------------------------------------------

def _normalise_rel(repo_root: Path, path: Path) -> str | None:
    """Return ``path`` as a POSIX-style repo-relative string, or None if
    it sits outside the repo.

    A relative ``path`` is interpreted against ``repo_root`` (not the
    process CWD) so the scanner behaves predictably when invoked from a
    different directory or in tests that pass synthetic repo roots.
    """
    resolved_root = repo_root.resolve()
    if path.is_absolute():
        candidate = path.resolve()
    else:
        candidate = (resolved_root / path).resolve()
    try:
        rel = candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return rel.as_posix()


def _is_in_scope(rel_path: str) -> bool:
    """Decide whether the scanner should look at this repo-relative path.

    Scope:
        - any file under one of the CODE_ROOTS (filtered by SOURCE_EXTS)
        - any tracked ``*.md`` under DOC_ROOTS

    Exclusions:
        - HARD_EXCLUDES (always skipped, even if explicitly passed)
        - anything under .claude/worktrees/ (agent scratch space)
    """
    if rel_path in HARD_EXCLUDES:
        return False
    if rel_path.startswith(WORKTREE_PREFIX):
        return False

    for root in CODE_ROOTS:
        prefix = root + "/"
        if rel_path.startswith(prefix) or rel_path == root:
            ext = Path(rel_path).suffix
            return ext in SOURCE_EXTS

    for root in DOC_ROOTS:
        prefix = root + "/"
        if (rel_path.startswith(prefix) or rel_path == root) and rel_path.endswith(".md"):
            return True

    return False


# ---------------------------------------------------------------------------
# Repository walking
# ---------------------------------------------------------------------------

def _git_tracked_files(repo_root: Path) -> list[str]:
    """Return tracked-file paths from ``git ls-files``.

    If git is unavailable or the directory is not a repository, falls
    back to a recursive disk walk under CODE_ROOTS + DOC_ROOTS so the
    scanner still works in CI containers that clone without history.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        files: list[str] = []
        for root in CODE_ROOTS + DOC_ROOTS:
            root_path = repo_root / root
            if not root_path.is_dir():
                continue
            for child in root_path.rglob("*"):
                if child.is_file():
                    rel = _normalise_rel(repo_root, child)
                    if rel is not None:
                        files.append(rel)
        return files

    return [line for line in out.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def scan_text(rel_path: str, text: str) -> list[Violation]:
    """Scan ``text`` for forbidden terms; return any violations found.

    Public API — used by the test suite to scan synthetic content
    without writing to disk.
    """
    violations: list[Violation] = []
    seen_spans_per_line: dict[int, list[tuple[int, int]]] = {}

    for line_no, line in enumerate(text.splitlines(), start=1):
        is_allowlisted = bool(ALLOW_COMMENT_RE.search(line))
        if is_allowlisted:
            continue
        for term, pattern in _COMPILED_PATTERNS:
            for match in pattern.finditer(line):
                # Skip overlapping spans (longer term already matched here).
                overlaps = seen_spans_per_line.setdefault(line_no, [])
                span = match.span()
                if any(
                    span[0] < existing_end and span[1] > existing_start
                    for existing_start, existing_end in overlaps
                ):
                    continue
                overlaps.append(span)
                violations.append(
                    Violation(
                        path=rel_path,
                        line_no=line_no,
                        term=term,
                        line_text=line,
                    )
                )
    return violations


def scan_file(repo_root: Path, rel_path: str) -> list[Violation]:
    """Read a single in-scope file and scan it."""
    file_path = repo_root / rel_path
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    return scan_text(rel_path, text)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_mandate.py",
        description=(
            "Mandate-guard scanner — enforces the RL/IA scope boundary "
            "from IA_FULL_SPEC_2026-05-10.md (Phase 17b)."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "Files to scan. Paths outside IA's code/doc scope are silently "
            "ignored. With no paths, scans every tracked in-scope file."
        ),
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Override repo root (defaults to git toplevel of cwd).",
    )
    return parser.parse_args(argv)


def _resolve_repo_root(arg_root: str | None) -> Path:
    if arg_root is not None:
        return Path(arg_root).resolve()
    # Walk up from this script: scripts/lint_mandate.py → repo root.
    return Path(__file__).resolve().parent.parent


def _collect_paths(
    repo_root: Path, raw_paths: Sequence[str],
) -> list[str]:
    """Map CLI arguments (or "scan everything") to in-scope rel-paths."""
    if not raw_paths:
        return [
            rel for rel in _git_tracked_files(repo_root)
            if _is_in_scope(rel)
        ]

    rel_paths: list[str] = []
    for raw in raw_paths:
        rel = _normalise_rel(repo_root, Path(raw))
        if rel is None:
            continue
        if _is_in_scope(rel):
            rel_paths.append(rel)
    return rel_paths


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    repo_root = _resolve_repo_root(args.repo_root)
    targets = _collect_paths(repo_root, args.paths)

    all_violations: List[Violation] = []
    for rel in targets:
        all_violations.extend(scan_file(repo_root, rel))

    if not all_violations:
        return 0

    for v in all_violations:
        print(v.render(), file=sys.stderr)
    print(
        f"\n{len(all_violations)} mandate violation(s) across "
        f"{len({v.path for v in all_violations})} file(s).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
