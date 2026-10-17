#!/usr/bin/env python3
"""The curricula that ask this pipeline for scenes, read into backlog tasks.

Sources, in the rank curriculum_bindings.json gives them:
  - HomeHero's occupation curricula: scene requests the Occupation Studio
    published here (workspace/knowledge/homehero_occupation_requests/<sha>/),
    the curriculum its handoff names, and the Studio's REVIEWED drafts (read
    over its local API when it runs); per occupation, the latest revision.
  - HomeHero's scenario queue (homehero_scenario_queue_v1.json).
  - HomeHero's shared interaction curriculum (its config/skill_pipeline).
  - Our own use ladders (asset_class_priors.json), for the rungs that need
    a physical interaction or a scene.

Each lesson needs, in order: its assets (sim-ready, in the library), the
physical interactions it asks for (physical_interactions.json: a law, its
test, a take that proves it), our planners for its actions, a scene, a
measure for each criterion, a passing take, and - where HomeHero asked for
one - an evidence handoff. Every requirement not met is a backlog task
('cur:<requirement>'), shared by the lessons it blocks, ranked by source,
lesson order and step; one met on a later run is closed with the evidence
found; one no curriculum needs any more is superseded. `next` names the task
to do first.

    curriculum_intake.py [--no-backlog] [--offline]     # derive, update the backlog, write the report
    curriculum_intake.py next                            # the next task
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KN = REPO / "workspace" / "knowledge"
HERE = Path(__file__).resolve().parent
STEPS = ("asset", "interaction", "planner", "scene", "measure", "take", "handoff")


def _load(p) -> dict | list | None:
    p = Path(p)
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _get(rec: dict, dotted: str):
    v = rec
    for k in dotted.split("."):
        if not isinstance(v, dict):
            return None
        v = v.get(k)
    return v


def _terms_in(text: str, terms) -> list[str]:
    t = text.lower()
    return [w for w in terms if re.search(r"(?<![a-z])" + re.escape(w.lower()) + r"(?![a-z])", t)]


# --- sources -> lessons ------------------------------------------------------

def _studio_reviewed(url: str, timeout: float = 3.0) -> list[dict]:
    """The Studio's reviewed drafts' plans (read-only GETs); [] when it is not running."""
    try:
        with urllib.request.urlopen(f"{url}/api/state", timeout=timeout) as r:
            state = json.loads(r.read())
    except Exception:
        return []
    out = []
    for d in state.get("drafts", []):
        if d.get("status") != "reviewed":
            continue
        try:
            with urllib.request.urlopen(f"{url}/api/drafts/{d['id']}", timeout=timeout) as r:
                det = json.loads(r.read())
        except Exception:
            continue
        if det.get("plan"):
            out.append({"plan": det["plan"], "origin": f"studio:{d['id']} (reviewed)", "at": float(d.get("reviewed_at") or d.get("created_at") or 0.0)})
    return out


def occupation_plans(b: dict, offline: bool = False) -> tuple[list[dict], list[dict]]:
    """(current, superseded): per occupation the latest published/handed-off/reviewed revision."""
    cands = []
    for f in sorted((REPO / b["sources"]["occupation_deliveries"]).glob("*/scene_request.json")):
        p = _load(f)
        if p:
            cands.append({"plan": p, "origin": f"delivered:{f.relative_to(REPO)}", "at": f.stat().st_mtime})
    for f in b["sources"].get("occupation_handoff_curricula", []):
        p = _load(f)
        if p:
            at = p.get("proposal_provenance", {}).get("created_at")
            ts = _dt.datetime.fromisoformat(at).timestamp() if at else Path(f).stat().st_mtime
            cands.append({"plan": p, "origin": f"handoff:{f}", "at": ts})
    if not offline:
        cands += _studio_reviewed(b["sources"]["studio"])
    by_sha = {}
    for c in cands:
        sha = c["plan"].get("curriculum_sha256")
        if sha not in by_sha or c["origin"].startswith("delivered"):
            by_sha[sha] = c
    # one occupation's revisions share its title: the Studio gives a revision drafted from an
    # imported curriculum an occupation id of its own (occupation_45d46bc54017 for the lab's
    # laboratory_materials_handler, 2026-10-17); its id is the earliest revision's
    first_id, groups = {}, {}
    for c in sorted(by_sha.values(), key=lambda c: c["at"]):
        p = c["plan"]
        title = (p.get("occupation_title") or "").strip().lower() or p.get("occupation_id") or (p.get("scene") or p.get("scene_request") or {}).get("id")
        c["occupation"] = first_id.setdefault(title, p.get("occupation_id") or title)
        groups.setdefault(title, []).append(c)
    current, superseded = [], []
    for occ, cs in groups.items():
        cs.sort(key=lambda c: c["at"])
        current.append(cs[-1])
        superseded += cs[:-1]
    return current, superseded


def lessons_from_occupation(c: dict, rank: int) -> list[dict]:
    plan, occ = c["plan"], c["occupation"]
    roles = {r["role"]: r for r in (plan.get("assets") or plan.get("asset_roles") or [])}
    scene = plan.get("scene_request") or plan.get("scene") or {}
    out = []
    for i, L in enumerate(plan.get("lessons", [])):
        tr = L.get("transfers") or []
        used = []
        for t in tr:
            for k in ("object", "source", "destination"):
                if t.get(k) and t[k] not in used:
                    used.append(t[k])
        out.append({
            "key": f"homehero.occupation.{occ}.{L['id']}", "kind": "homehero_occupation", "rank": rank,
            "order": L.get("order", i), "title": L.get("title", L["id"]), "family": L.get("family"),
            "text": " ".join([L.get("title", ""), L.get("trigger", ""), " ".join(L.get("capability_gaps", []))]),
            "roles": [dict(roles.get(r, {"role": r}), role=r) for r in used],
            "hands": max([len(t.get("hands") or []) for t in tr] or [1]),
            "moves_between_stations": any(t.get("source") != t.get("destination") for t in tr),
            "into_receptacle": any(roles.get(t.get("destination"), {}).get("requirements", {}).get("kind") == "receptacle" for t in tr),
            "criteria": list((L.get("criteria") or {}).keys()),
            # the Studio names a criterion it drafted from a capability gap 'additional_outcome_<k>':
            # its meaning is the k-th 'measurement_adapter_required:' gap
            "criteria_notes": {f"additional_outcome_{k + 1}": g.split(":", 1)[1].strip() for k, g in
                               enumerate(x for x in L.get("capability_gaps", []) if x.startswith("measurement_adapter_required:"))},
            "group": occ, "scene_request": {"id": scene.get("id"), "style": scene.get("style"), "locations": scene.get("locations")},
            "source": {"kind": "homehero_occupation", "origin": c["origin"], "curriculum_sha256": plan.get("curriculum_sha256"),
                       "title": plan.get("occupation_title") or occ},
            "handoff": True})
    return out


def lessons_from_queue(q: dict, rank: int) -> list[dict]:
    out = []
    for s in q.get("scenarios", []):
        out.append({"key": f"homehero.queue.{s['id']}", "kind": "homehero_scenario_queue", "rank": rank,
                    "order": int(s.get("priority", 9)), "title": s.get("title", s["id"]),
                    "text": " ".join([s.get("title", ""), s.get("first_deliverable", ""), " ".join(s.get("stages", []))]),
                    "asset": s.get("asset_id"), "hands": s.get("required_hands", 1), "parent": s["id"],
                    "source": {"kind": "homehero_scenario_queue", "origin": "homehero_scenario_queue_v1.json"}, "handoff": True})
    return out


def lessons_from_interaction_curriculum(c: dict, rank: int) -> list[dict]:
    out, n = [], 0
    for a in c.get("assets", []):
        for t in a.get("tasks", []):
            out.append({"key": f"homehero.interaction.{a['id']}.{t['id']}", "kind": "homehero_interaction", "rank": rank,
                        "order": n, "title": f"{a.get('label', a['id'])}: {t['id'].replace('_', ' ')}",
                        "text": " ".join([t.get("expected_response", ""), " ".join(t.get("skills", []))]),
                        "asset": a["id"], "asset_source": (a.get("source") or {}).get("path"),
                        "hands": t.get("required_hands", 1), "family": t.get("family"),
                        "prerequisites": [f"homehero.interaction.{a['id']}.{p}" for p in t.get("builds_on", [])],
                        "source": {"kind": "homehero_interaction", "origin": "aida_interaction_curriculum_v1.json"}})
            n += 1
    return out


def lessons_from_ladders(priors: dict, bindings: dict, rank: int) -> list[dict]:
    """The rungs a binding names (a rung's own take is the critic's task: only its interactions and scene are ours here)."""
    out = []
    for key in bindings.get("lessons", {}):
        m = re.match(r"nemotron\.ladder\.([a-z0-9_]+)\.r(\d+)$", key)
        if not m:
            continue
        cls, k = m.group(1), int(m.group(2))
        lad = ((priors.get(cls) or {}).get("use") or {}).get("ladder") or []
        if k < 1 or k > len(lad):
            continue
        out.append({"key": key, "kind": "nemotron_use_ladder", "rank": rank, "order": k, "title": f"{cls} rung {k}: {lad[k - 1]}",
                    "text": lad[k - 1], "asset": cls, "source": {"kind": "nemotron_use_ladder", "origin": "asset_class_priors.json"},
                    "no_take": True})
    return out


# --- checks --------------------------------------------------------------------

class Library:
    """Our sim-ready assets: the verified catalogue, the fixed library and the review queue's approvals."""

    def __init__(self):
        self.entries = []
        cat = _load(KN / "sim_ready_assets.json") or {}
        for a in cat.get("assets", []):
            self.entries.append({"asset_id": a["asset_id"], "status": a.get("category", ""), "text": a["asset_id"].replace("_", " "),
                                 "verified": str(a.get("category", "")).endswith("_verified")})
        for f in (REPO / "workspace" / "assets_fixed").glob("*_simready.usda"):
            aid = f.name[:-len("_simready.usda")]
            self.entries.append({"asset_id": aid, "status": "fixed (not reviewed)", "text": aid.replace("_", " "), "verified": False})
        for f in (REPO / "config" / "scenes").glob("*.json"):
            for o in (_load(f) or {}).get("objects", []):
                if o.get("asset_id"):
                    self.entries.append({"asset_id": o["asset_id"], "status": f"in scene {f.stem}",
                                         "text": " ".join(str(x) for x in (o["asset_id"].replace("_", " "), o.get("label"), o.get("class")) if x),
                                         "verified": True})
        for f in (REPO / "workspace" / "review_queue").glob("*.json"):
            r = _load(f) or {}
            if not r.get("asset_id"):
                continue
            vlm = r.get("vlm") or {}
            self.entries.append({"asset_id": r["asset_id"], "status": r.get("status", ""),
                                 "text": " ".join(str(x) for x in (r["asset_id"].replace("_", " "), r.get("class_hint"), r.get("name_class"),
                                                                    vlm.get("asset_class"), vlm.get("object_name")) if x),
                                 "verified": r.get("status") in ("approved", "promoted")})

    def find(self, terms) -> list[dict]:
        hits = {}
        for e in self.entries:
            if any(_terms_in(e["text"], [t]) for t in terms):
                h = hits.setdefault(e["asset_id"], dict(e))
                h["verified"] = h["verified"] or e["verified"]
        return sorted(hits.values(), key=lambda e: (not e["verified"], e["asset_id"]))


