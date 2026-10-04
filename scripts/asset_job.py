#!/usr/bin/env python3
"""Run a long asset job in the background and record it in a job file.

The asset tools (handlers/asset_conventions.py) start these and return a
job id at once; get_asset_job reads the file. A job is one of:

  verify <asset_id> [--no-critic] [--judge claude]
        animate the asset in PhysX (in the machine's single Isaac slot):
        every joint through its travel, every behavior's scenarios
        (behaviors.py), then the motion critic (motion_critic.py: a vision
        judge plus the measured over-travel check).
  downloads [--dry-run] [--delete-duplicates] [--animate] [--soft]
        process_downloads.sh: survey, stage, ingest, classify, file,
        articulate, behaviors check, verify.

    python scripts/asset_job.py <job_id> verify <asset_id> ...
The job file is workspace/asset_jobs/<job_id>.json:
    {"job_id", "kind", "args", "status": running|done|failed, "started",
     "finished", "log", "result"}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JOBS = REPO / "workspace" / "asset_jobs"
ISAAC_PYTHON = os.environ.get("ISAAC_PYTHON_SH",
                              str(Path.home() / "Documents/Github/isaacsim/_build/linux-aarch64/release/python.sh"))


def _write(job: dict) -> None:
    JOBS.mkdir(parents=True, exist_ok=True)
    tmp = JOBS / f".{job['job_id']}.json"
    tmp.write_text(json.dumps(job, indent=1, default=str))
    tmp.replace(JOBS / f"{job['job_id']}.json")


def _env() -> dict:
    env = dict(os.environ)
    dot = REPO / ".env"
    if dot.exists():
        for line in dot.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def verify(asset_id: str, critic: bool, judge: str, log) -> dict:
    summary = REPO / "workspace" / "asset_animations" / asset_id / "summary.json"
    # an earlier run's summary must not pass for this one's (a run that died
    # in Kit left the old file, and stale results were reported)
    summary.unlink(missing_ok=True)
    cmd = (f"source {REPO}/scripts/isaac_slot.sh >/dev/null; "
           f"timeout 1200 {ISAAC_PYTHON} {REPO}/scripts/animate_asset.py {asset_id} --seconds-per-joint 3")
    rc = subprocess.run(["bash", "-c", cmd], cwd=REPO, stdout=log, stderr=subprocess.STDOUT, env=_env()).returncode
    if not summary.exists():
        raise RuntimeError(f"the animation did not finish (exit {rc}; see the log)")
    s = json.loads(summary.read_text())

    def reached(v):
        if v.get("unlimited"):            # no stops: it turned a sweep each way
            return v["measured_range"][0] <= 0.9 * v["limits"][0] and v["measured_range"][1] >= 0.9 * v["limits"][1]
        far = v["limits"][1] if abs(v["limits"][1]) >= abs(v["limits"][0]) else v["limits"][0]
        got = v["measured_range"][1] if far > 0 else v["measured_range"][0]
        return abs(got - far) <= 0.1 * abs(far) + 1e-4

    joints = {k: {"limits": v["limits"], "measured": v["measured_range"], "reached": reached(v)}
              for k, v in s["joints"].items() if not v.get("follower")}
    out = {"joints": joints, "joints_reached": f"{sum(v['reached'] for v in joints.values())}/{len(joints)}",
           "behaviors": s.get("behaviors") or {}, "gates": s.get("gates") or {},
           "video": str(REPO / "workspace" / "asset_animations" / asset_id / f"{asset_id}.mp4")}
    runs = [r for v in out["behaviors"].values() for r in v.values()]
    if runs:
        out["behaviors_ok"] = f"{sum(1 for r in runs if r.get('ok'))}/{len(runs)}"
    sys.path.insert(0, str(REPO / "scripts"))
    from processing import record
    qf = REPO / "workspace" / "review_queue" / f"{asset_id}.json"
    e = json.loads(qf.read_text())
    record(e, "verify", joints_reached=out["joints_reached"], behaviors_ok=out.get("behaviors_ok"))
    qf.write_text(json.dumps(e, indent=1))
    if critic:
        sys.path.insert(0, str(REPO / "scripts"))
        os.environ.update({k: v for k, v in _env().items() if k not in os.environ})
        from motion_critic import critique
        r = critique(asset_id, judge)
        out["motion_critic"] = {"pass": r["pass"], "joints": {k: {"ok": v.get("motion_ok"), "problem": v.get("problem")}
                                                             for k, v in r["joints"].items()}}
    return out


def downloads(flags: list[str], log) -> dict:
    before = set((REPO / "workspace" / "review_queue" / "_runs").glob("downloads_*.json"))
    subprocess.run([str(REPO / "scripts" / "process_downloads.sh"), *flags], cwd=REPO, stdout=log,
                   stderr=subprocess.STDOUT, env=_env())
    after = sorted(set((REPO / "workspace" / "review_queue" / "_runs").glob("downloads_*.json")) - before)
    if not after:
        raise RuntimeError("no run report was written (see the log)")
    rep = json.loads(after[-1].read_text())
    kinds = {}
    for f in rep.get("files", []):
        kinds[f.get("kind")] = kinds.get(f.get("kind"), 0) + 1
    return {"report": str(after[-1]), "files": kinds, "assets": {
        a: {k: v.get(k) for k in ("class", "file", "articulation", "behaviors", "physx") if v.get(k)}
        for a, v in rep.get("assets", {}).items()}}


def reprocess(flags: list[str], log) -> dict:
    """scripts/reprocess.py with the OpenUSD environment process_downloads.sh sets."""
    env = _env()
    usd = env.get("USD_INSTALL", "/home/kimate/Documents/Github/openusd_build")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (f"{usd}/lib/python", str(REPO), env.get("PYTHONPATH", "")) if p)
    env["LD_LIBRARY_PATH"] = os.pathsep.join(p for p in (f"{usd}/lib", env.get("LD_LIBRARY_PATH", "")) if p)
    out = subprocess.run(["/usr/bin/python3", str(REPO / "scripts" / "reprocess.py"), *flags], cwd=REPO,
                         capture_output=True, text=True, env=env)
    log.write(out.stdout + out.stderr)
    rep = [ln[7:] for ln in out.stdout.splitlines() if ln.startswith("REPORT ")]
    if not rep:
        raise RuntimeError("reprocess wrote no report (see the log)")
    r = json.loads(Path(rep[-1]).read_text())
    return {"report": rep[-1], "stages": r.get("stages"), "dry_run": r.get("dry_run"),
            "assets": len(r.get("assets", {})), "summary": (out.stdout.splitlines() or [""])[0],
            "failed": {a: {s: v for s, v in d.items() if str(v).startswith("FAILED")}
                       for a, d in r.get("assets", {}).items()
                       if any(str(v).startswith("FAILED") for v in d.values())}}


def main() -> int:
    job_id, kind, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    job = {"job_id": job_id, "kind": kind, "args": rest, "status": "running", "started": time.time(),
           "log": str(JOBS / f"{job_id}.log"), "pid": os.getpid()}
    _write(job)
    with open(job["log"], "w") as log:
        try:
            if kind == "verify":
                job["result"] = verify(rest[0], "--no-critic" not in rest,
                                       rest[rest.index("--judge") + 1] if "--judge" in rest else "claude", log)
            elif kind == "downloads":
                job["result"] = downloads(rest, log)
            elif kind == "reprocess":
                job["result"] = reprocess(rest, log)
            else:
                raise ValueError(f"unknown job kind {kind!r}")
            job["status"] = "done"
        except Exception as ex:  # noqa: BLE001 - the job file carries the failure
            job["status"], job["error"] = "failed", str(ex)[:500]
    job["finished"] = time.time()
    _write(job)
    return 0 if job["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
