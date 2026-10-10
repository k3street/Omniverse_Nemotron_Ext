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
import re
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
    "articulate": "2026-10-09",  # + sets of articulable copies split into assets; links massed by volume; set bodies from the survey  # see TIERS; set bodies off; press-fits filtered; mirrored meshes unmirrored
    "behaviors": "2026-10-08",   # + chess sets: board grid, piece identities, starting FEN (chess_board)  # motor, wheeled_base (driven, or pushed on casters with forks; split wheels one body), gate; not internal ones
    "verify": "2026-10-09b",     # + a wide view of the whole run recorded beside the close-ups  # hand-sized tools held in a hand while driven  # chess sets: settle on their squares, 1. e4 e5 2. Nf3 carried and read back; close-up per joint; integrity of every part
    "critic": "2026-10-09b",     # + whole-run view from wide frames only, an unsure fault counts, a still root overrides "it moved"  # the judge states a correction and a failed joint is re-drafted on it once; pins hinged about and whole-tool uses are not missing joints; whole video cropped to the object, an unsure fault noted not counted  # four-frame close-ups, unseen is not judged, completeness; judge errors never prune
    "soft": "2026-10-07",        # cloth proxy drape, squish, cable; mixed bodies: per-part deformables, drop test
    "rig": "2026-10-04",         # character rigs: detected, or a humanoid autorig + pose check
    "survey": "2026-10-06",      # moulded-in movers split and surveyed again; unmirrored, normals blocked; manual; pivot_toward
    "approve": "2026-10-06",     # evidence gates, then a visual critic; machine approvals withdrawn when they stop passing
    "materials": "2026-10-06",   # physics materials per part from the survey; the class's on any bare collider
}

# drafting tier -> version of its rules ("generic": the geometric proposal)
TIERS = {
    "pivot": "2026-10-04",        # halves, rivet patch, limits where the halves meet
    "clip": "2026-10-04",         # opens the end with room before the halves meet
    "power_drill": "2026-10-04",  # direction switch, motor behavior, spindle chuck, rocker filtered
    "rotors": "2026-10-04",       # rotors are spindles
    "buttons": "2026-10-03",      # keys on the deck they sit in; key split
    "generic": "2026-10-04",      # wheeled base: forks swivel, rims ride, casters
    "survey": "2026-10-09",       # + hinges about the pin that joins them; fasteners are small; shafts spin about their length; rack-driven parts follow the worm (couple); the critic's corrections honoured  # wheels round, forks not (swapped by shape); body a substantial still part; riders on what they touch; long parts pinned at an end, edges not faces; edge hinges: axis from geometry (leaf plane, beam horizontal); housings by enclosure; forks swivel; copies jointed each; pin toward a named part; parents touch their parts; housings hand motion to the leaf on them; pin on a face, recessed lids open away, housings held back; body = what parts move against; spin about the symmetry axis; slides out the near side; merged movers held back; sets
    "door": "2026-10-02", "watch": "2026-10-02", "turntable": "2026-10-02", "cabinet": "2026-10-02",
    "temples": "2026-10-02", "thread": "2026-10-02", "plunger": "2026-10-02",
}

ORDER = ["ingest", "classify", "survey", "file", "materials", "rig", "articulate", "behaviors", "verify",
         "critic", "soft", "approve"]


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


SOFT_MATERIALS = {"fabric_cotton", "foam_polyurethane", "leather", "paper_kraft"}
DECAL = re.compile(r"\b(decal|label|sticker|marking|print(ed|ing)?|pad|felt|logo|tag)\b", re.I)