def _record(scene: str) -> dict | None:
    return _load(REPO / "workspace" / "scenes" / scene / "grasp" / "grasp.json")


def _proven(pb: dict | None) -> tuple[bool, str]:
    if not pb:
        return False, "no take named to prove it"
    rec = _record(pb["scene"])
    if rec is None:
        return False, f"{pb['scene']} has no take"
    v = _get(rec, pb["record"])
    return bool(v), f"{pb['scene']} {pb['record']} = {v}"


def check_interaction(entry: dict | None, iid: str) -> tuple[bool, str, str]:
    """(met, evidence, the work if not)."""
    if entry is None:
        return False, "not in physical_interactions.json", f"define the interaction '{iid}': its law (sourced), test and a take that proves it"
    if not entry.get("builtin"):
        if not entry.get("law"):
            return False, "no law", entry.get("needs") or f"write {iid}'s law"
        mod, fn = entry["law"].split(":")
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            getattr(importlib.import_module(mod), fn)
        except Exception as e:  # noqa: BLE001
            return False, f"law {entry['law']} does not import ({type(e).__name__})", f"fix {entry['law']}"
        if not entry.get("test") or not (REPO / entry["test"]).exists():
            return False, f"law {entry['law']} has no test", f"a test of {entry['law']} against its sources"
    ok, ev = _proven(entry.get("proven_by"))
    if not ok:
        scene = (entry.get("proven_by") or {}).get("scene")
        return False, ev, (f"a take in {scene} that passes {entry['proven_by']['record']}" if scene else f"a scene and take that prove {iid}")
    return True, ev, ""


