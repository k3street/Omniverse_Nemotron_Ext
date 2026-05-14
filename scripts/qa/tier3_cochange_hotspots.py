"""TI.2 — Co-change hotspot report.

Parses `git log --name-only` for the last N commits and computes:
- per-file: how often it changed
- per-pair: how often two files changed in the same commit (coupling)

High co-change pairs indicate hidden coupling that may warrant
refactoring. High individual change frequency indicates churn hotspots.

Usage: `python scripts/qa/tier3_cochange_hotspots.py [--commits=500]`

Output: JSON with top change-frequency files + top co-change pairs.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def get_commits(n: int) -> List[List[str]]:
    """Return list of files-changed per commit, newest first."""
    r = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", f"-n{n}", "--pretty=format:---COMMIT---", "--name-only"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        raise SystemExit(f"git log failed: {r.stderr}")
    commits: List[List[str]] = []
    current: List[str] = []
    for line in r.stdout.splitlines():
        if line == "---COMMIT---":
            if current:
                commits.append(current)
            current = []
        elif line.strip():
            # skip lockfiles and generated noise
            if any(ig in line for ig in [".lock", "_models.py", ".min.js"]):
                continue
            current.append(line.strip())
    if current:
        commits.append(current)
    return commits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commits", type=int, default=500)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--min-pair-count", type=int, default=3)
    args = parser.parse_args()

    commits = get_commits(args.commits)
    file_freq: Counter[str] = Counter()
    pair_freq: Counter[tuple] = Counter()

    for files in commits:
        unique_files = list(set(files))
        for f in unique_files:
            file_freq[f] += 1
        # skip enormous commits (refactor mega-commits skew pair stats)
        if len(unique_files) > 30:
            continue
        for a, b in combinations(sorted(unique_files), 2):
            pair_freq[(a, b)] += 1

    # Filter to Python files only for cleaner output
    py_files = {f: c for f, c in file_freq.items() if f.endswith(".py")}
    py_pairs = {
        p: c for p, c in pair_freq.items()
        if all(f.endswith(".py") for f in p) and c >= args.min_pair_count
    }

    out = {
        "commits_analysed": len(commits),
        "top_changed_files": dict(
            sorted(py_files.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
        ),
        "top_cochanged_pairs": [
            {"a": p[0], "b": p[1], "count": c}
            for p, c in sorted(py_pairs.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
        ],
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