def soft_parts(entry: dict) -> dict:
    """The soft parts of an asset the class prior calls rigid: what the
    classifier read as deformable (a shoe's knit upper, dropped because the
    shoe class is not a deformable class) and the parts the survey said flex
    or made of a soft material (laces, fur, a ribbon, a cushion). A shoe is a
    rubber sole with a soft upper: neither a rigid body nor one cloth.
    {"kind": cloth|sponge|rope|None, "parts": [roles], "whole": bool}."""
    if entry.get("deformable"):
        return {"kind": entry["deformable"], "parts": [], "accessories": [], "whole": True}
    vlm = entry.get("vlm") or {}
    kind = vlm.get("deformable_type")
    parts, accessories = [], []
    prior = _prior(entry)
    if prior.get("soft_parts"):
        # the class knows (a shoe is always a soft upper on a sole), whether or
        # not this asset was surveyed or the classifier said so
        kind = kind or prior.get("soft_kind", "cloth")
        parts += list(prior["soft_parts"])
    survey_parts = (entry.get("part_survey") or {}).get("parts") or []
    big = max([float((entry.get("report") or {}).get("max_dim_m") or 0.0)]
              + [max(p.get("size_m") or [0.0]) for p in survey_parts])
    for p in survey_parts:
        role = p.get("role") or p["path"].split("/")[-1]
        size = p.get("size_m") or [0.0, 0.0, 0.0]
        if p.get("material") in SOFT_MATERIALS:
            # a felt pad under a chess piece, a paper decal, a label: soft in
            # material, nothing in the simulation (tiny beside the object, or
            # flat on another part)
            tiny = big > 0 and max(size) < 0.05 * big
            flat = min(size) < 0.001 and DECAL.search(role)
            if tiny or flat:
                accessories.append(role)
                continue
            parts.append(role)                   # a soft body: an upper, a cushion, a strap
        elif p.get("motion") == "flex":
            # a cable, a cord, a hose on a rigid object: wrong as a rigid rod,
            # but not what makes the object soft; recorded, not a block
            accessories.append(role)
    if not kind and parts:
        kind = "cloth"
    return {"kind": kind, "parts": parts, "accessories": accessories, "whole": False}


def owned(path: str) -> bool:
    """Whether a file is ours to write: a derivative under workspace/ (the
    assets_fixed copies, the library). A downloaded or vendor source never is."""
    import tempfile

    for base in (REPO / "workspace", Path(tempfile.gettempdir())):   # and scratch files (tests)
        try:
            Path(path).resolve().relative_to(base.resolve())
            return True
        except ValueError:
            pass
    return False


def articulated(entry: dict) -> bool:
    fx = [f for f in entry.get("applied_fixes", []) if f.startswith(("articulate_asset", "unarticulated"))]
    return bool(fx) and fx[-1].startswith("articulate_asset")


def applicable(entry: dict) -> list[str]:
    """The stages that apply to this asset."""
    if entry.get("split_into"):
        return []                             # split into one asset per object: those carry on, not this
    prior = _prior(entry)
    stages = ["ingest", "classify", "file"]
    meshes = (entry.get("report") or {}).get("structure", {}).get("meshes", 0)
    cls = entry.get("class_hint") or (entry.get("report") or {}).get("matched_class")
    seen_moving = any(f.get("kind") in ("manual", "spring_return", "motor", "gate", "wheeled_base", "detachable")
                      for f in (entry.get("vlm") or {}).get("functions") or [])
    soft_or_person = entry.get("deformable") or prior.get("deformable") or cls == "human_character"
    if (meshes >= 2 or seen_moving) and not soft_or_person:
        stages += ["survey", "materials"]
    elif not soft_or_person:
        stages.append("materials")            # a physics material on every collider, at least the class's
    if cls == "human_character" or (entry.get("report") or {}).get("skeleton"):
        stages.append("rig")
    if articulated(entry) or prior.get("mechanism_templates") or prior.get("behaviors") \
            or (prior.get("articulable") and not prior.get("deformable")) or prior.get("multi_body"):
        stages.append("articulate")           # a set of free bodies: its bodies follow the survey here
    if prior.get("behaviors") or (entry.get("vlm") or {}).get("functions") or cls == "chess_set":
        stages.append("behaviors")            # a chess set's board and pieces are its behaviour
    if articulated(entry):
        stages += ["verify", "critic"]
    elif prior.get("game"):
        stages.append("verify")               # a set is played in PhysX (verify_chess_set)
    if entry.get("deformable") or prior.get("deformable") or soft_parts(entry)["kind"]:
        stages.append("soft")                 # a whole soft body, or soft parts on a rigid one
    stages.append("approve")                  # last: on everything the stages before measured
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
            if why is None and rec.get("incomplete"):
                why = "incomplete (a judge could not be reached)"
            if why is None and rec.get("undone"):
                # taken off (unarticulate) and not put back: no current result
                why = f"undone ({str(rec['undone'])[:60]})"
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