def check_planner(entry: dict | None, pid: str) -> tuple[bool, str, str]:
    if entry is None:
        return False, "not in curriculum_bindings.json planners", f"define the planner capability '{pid}'"
    if not entry.get("module") or not (HERE / f"{entry['module']}.py").exists():
        return False, "no planner", entry.get("needs") or f"write the {pid} planner"
    ok, ev = _proven(entry.get("proven_by"))
    return ok, ev, "" if ok else f"a take proving {pid}"


def requirements(L: dict, b: dict, reg: dict, lib: Library) -> list[dict]:
    """The lesson's requirements in order, each {id, step, met, evidence, work, title}."""
    bind = b.get("lessons", {}).get(L["key"])
    via_terms = bind is None
    reqs = []

    def add(rid, step, title, met, ev, work, extra=None):
        reqs.append(dict({"id": rid, "step": step, "title": title, "met": bool(met), "evidence": ev, "work": work,
                          "matched_by_terms": via_terms and step in ("interaction", "planner")}, **(extra or {})))

    # assets
    if L["kind"] == "homehero_occupation":
        occ_b = (b.get("occupations") or {}).get(L["group"], {})
        for r in L["roles"]:
            rid = f"asset:{L['group']}:{r['role']}"
            bound = (occ_b.get("roles") or {}).get(r["role"])
            req = r.get("requirements") or {}
            if bound:
                add(rid, "asset", f"asset for {r['role']}", True, f"bound to {bound}", "")
                continue
            # the role's own terms, and its noun (the role id's last word: a 'sealed_inert_container' is a container)
            terms = list(dict.fromkeys(list(req.get("search_terms") or [r["role"].replace("_", " ")]) + [r["role"].split("_")[-1]]))
            found = lib.find(terms)
            ver = [e["asset_id"] for e in found if e["verified"]]
            if ver:
                work = (f"qualify one of {ver[:6]} for the role '{r['role']}' ({req.get('requirements', '')}): measured size, mass, "
                        f"friction, task preflight; then bind it in curriculum_bindings.json occupations.{L['group']}.roles")
                ev = f"{len(ver)} approved/verified candidates by {terms}, none bound"
            else:
                pend = [e["asset_id"] for e in found][:6]
                work = (f"source or generate a sim-ready '{r['role']}' ({req.get('kind', '')}: {req.get('requirements', '')}) - "
                        f"CAD Creator request or the asset pipeline" + (f"; pending review: {pend}" if pend else ""))
                ev = f"no approved asset by {terms}" + (f" ({len(found)} pending)" if found else "")
            add(rid, "asset", f"asset for the {r['role'].replace('_', ' ')} ({L['source']['title']})", False, ev, work,
                {"role_kind": req.get("kind"), "candidates": [e["asset_id"] for e in found][:10]})
    elif L.get("asset") and not (bind or {}).get("scene"):
        name = (bind or {}).get("asset") or re.sub(r"[_\d]+$", "", L["asset"])
        found = lib.find([name.replace("_", " ")])
        ver = [e["asset_id"] for e in found if e["verified"]]
        add(f"asset:{name}", "asset", f"a sim-ready {name.replace('_', ' ')}", bool(ver),
            f"verified/approved: {ver[:6]}" if ver else f"none approved ({len(found)} candidates)",
            "" if ver else f"bring a {name} through the asset pipeline (HomeHero's source: {L.get('asset_source')})",
            {"candidates": [e["asset_id"] for e in found][:10]})

    # interactions
    entries = {e["id"]: e for e in reg.get("interactions", [])}
    if bind is not None:
        iids = bind.get("interactions", [])
    else:
        iids = [e["id"] for e in reg.get("interactions", []) if _terms_in(L["text"], e.get("terms", []))]
        if L.get("family") == "pick_hold_place" and "rigid_contact" not in iids:
            iids.insert(0, "rigid_contact")
    if not iids:
        add(f"binding:{L['key']}", "interaction", f"map '{L['title']}' to its physical interactions", False,
            "no binding and no interaction's terms in its text", f"add {L['key']} to curriculum_bindings.json lessons")
    for iid in iids:
        met, ev, work = check_interaction(entries.get(iid), iid)
        add(f"interaction:{iid}", "interaction", f"the {iid.replace('_', ' ')} interaction", met, ev, work,
            {"interaction_kind": (entries.get(iid) or {}).get("kind")})

    # planners
    planners = b.get("planners", {})
    if bind is not None:
        pids = list(bind.get("planners", []))
    else:
        pids = []
        if L.get("family") == "pick_hold_place":
            pids.append("pinch_place")
        if L.get("hands", 1) >= 2:
            pids.append("bimanual_carry")
        if L.get("moves_between_stations"):
            pids.append("base_motion")
        if L.get("into_receptacle"):
            pids.append("place_inside")
        if not pids:
            add(f"binding:{L['key']}:planner", "planner", f"map '{L['title']}' to our planners", False,
                f"family {L.get('family')!r} has no planner mapping", f"add {L['key']} to curriculum_bindings.json lessons with its planners")
    for pid in pids:
        met, ev, work = check_planner(planners.get(pid), pid)
        add(f"planner:{pid}", "planner", f"the {pid.replace('_', ' ')} planner", met, ev, work)

    # scene
    if L["kind"] == "homehero_occupation":
        sc = ((b.get("occupations") or {}).get(L["group"]) or {}).get("scene")
        sr = L["scene_request"]
        add(f"scene:{L['group']}", "scene", f"the {L['source']['title']} scene ({sr.get('id')})", bool(sc and (REPO / 'config' / 'scenes' / f'{sc}.json').exists()),
            f"bound to {sc}" if sc else "none built", "" if sc else
            f"compose {sr.get('id')}: {sr.get('style')}; stations {sr.get('locations')}; native case resets per the curriculum's cases",
            {"scene_request": sr})
    else:
        sc = (bind or {}).get("scene")
        exists = bool(sc and (REPO / "config" / "scenes" / f"{sc}.json").exists())
        add(f"scene:{L['key']}", "scene", f"a scene for {L['title']}", exists, f"{sc}" if exists else (f"{sc} not built" if sc else "none named"),
            "" if exists else f"build a scene for '{L['title']}'" + (f" ({sc})" if sc else ""))

    # measures
    meas = b.get("measures", {})
    owned = set(meas.get("_homehero_owned", []))
    for crit in L.get("criteria", []):
        if crit in owned:
            continue
        note = (L.get("criteria_notes") or {}).get(crit)
        add(f"measure:{crit}" if not note else f"measure:{L['key']}:{crit}", "measure",
            f"measure '{crit}'" + (f" ({note})" if note else "") + " in the take's record", crit in meas,
            f"record field {meas[crit]}" if crit in meas else "no record field", "" if crit in meas else
            f"record '{crit}' in replay_grasp's result (and map it in curriculum_bindings.json measures)")

    # the take
    if not L.get("no_take"):
        sc = (bind or {}).get("scene") or ((b.get("occupations") or {}).get(L.get("group")) or {}).get("scene")
        field = (bind or {}).get("record") or "pass"
        rec = _record(sc) if sc else None
        v = _get(rec, field) if rec else None
        add(f"take:{L['key']}", "take", f"a passing take: {L['title']}", bool(v),
            f"{sc} {field} = {v}" if rec else "no take", "" if v else f"run and pass a take for '{L['title']}'" + (f" in {sc}" if sc else ""))

    # the handoff
    if L.get("handoff") or (bind or {}).get("handoff"):
        hid = L.get("parent") or L.get("group") or L["key"]
        d = REPO / "workspace" / "handoffs" / "homehero" / str(hid)
        ok = d.exists() and any(d.glob("*/manifest.json"))
        add(f"handoff:{hid}", "handoff", f"the evidence handoff for {hid}", ok, str(d) if ok else "no package",
            "" if ok else f"package {hid}'s evidence per HomeHero's queue: manifest, frozen cases, attempt ledger, measured states, critic findings, videos")
    return reqs


