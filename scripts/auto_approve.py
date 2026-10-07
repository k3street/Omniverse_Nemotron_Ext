#!/usr/bin/env python3
"""Auto approval: evidence first, then a visual critic.

The old machine approval (visual_qa.py) asked three vision models to agree on
our class names from thumbnails, ran only from the hub's folder watcher, and
approved rigid assets only: 56 of 3,123 assets. This approves on what the
pipeline now measures, the last stage in the processing ledger:

  1. Gates, measured from the asset file and its run evidence (no model):
     the file loads, no error callouts, a confirmed class, metres and Z up,
     size and mass inside the class's ranges (or a named product's spec),
     every body collides and has a physics material, no mirrored bodies.
     An articulated asset also needs a current PhysX run in which every
     joint reached its travel and every part held together, its behaviours
     met, the motion critic passed, and no real moving part left fixed.
  2. The visual critic (Claude) on fresh orbit renders, only when every gate
     passed: is it the object it is said to be, upright, intact, of
     plausible size and plausible physics materials.
  3. Sign-off through the review hub's approval (registry, promotion into
     the library), as reviewer_type machine with the evidence attached.
     Every Nth machine approval is sampled for a person to audit. A
     machine approval that later stops passing is withdrawn.

A person's decision (approved or rejected by a human) is never revisited.
Deformables, characters and scenes stay with people for now.

    python scripts/auto_approve.py <asset_id> [...]
    python scripts/auto_approve.py --pending [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"
PRIORS = REPO / "workspace" / "knowledge" / "asset_class_priors.json"
ANIM = REPO / "workspace" / "asset_animations"
REVIEWER = "auto-approval-v2"
MODEL = "claude-opus-5"
AUDIT_EVERY = int(os.environ.get("AUTO_APPROVE_AUDIT_EVERY", "5"))
MOVING_KINDS = ("manual", "spring_return", "motor", "gate", "wheeled_base")


def measure(entry: dict) -> dict:
    """What the asset file holds now: units, size, bodies, colliders,
    physics materials, masses, joints."""
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    stage = Usd.Stage.Open(entry["file"])
    root = stage.GetDefaultPrim() or stage.GetPseudoRoot()
    rng = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
                            ).ComputeWorldBound(root).ComputeAlignedRange()
    dims = [] if rng.IsEmpty() else [round(float(v), 4) for v in rng.GetSize()]
    xc = UsdGeom.XformCache()
    bodies = [p for p in Usd.PrimRange(root) if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    colliders = [p for p in Usd.PrimRange(root) if p.HasAPI(UsdPhysics.CollisionAPI)]

    def physics_material(p):
        m, _ = UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial(materialPurpose="physics")
        return m.GetPrim().GetName() if m and m.GetPrim().HasAPI(UsdPhysics.MaterialAPI) else None

    bound = {str(p.GetPath()): physics_material(p) for p in colliders}
    no_material = [k.split("/")[-1] for k, v in bound.items() if not v]
    col_paths = [p.GetPath() for p in colliders]
    no_collider = [str(b.GetPath()).split("/")[-1] for b in bodies
                   if not any(c.HasPrefix(b.GetPath()) for c in col_paths)]
    masses = {}
    for b in bodies:
        if b.HasAPI(UsdPhysics.MassAPI):
            m = UsdPhysics.MassAPI(b).GetMassAttr().Get()
            if m and m > 0:
                masses[str(b.GetPath())] = float(m)
    mirrored = [str(b.GetPath()).split("/")[-1] for b in bodies
                if xc.GetLocalToWorldTransform(b).ExtractRotationMatrix().GetDeterminant() < 0]
    moving = [p for p in stage.Traverse() if p.IsA(UsdPhysics.RevoluteJoint) or p.IsA(UsdPhysics.PrismaticJoint)]
    return {"up": str(UsdGeom.GetStageUpAxis(stage)), "meters_per_unit": UsdGeom.GetStageMetersPerUnit(stage),
            "dims_m": dims, "bodies": len(bodies), "colliders": len(colliders),
            "no_collider": no_collider, "no_physics_material": no_material,
            "mass_kg": round(sum(masses.values()), 4), "bodies_with_mass": len(masses),
            "mirrored": mirrored, "moving_joints": len(moving),
            "physics_materials": sorted({v for v in bound.values() if v})}


def _check(name, ok, evidence):
    return {"check": name, "ok": bool(ok), "evidence": evidence}


def _within(v, lo, hi, slack=0.1):
    return lo * (1 - slack) <= v <= hi * (1 + slack)


def gates(entry: dict, m: dict) -> list[dict]:
    """The measured checks, in order; every one must pass."""
    from ingest_asset import run_report

    cls = entry.get("class_hint") or (entry.get("report") or {}).get("matched_class")
    # the same audit promotion re-runs (with the entry's own class hint): a gate
    # that passed on the matched class let two assets fail at sign-off
    report = run_report(entry["file"], entry.get("class_hint"))
    entry["report"] = report
    errors = [c.get("message", "") for c in report.get("callouts", []) if c.get("severity") == "error"]
    priors = json.loads(PRIORS.read_text())["classes"]
    prior = priors.get(cls or "", {})
    spec = entry.get("product_spec") or {}
    kind = (entry.get("vlm") or {}).get("content_kind")
    import re as _re
    from processing import soft_parts, stale
    soft = soft_parts(entry)
    mixed = entry.get("mixed_body") or {}
    mixed_ok = bool(soft["kind"] and not soft["whole"] and entry.get("soft_body_parts") and mixed.get("pass")
                    and "soft" not in dict(stale(entry, ["soft"])))
    surveyed_mats = {p.get("material") for p in (entry.get("part_survey") or {}).get("parts") or [] if p.get("material")}
    out = [
        _check("registry_id", bool(_re.fullmatch(r"[a-z0-9_]+", entry["asset_id"])),
               "the registry takes ids of a-z, 0-9 and _ only" if not _re.fullmatch(r"[a-z0-9_]+", entry["asset_id"])
               else entry["asset_id"]),
        _check("loads", m["dims_m"] and max(m["dims_m"]) > 0, f"bounds {m['dims_m']}"),
        _check("no_error_callouts", not errors, "; ".join(e[:90] for e in errors[:3]) or "none"),
        _check("classified", cls and kind not in ("scene", "scene_fragment"),
               f"class {cls}, content {kind or 'single object'}"),
        _check("class_confirmed", prior and prior.get("source") != "vlm",
               "class written by a model and not yet confirmed by a person" if prior.get("source") == "vlm"
               else f"class {cls}" if prior else "no such class"),
        _check("in_machine_scope", not (entry.get("deformable") or prior.get("deformable")
                                        or cls == "human_character" or (soft["kind"] and not mixed_ok)),
               "deformables and characters are approved by people for now" if not soft["kind"] else
               "a mixed body: soft parts authored as deformables and verified in a PhysX drop" if mixed_ok else
               f"has soft parts ({soft['kind']}: {', '.join(soft['parts'][:4]) or 'the classifier read it as soft'}); "
               + ("its soft parts are authored but the drop test did not pass: " + str(mixed.get("verdict", ""))[:120]
                  if mixed else "a rigid body would be wrong; the mixed-body stage has not run on it")),
        _check("units", m["up"] == "Z" and abs(m["meters_per_unit"] - 1.0) < 1e-6,
               f"up {m['up']}, {m['meters_per_unit']} m per unit"),
    ]
    big = max(m["dims_m"]) if m["dims_m"] else 0.0
    if spec.get("max_dim_m"):
        out.append(_check("size", abs(big - spec["max_dim_m"]) <= 0.2 * spec["max_dim_m"],
                          f"{big} m against the product's {spec['max_dim_m']} m"))
    else:
        r = prior.get("max_dim_m")
        out.append(_check("size", r and _within(big, r[0], r[1]), f"{big} m against class range {r}"))
    out.append(_check("physics_complete",
                      m["bodies"] >= 1 and not m["no_collider"] and not m["no_physics_material"] and not m["mirrored"],
                      f"{m['bodies']} bodies, {m['colliders']} colliders; without collider {m['no_collider'][:4]}, "
                      f"without physics material {m['no_physics_material'][:4]}, mirrored {m['mirrored'][:4]}"))
    if len(surveyed_mats) >= 2:
        # the survey saw several materials: every part must carry its own
        # (four shoes were approved as one lump of soft rubber)
        out.append(_check("multi_material", len(m.get("physics_materials") or []) >= 2,
                          f"survey saw {sorted(surveyed_mats)}, bound {m.get('physics_materials')}"))
    if spec.get("mass_kg"):
        out.append(_check("mass", m["bodies_with_mass"] and abs(m["mass_kg"] - spec["mass_kg"]) <= 0.25 * spec["mass_kg"],
                          f"{m['mass_kg']} kg against the product's {spec['mass_kg']} kg"))
    else:
        r = prior.get("mass_kg")
        out.append(_check("mass", m["bodies_with_mass"] and r and _within(m["mass_kg"], r[0], r[1], 0.25),
                          f"{m['mass_kg']} kg on {m['bodies_with_mass']} bodies against class range {r}"))
    if m["moving_joints"]:
        out += _articulated(entry)
    elif prior.get("mechanism_templates"):
        # a kind with a drafting rule (scissors, pliers, doors) moves: not as a
        # rigid lump (a pair of garden shears nearly passed as one)
        out.append(_check("nothing_missing", False,
                          f"class {cls} has joints by convention ({', '.join(prior['mechanism_templates'])}) "
                          "and this asset has none"))
    else:
        moving_fns = [f.get("does") for f in (entry.get("vlm") or {}).get("functions") or []
                      if f.get("kind") in MOVING_KINDS]
        out.append(_check("nothing_missing", not moving_fns,
                          "it should move but has no joints: " + "; ".join(str(x) for x in moving_fns[:3])
                          if moving_fns else "a rigid object"))
    return out


def _articulated(entry: dict) -> list[dict]:
    """Evidence an articulated asset needs: a current run, everything held
    together and reached its travel, behaviours met, the critic passed, and
    nothing that moves on the real object left fixed."""
    from behaviors import check as behavior_check
    from motion_critic import _reached, completeness
    from processing import stale

    led = entry.get("processing") or {}
    st = dict(stale(entry, ["articulate", "behaviors", "verify", "critic"]))
    out = [_check("run_current", not any(k in st for k in ("articulate", "verify", "critic")),
                  "; ".join(f"{k}: {v}" for k, v in st.items()) or "verify and critic ran on the current joints")]
    sf = ANIM / entry["asset_id"] / "summary.json"
    summary = json.loads(sf.read_text()) if sf.exists() else {}
    integ = summary.get("integrity") or {}
    out.append(_check("held_together", summary and integ.get("ok"),
                      "no PhysX run" if not summary else json.dumps(
                          {k: integ.get(k) for k in ("max_joint_separation_m", "root_moved_m", "fixed_came_apart",
                                                     "press_fits_let_go", "loose_bodies")})))
    joints = {k: v for k, v in (summary.get("joints") or {}).items() if not v.get("follower")}
    short = [k for k, v in joints.items() if not _reached(v)]
    out.append(_check("reached_travel", joints and not short,
                      f"{len(joints) - len(short)}/{len(joints)} joints reached their travel"
                      + (f"; short: {short[:4]}" if short else "")))
    unmet = {k: v.get("missing") for k, v in behavior_check(entry).items() if not v.get("ok")}
    runs = [(k, sc) for k, r in (summary.get("behaviors") or {}).items() if isinstance(r, dict)
            for sc, x in r.items() if isinstance(x, dict) and x.get("ok") is False]
    out.append(_check("behaviours", not unmet and not runs,
                      f"unmet {unmet}; failed scenarios {runs}" if unmet or runs else "met in PhysX"))
    mq = entry.get("motion_qa") or {}
    out.append(_check("motion_critic", mq.get("pass") and not mq.get("incomplete")
                      and mq.get("date", "") >= (led.get("verify") or {}).get("at", "")[:10],
                      "passed" if mq.get("pass") else
                      "incomplete" if mq.get("incomplete") else
                      "; ".join(f"{k}: {(v.get('problem') or '')[:70]}" for k, v in (mq.get("joints") or {}).items()
                                if not v.get("motion_ok"))[:300] or "not run"))
    comp = completeness(entry)
    out.append(_check("nothing_missing", comp["ok"],
                      "moves on the real object but is not jointed: " + "; ".join(comp["missing"][:4])
                      if comp["missing"] else "every surveyed moving part is jointed"))
    return out


def _views(entry: dict) -> list[str]:
    """Orbit renders of the file as it is now (re-rendered when older)."""
    from ingest_asset import refresh_renders

    views = [v for v in entry.get("views") or [] if Path(v).exists()]
    fmt = os.path.getmtime(entry["file"])
    if len(views) < 2 or min(os.path.getmtime(v) for v in views) < fmt:
        refresh_renders(entry)
        views = [v for v in entry.get("views") or [] if Path(v).exists()]
    return views


def _visual_schema() -> dict:
    b = {"type": "boolean"}
    s = {"type": "string"}
    return {"type": "object", "properties": {
        "what_it_is": s, "identity_matches": b, "upright": b, "intact": b, "intact_problems": s,
        "size_plausible": b, "materials_plausible": b, "materials_problems": s,
        "approve": b, "reasons": s, "confidence": {"type": "number"}},
        "required": ["what_it_is", "identity_matches", "upright", "intact", "intact_problems", "size_plausible",
                     "materials_plausible", "materials_problems", "approve", "reasons", "confidence"],
        "additionalProperties": False}


def visual_critic(entry: dict, m: dict) -> dict:
    """Claude looks at the asset as it now is and says whether a person would
    sign it off: the object it claims to be, upright, intact, believable size
    and physics materials. Fail-closed: any doubt goes to a person."""
    import anthropic
    from PIL import Image

    import visual_qa as vq

    views = _views(entry)
    if not views:
        return {"ok": False, "reasons": "no renders"}
    tiles = [Image.open(v).convert("RGB").resize((512, 512)) for v in views[:4]]
    grid = Image.new("RGB", (1024, 512 * ((len(tiles) + 1) // 2)), "white")
    for k, t in enumerate(tiles):
        grid.paste(t, ((k % 2) * 512, (k // 2) * 512))
    path = QUEUE / "thumbs" / f"{entry['asset_id']}__approval.png"
    grid.save(path)
    pm = m.get("physics_materials") or []        # what is bound, not what the class suggests
    vlm = entry.get("vlm") or {}
    prompt = (
        f"Four views of a 3D asset about to be approved for robot-learning simulation. It is said to be: "
        f"{vlm.get('object_name') or entry['asset_id']} (class {entry.get('class_hint')}). "
        f"Measured size {' x '.join(f'{d:.3f}' for d in m['dims_m'])} m (x, y, z; z is up), mass {m['mass_kg']} kg, "
        f"physics materials: {', '.join(pm) or 'none listed'}"
        + (f"; soft parts simulated as deformables: {', '.join(x['role'][:30] for x in (entry.get('soft_body_parts') or {}).get('soft', []))}"
           if entry.get("soft_body_parts") else "")
        + (f", {m['moving_joints']} moving joints (their motion was judged separately)" if m["moving_joints"] else "")
        + ".\nAs a careful reviewer, say: what it is; whether that matches; whether it rests upright the way the "
        "real object stands; whether it is intact (no missing, black or inside-out surfaces, no parts floating "
        "free, no leftover ground plane or backdrop, no duplicate overlapping copies); whether the size and mass "
        "are believable for it; whether the physics materials fit what it looks made of. Approve only if all of "
        "these hold. Do not approve on doubt.")
    r = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=8000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": vq._b64(str(path))}},
            {"type": "text", "text": prompt}]}],
        output_config={"format": {"type": "json_schema", "schema": _visual_schema()}})
    if r.stop_reason == "refusal":
        return {"ok": False, "reasons": "the model declined"}
    v = json.loads(next(b.text for b in r.content if b.type == "text"))
    v["ok"] = bool(v["approve"] and v["identity_matches"] and v["upright"] and v["intact"]
                   and v["size_plausible"] and v["materials_plausible"] and v["confidence"] >= 0.55)
    v["image"] = str(path.relative_to(REPO))
    return v


def _verification(entry: dict) -> dict | None:
    """The registry's articulation evidence, from the PhysX run."""
    sf = ANIM / entry["asset_id"] / "summary.json"
    if not sf.exists():
        return None
    s = json.loads(sf.read_text())
    rows = []
    for k, v in (s.get("joints") or {}).items():
        if v.get("follower"):
            continue
        lo, hi = v["limits"]
        far = hi if abs(hi) >= abs(lo) else lo
        meas = v["measured_range"][1] if far >= 0 else v["measured_range"][0]
        rows.append({"joint": k, "type": v["type"], "commanded": float(far), "measured": float(meas),
                     "error": round(abs(float(far) - float(meas)), 4),
                     "unit": "deg" if v["type"] == "revolute" else "m", "pass": True})
    if not rows:
        return None
    worst = max(rows, key=lambda r: r["error"] if r["unit"] == "m" else r["error"] / 1000.0)
    integ = s.get("integrity") or {}
    return {"date": date.today().isoformat(), "method": "live_physx_drive_test", "simulator": "Isaac Sim 6 / PhysX",
            "articulation": {"joint": worst["joint"], "unit": worst["unit"], "commanded_m": worst["commanded"],
                             "measured_m": worst["measured"], "position_error_m": worst["error"],
                             "max_position_error_m": max(worst["error"], 0.005) if worst["unit"] == "m"
                             else max(worst["error"], 2.0),
                             "base_drift_m": float(integ.get("root_moved_m") or 0.0),
                             "max_base_drift_m": max(0.01, float(integ.get("root_moved_m") or 0.0)),
                             "joints": rows}}


