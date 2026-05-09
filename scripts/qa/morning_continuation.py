"""morning_continuation.py — extend autonomous work past 08:00.

Triggered by morning_watchdog.sh AFTER overnight_chain.py writes /tmp/overnight_chain.done.

Runs additional rounds of:
  Round A — re-run feasibility on all 86 (some changed verdict after Phase 2 fixes)
  Round B — re-run triage + action plan
  Round C — phase2_safe_execute --classes 2a,2b,2c (broader sweep)
  Round D — probe_ctrl_telemetry on every still-failing CP (data for 2d review)
  Round E — repeat A-C if budget remains

Bound by MORNING_BUDGET_S env var (default 14400 = 4h).

Independent from the main chain — uses overnight_chain.py imports so the
fixes/templates/baselines stay consistent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _log(msg: str, log_path: Path) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(log_path, "a") as f:
        f.write(line + "\n")


def _run(cmd, log_path: Path, timeout: int = 7200) -> int:
    _log(f"$ {' '.join(cmd)}", log_path)
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=timeout, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except subprocess.TimeoutExpired:
        _log(f"  TIMEOUT after {timeout}s", log_path)
        return -1
    if proc.stdout:
        with open(log_path, "a") as f:
            f.write(proc.stdout)
        # Tail-print for live visibility
        for line in (proc.stdout or "").splitlines()[-20:]:
            print(line, flush=True)
    if proc.stderr:
        with open(log_path, "a") as f:
            f.write(proc.stderr)
    return proc.returncode


def main() -> int:
    log_path = Path("/tmp/morning_continuation.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _log("=" * 78, log_path)
    _log("MORNING CONTINUATION START", log_path)
    _log("=" * 78, log_path)

    budget = int(os.environ.get("MORNING_BUDGET_S", "14400"))  # 4h
    max_rounds = int(os.environ.get("MORNING_MAX_ROUNDS", "3"))
    start = time.time()

    for round_i in range(1, max_rounds + 1):
        if time.time() - start > budget:
            _log(f"budget exhausted after round {round_i-1}", log_path)
            break
        _log(f"--- Morning round {round_i}/{max_rounds} ---", log_path)

        # A: re-feasibility (--update writes new verdicts over old)
        _log(f"round {round_i}: re-feasibility on all 86", log_path)
        _run([sys.executable, "-u", "scripts/qa/feasibility_baseline.py",
              "--update", "--per-cp-timeout", "120"],
             log_path, timeout=4800)

        # B: re-triage
        _log(f"round {round_i}: re-triage", log_path)
        _run([sys.executable, "-u", "scripts/qa/phase2_triage.py"],
             log_path, timeout=300)
        _run([sys.executable, "-u", "scripts/qa/phase2_action_plan.py"],
             log_path, timeout=300)

        # C: safe_execute broader sweep (2a/2b/2c — 2d still deferred)
        _log(f"round {round_i}: safe_execute --classes 2a,2b,2c", log_path)
        _run([sys.executable, "-u", "scripts/qa/phase2_safe_execute.py",
              "--classes", "2a,2b,2c",
              "--max-attempts", "20",
              "--total-budget", "5400"],  # 1.5h per round
             log_path, timeout=6000)

        # commit + push any incremental changes
        _run(["git", "add", "-A"], log_path, timeout=60)
        _run(["git", "commit", "-m",
              f"Morning continuation round {round_i} — additional fixes + re-feasibility"],
             log_path, timeout=60)
        _run(["git", "push", "anton", "feat/multimodal-foundation"],
             log_path, timeout=120)

    # D: telemetry probe over remaining failing CPs (data for human review)
    if time.time() - start < budget - 1800:  # leave 30 min for telemetry
        _log("telemetry probe on still-failing CPs", log_path)
        try:
            bl_path = REPO_ROOT / "workspace/baselines/2026-05-09-baseline.json"
            if bl_path.exists():
                bl = json.loads(bl_path.read_text())
                failing = [r["label"] for r in (bl.get("results") or [])
                            if r.get("status") in ("stable_fail", "flaky")][:30]
                for label in failing:
                    if time.time() - start > budget:
                        break
                    _run([sys.executable, "-u", "scripts/qa/probe_ctrl_telemetry.py",
                          label, "--duration", "30", "--json"],
                         log_path, timeout=120)
        except Exception as e:
            _log(f"telemetry probe error: {e}", log_path)

    elapsed = time.time() - start
    _log(f"MORNING CONTINUATION END elapsed={elapsed/60:.0f}min", log_path)
    Path("/tmp/morning_continuation.done").write_text(
        f"{datetime.now().isoformat(timespec='seconds')} elapsed={elapsed:.0f}s\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