# --- backlog -------------------------------------------------------------------

def derive(offline: bool = False) -> dict:
    b = _load(KN / "curriculum_bindings.json")
    reg = _load(KN / "physical_interactions.json") or {}
    rank = b["source_rank"]
    lessons, info = [], {"superseded_revisions": []}
    cur, sup = occupation_plans(b, offline=offline)
    for c in cur:
        lessons += lessons_from_occupation(c, rank["homehero_occupation"])
    info["occupations"] = [{"occupation": c["occupation"], "origin": c["origin"], "curriculum_sha256": c["plan"].get("curriculum_sha256"),
                            "lessons": len(c["plan"].get("lessons", []))} for c in cur]
    info["superseded_revisions"] = [{"occupation": c["occupation"], "origin": c["origin"], "curriculum_sha256": c["plan"].get("curriculum_sha256")} for c in sup]
    q = _load(REPO / b["sources"]["scenario_queue"])
    if q:
        lessons += lessons_from_queue(q, rank["homehero_scenario_queue"])
    ic = _load(b["sources"]["interaction_curriculum"])
    if ic:
        lessons += lessons_from_interaction_curriculum(ic, rank["homehero_interaction"])
    pri = (_load(REPO / b["sources"]["use_ladders"]) or {}).get("classes", {})
    lessons += lessons_from_ladders(pri, b, rank["nemotron_use_ladder"])
    lib = Library()
    tasks = {}
    for L in lessons:
        reqs = requirements(L, b, reg, lib)
        L["requirements"] = reqs
        unmet = [r for r in reqs if not r["met"]]
        L["next"] = unmet[0]["id"] if unmet else None
        for j, r in enumerate(reqs):
            if r["met"]:
                continue
            pr = L["rank"] * 10000 + min(int(L["order"]), 99) * 100 + STEPS.index(r["step"]) * 10 + min(j, 9)
            t = tasks.setdefault(r["id"], {"id": "cur:" + r["id"], "kind": r["step"], "title": r["title"], "work": r["work"],
                                           "evidence": r["evidence"], "priority": pr, "blocks": [], "actionable": False,
                                           "matched_by_terms": r["matched_by_terms"], "sources": []})
            t["priority"] = min(t["priority"], pr)
            t["blocks"].append(L["key"])
            t["actionable"] = t["actionable"] or L["next"] == r["id"]
            if L["source"]["kind"] not in t["sources"]:
                t["sources"].append(L["source"]["kind"])
            if L.get("parent"):
                t.setdefault("parents", [])
                if L["parent"] not in t["parents"]:
                    t["parents"].append(L["parent"])
            for k in ("candidates", "role_kind", "interaction_kind", "scene_request"):
                if r.get(k) is not None:
                    t[k] = r[k]
    return {"lessons": lessons, "tasks": tasks, "info": info}