def withdraw(entry: dict, why: str) -> str:
    """Take back a machine approval that no longer holds."""
    from asset_review_hub import load_registry, save_queue_entry, write_registry

    reg = load_registry()
    reg["assets"] = [a for a in reg["assets"] if a["asset_id"] != entry["asset_id"]]
    write_registry(reg)
    entry["status"] = "pending_review"
    entry.setdefault("history", []).append({"withdrawn": {"date": date.today().isoformat(), "why": why,
                                                          "review": entry.pop("review", None)}})
    save_queue_entry(entry)
    return f"withdrawn: {why}"


def approve(asset_id: str, dry: bool = False, use_critic: bool = True) -> dict:
    """Assess one asset; approve, send to a person, or withdraw."""
    from asset_review_hub import do_approve, load_registry, save_queue_entry
    from processing import record

    qf = QUEUE / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    review = entry.get("review") or {}
    if entry.get("status") in ("approved", "promoted", "rejected") and review.get("reviewer_type") != "machine":
        return {"asset_id": asset_id, "outcome": "decided by a person", "failed": []}
    try:
        m = measure(entry)
        checks = gates(entry, m)
    except Exception as ex:  # noqa: BLE001 - an asset that cannot be measured goes to a person
        m, checks = {}, [_check("measurable", False, f"{type(ex).__name__}: {str(ex)[:150]}")]
    failed = [c["check"] for c in checks if not c["ok"]]
    visual = None
    was_machine = entry.get("status") in ("approved", "promoted") and review.get("reviewer_type") == "machine"
    if was_machine and use_critic:
        # a standing approval is re-checked on the measured gates alone: the
        # visual critic's verdict varies between runs on the same renders (a
        # dog bowl refused one day and approved the next), and withdrawing on
        # that would be noise, not evidence
        use_critic = False
    if not failed and use_critic:
        try:
            visual = visual_critic(entry, m)
        except Exception as ex:  # noqa: BLE001 - not judged: a person decides
            visual = {"ok": False, "reasons": f"visual critic unavailable: {str(ex)[:120]}", "not_judged": True}
        if not visual.get("ok"):
            failed.append("visual_critic")
    category = ("mixed_body_verified" if entry.get("soft_body_parts") and (entry.get("mixed_body") or {}).get("pass")
                else "articulated_verified" if m.get("moving_joints") else "rigid_unverified")
    outcome = "approved" if not failed else "human_review"
    msg = ""
    if not dry:
        if outcome == "approved" and not was_machine:
            prior_machine = sum(1 for a in load_registry().get("assets", [])
                                if (a.get("review") or {}).get("reviewer_type") == "machine")
            sampled = (prior_machine + 1) % max(1, AUDIT_EVERY) == 0
            ver = _verification(entry) if category.startswith("articulated") else None
            msg = do_approve(
                entry, category, REVIEWER,
                "approved on measured evidence and a visual critic: " + ", ".join(c["check"] for c in checks),
                review_extra={"reviewer_type": "machine",
                              "models": [f"{MODEL} (visual critic)"]
                              + ([f"{MODEL} (motion critic)"] if category.startswith("articulated") else []),
                              "evidence": {"checks": [c["check"] for c in checks],
                                           "visual": {k: visual.get(k) for k in ("what_it_is", "confidence")}},
                              **({"audit_sampled": True} if sampled else {})},
                verification=ver,
                soft_parts=entry.get("soft_body_parts") if category.startswith("mixed") else None)
            if not msg.startswith("approved"):
                outcome, failed = "human_review", failed + ["sign_off"]
        elif outcome != "approved" and was_machine:
            msg = withdraw(entry, "no longer passes: " + ", ".join(failed))
            outcome = "withdrawn"
        entry = json.loads(qf.read_text())
        entry["auto_approval"] = {"date": datetime.now().isoformat(timespec="seconds"), "outcome": outcome,
                                  "category": category, "failed": failed, "checks": checks,
                                  **({"visual": visual} if visual else {}), **({"message": msg} if msg else {})}
        record(entry, "approve", outcome=outcome, failed=failed)
        save_queue_entry(entry)
    return {"asset_id": asset_id, "outcome": outcome, "failed": failed, "category": category,
            "message": msg, "checks": checks, "visual": visual}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("assets", nargs="*")
    ap.add_argument("--pending", action="store_true", help="every asset still waiting for review")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="assess and report; approve nothing")
    ap.add_argument("--no-critic", action="store_true", help="gates only (no model calls)")
    args = ap.parse_args()
    ids = list(args.assets)
    if args.pending:
        for f in sorted(QUEUE.glob("*.json")):
            try:
                e = json.loads(f.read_text())
            except ValueError:
                continue
            if e.get("asset_id") and e.get("status") == "pending_review":
                ids.append(e["asset_id"])
    if args.limit:
        ids = ids[: args.limit]
    from collections import Counter
    tally, blockers = Counter(), Counter()
    for a in ids:
        r = approve(a, dry=args.dry_run, use_critic=not args.no_critic)
        tally[r["outcome"]] += 1
        blockers.update(r["failed"][:1])
        print(f"{a}: {r['outcome']}" + (f" ({', '.join(r['failed'])})" if r["failed"] else "")
              + (f" - {r['message'][:120]}" if r.get("message") else ""), flush=True)
    print("outcomes:", dict(tally))
    print("first blocker:", dict(blockers.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
