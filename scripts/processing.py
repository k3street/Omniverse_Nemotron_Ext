#!/usr/bin/env python3
"""The processing ledger: when each stage last ran on an asset, from which
code, under which version of its rules - so a fix can be re-applied to every
asset it touches, and only to those.

Each queue entry carries entry["processing"][stage] =
    {"at": ISO time, "code": git revision (+dirty), "rules": version, ...}
for the stages ingest, classify, file, articulate, behaviors, verify,
critic, soft.

RULES holds each stage's current version. When a fix changes what a stage
produces, bump its version here - by the date of the change - and every
asset processed under the older rules becomes stale for that stage (and
the stages after it). Articulation is versioned per drafting tier (TIERS):
a pliers fix re-drafts pliers, not drills. An entry from before the ledger
has no record: every applicable stage is stale.

stale(entry) says which stages to re-run and why; scripts/reprocess.py
re-runs them.
"""
from __future__ import annotations

import functools
import json
import subprocess
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# stage -> version of its rules (bump on a change to what it produces)
RULES = {
    "ingest": "2026-10-04",      # prim names keep only characters USD takes
    "classify": "2026-10-04",    # the VLM reports what the object does (functions)
    "file": "2026-10-04",        # wheelchair / power_wheelchair classes, Mobility
    "articulate": "2026-10-05c",  # see TIERS; set ingest bodies off above the links; press-fits (and what rides them) filtered
    "behaviors": "2026-10-05",   # motor, wheeled_base, gate; functions seen, not internal ones
    "verify": "2026-10-05",      # framed on the asset (reach only for swinging leaves); integrity
    "critic": "2026-10-05",      # integrity first; hinges far short of their intended range fail
    "soft": "2026-10-03",        # cloth proxy drape, squish, cable
    "rig": "2026-10-04",         # character rigs: detected, or a humanoid autorig + pose check
    "survey": "2026-10-05",      # numbers on each part's visible pixels; hidden parts stay still
    "materials": "2026-10-04",   # physics materials per part from the survey
}

# drafting tier -> version of its rules ("generic": the geometric proposal)
TIERS = {
    "pivot": "2026-10-04",        # halves, rivet patch, limits where the halves meet
    "clip": "2026-10-04",         # opens the end with room before the halves meet
    "power_drill": "2026-10-04",  # direction switch, motor behavior, spindle chuck, rocker filtered
    "rotors": "2026-10-04",       # rotors are spindles
    "buttons": "2026-10-03",      # keys on the deck they sit in; key split
    "generic": "2026-10-04",      # wheeled base: forks swivel, rims ride, casters
    "survey": "2026-10-05b",      # + housings hand motion to the leaf on them; pin on a face, recessed lids open away, housings held back; body = what parts move against; spin about the symmetry axis; slides out the near side; merged movers held back; sets
    "door": "2026-10-02", "watch": "2026-10-02", "turntable": "2026-10-02", "cabinet": "2026-10-02",
    "temples": "2026-10-02", "thread": "2026-10-02", "plunger": "2026-10-02",
}

ORDER = ["ingest", "classify", "survey", "file", "materials", "rig", "articulate", "behaviors", "verify",
         "critic", "soft"]


@functools.lru_cache(maxsize=1)
def code_rev() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                             text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "scripts", "service"],
                               cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()
        return rev + ("+dirty" if dirty else "") if rev else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def rules_for(stage: str, tier: str | None = None) -> str:
    if stage == "articulate" and tier:
        # the tier drafts, the shared apply step writes: a fix to either
        # (a set's ingest bodies taken off, press-fits filtered) re-runs it
        return f"{TIERS.get(tier, RULES['articulate'])}+{RULES['articulate']}"
    return RULES[stage]


def record(entry: dict, stage: str, **info) -> dict:
    """Note on the entry that `stage` ran now, under the current rules."""
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "code": code_rev(),
           "rules": rules_for(stage, info.get("tier")), **info}
    entry.setdefault("processing", {})[stage] = rec
    return rec


_PRIORS: dict = {}


def _prior(entry: dict) -> dict:
    cls = entry.get("class_hint") or (entry.get("report") or {}).get("matched_class")
    path = REPO / "workspace" / "knowledge" / "asset_class_priors.json"
    try:
        mtime = path.stat().st_mtime
        if _PRIORS.get("mtime") != mtime:
            _PRIORS.update(mtime=mtime, classes=json.loads(path.read_text())["classes"])
        return _PRIORS["classes"].get(cls or "", {})
    except (OSError, ValueError, KeyError):
        return {}


def articulated(entry: dict) -> bool:
    fx = [f for f in entry.get("applied_fixes", []) if f.startswith(("articulate_asset", "unarticulated"))]
    return bool(fx) and fx[-1].startswith("articulate_asset")


def applicable(entry: dict) -> list[str]:
    """The stages that apply to this asset."""
    prior = _prior(entry)
    stages = ["ingest", "classify", "file"]
    meshes = (entry.get("report") or {}).get("structure", {}).get("meshes", 0)
    cls = entry.get("class_hint") or (entry.get("report") or {}).get("matched_class")
    seen_moving = any(f.get("kind") in ("manual", "spring_return", "motor", "gate", "wheeled_base", "detachable")
                      for f in (entry.get("vlm") or {}).get("functions") or [])
    if (meshes >= 2 or seen_moving) and not (entry.get("deformable") or prior.get("deformable")) \
            and cls != "human_character":
        stages += ["survey", "materials"]
    if cls == "human_character" or (entry.get("report") or {}).get("skeleton"):
        stages.append("rig")
    if articulated(entry) or prior.get("mechanism_templates") or prior.get("behaviors") \
            or (prior.get("articulable") and not prior.get("deformable")):
        stages.append("articulate")
    if prior.get("behaviors") or (entry.get("vlm") or {}).get("functions"):
        stages.append("behaviors")
    if articulated(entry):
        stages += ["verify", "critic"]
    if entry.get("deformable") or prior.get("deformable"):
        stages.append("soft")
    return stages


def stale(entry: dict, stages: list[str] | None = None) -> list[tuple[str, str]]:
    """(stage, why) for each applicable stage that has not run under the
    current rules, among `stages` (all by default). A stage that will re-run
    makes the selected ones after it stale too; a stale stage that is NOT
    selected (re-classifying ~2900 assets is a VLM bill) does not cascade."""
    led = entry.get("processing") or {}
    out, upstream = [], None
    latest = ("", None)                       # the newest run of an earlier stage
    for st in ORDER:
        if st not in applicable(entry):
            continue
        selected = stages is None or st in stages
        rec = led.get(st)
        if rec is None:
            why = "not recorded (processed before the ledger)"
        else:
            want = rules_for(st, rec.get("tier"))
            why = f"rules {rec.get('rules')} -> {want}" if rec.get("rules") != want else None
        if why is None and upstream:
            why = f"after {upstream}"
        if why is None and rec and latest[1] and rec.get("at", "") < latest[0]:
            why = f"older than {latest[1]}"   # an earlier stage re-ran since (a re-articulated asset's verify)
        if rec and rec.get("at", "") > latest[0]:
            latest = (rec["at"], st)
        if why and selected:
            upstream = upstream or st
            out.append((st, why))
    return out