def update_backlog(backlog: dict, tasks: dict, met: dict, today: str) -> dict:
    """Upsert the curriculum's open tasks (by id); close the ones now met with
    the evidence, supersede the ones no lesson lists any more. Returns counts."""
    counts = {"added": 0, "updated": 0, "closed": 0, "superseded": 0}
    by_id = {t.get("id"): t for t in backlog["tasks"] if str(t.get("id", "")).startswith("cur:")}
    occ_parents = [t.get("id") for t in backlog["tasks"] if t.get("kind") == "homehero_occupation"]
    for rid, t in tasks.items():
        new = {"id": t["id"], "kind": t["kind"], "title": t["title"], "description": t["work"], "evidence": t["evidence"],
               "priority": t["priority"], "blocks": sorted(set(t["blocks"])), "actionable": t["actionable"], "from": "curriculum_intake",
               "curriculum_sources": t["sources"], **{k: t[k] for k in ("candidates", "role_kind", "interaction_kind", "parents") if k in t}}
        if "parents" not in new and "homehero_occupation" in t["sources"] and occ_parents:
            new["parents"] = occ_parents
        if t["matched_by_terms"]:
            new["confirm"] = "matched by the registries' terms, not a binding: confirm the mapping in curriculum_bindings.json"
        old = by_id.get(t["id"])
        if old is None:
            backlog["tasks"].append(dict(new, status="proposed", first_seen=today))
            counts["added"] += 1
        else:
            status = old.get("status") if old.get("status") in ("in_progress", "deferred") else "proposed"
            for k in ("closed_at", "closed_why", "confirm"):
                old.pop(k, None)
            old.update(new, status=status)
            counts["updated"] += 1
    for tid, old in by_id.items():
        rid = tid[4:]
        if rid in tasks or old.get("status") in ("done", "superseded"):
            continue
        if rid in met:
            old.update(status="done", closed_at=today, closed_why=f"met (curriculum_intake {today}): {met[rid]}")
            counts["closed"] += 1
        else:
            old.update(status="superseded", closed_at=today, closed_why=f"no current curriculum lesson needs it ({today})")
            counts["superseded"] += 1
    return counts


def met_requirements(lessons: list) -> dict:
    met = {}
    for L in lessons:
        for r in L["requirements"]:
            if r["met"]:
                met.setdefault(r["id"], r["evidence"])
    return met


def next_task(backlog: dict) -> dict | None:
    """The curriculum task to do first (one in progress before a proposed one, then by
    priority) - or, when it is blocked by an open task (blocked_by), that task: the
    drill's hole waits on a grip that can push the bit (2026-10-17)."""
    by_id = {t.get("id"): t for t in backlog["tasks"] if t.get("id")}
    is_open = lambda t: t is not None and t.get("status") in ("proposed", "in_progress")
    open_ = [t for t in backlog["tasks"] if str(t.get("id", "")).startswith("cur:") and is_open(t)]
    act = [t for t in open_ if t.get("actionable")] or open_
    if not act:
        return None
    t = min(act, key=lambda t: (t.get("status") != "in_progress", t.get("priority", 1e9)))
    seen = set()
    while t.get("id") not in seen:
        seen.add(t.get("id"))
        blocker = next((by_id.get(b) for b in t.get("blocked_by") or [] if is_open(by_id.get(b))), None)
        if blocker is None:
            break
        t = blocker
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=("run", "next"))
    ap.add_argument("--no-backlog", action="store_true", help="derive and report only")
    ap.add_argument("--offline", action="store_true", help="do not read the Studio's API")
    args = ap.parse_args()
    bp = KN / "task_backlog.json"
    backlog = json.loads(bp.read_text())
    if args.command == "next":
        t = next_task(backlog)
        print(json.dumps(t, indent=1) if t else "no open curriculum task")
        return 0
    res = derive(offline=args.offline)
    today = _dt.date.today().isoformat()
    report = {"at": _dt.datetime.now().isoformat(timespec="seconds"), **res["info"],
              "lessons": [{"key": L["key"], "title": L["title"], "source": L["source"], "next": L["next"],
                           "requirements": [{k: r[k] for k in ("id", "step", "met", "evidence")} for r in L["requirements"]]}
                          for L in res["lessons"]],
              "open": sorted(({"id": t["id"], "priority": t["priority"], "actionable": t["actionable"], "title": t["title"],
                               "blocks": len(t["blocks"])} for t in res["tasks"].values()), key=lambda x: x["priority"])}
    (KN / "curriculum_intake.json").write_text(json.dumps(report, indent=1))
    for L in res["lessons"]:
        n_met = sum(r["met"] for r in L["requirements"])
        print(f"{L['key'][:64]:64s} {n_met:2d}/{len(L['requirements']):2d} met  next: {L['next'] or '-'}")
    if not args.no_backlog:
        counts = update_backlog(backlog, res["tasks"], met_requirements(res["lessons"]), today)
        bp.write_text(json.dumps(backlog, indent=1))
        print(f"backlog: {counts}")
    t = next_task(backlog) if not args.no_backlog else min(res["tasks"].values(), key=lambda t: (not t["actionable"], t["priority"]), default=None)
    if t:
        print(f"NEXT {t['id']} (priority {t['priority']}): {t['title']} - {t.get('description') or t.get('work')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
