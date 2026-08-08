#!/usr/bin/env python3
"""Asset Review Hub — the human review-and-judgment surface.

Mechanical fixes (scale, orientation, rigid physics) happen automatically
at ingest; live verification and promotion run from their CLIs. What
remains here is exactly what needs a human:

  * SEE the asset — thumbnail card + callouts; Open in Isaac Sim for deep
    inspection (live Kit RPC session, or cold launch).
  * JUDGE it — VLM classify / manual reclassify when the image disagrees
    with the class; Segment baked mesh; draft-edit-apply articulation
    (joint types, axes, limits are judgment calls).
  * SIGN it — Approve (promotes into the library, schema-enforced named
    review) or Reject. Approval is blocked while error callouts stand.

Start with launch_review_hub.sh (OpenUSD env + .env for the VLM key),
default port 8777. Queue intake: the scan box, the ASSET_WATCH_DIRS
watcher, or scripts/ingest_asset.py.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUEUE_DIR = REPO / "workspace" / "review_queue"
REGISTRY = REPO / "workspace" / "knowledge" / "sim_ready_assets.json"
SCHEMA = REPO / "workspace" / "knowledge" / "sim_ready_asset_registry.schema.json"
KIT_RPC = f"http://127.0.0.1:{os.environ.get('KIT_RPC_PORT', '8001')}"
PORT = int(os.environ.get("REVIEW_HUB_PORT", "8777"))

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_asset import (  # noqa: E402
    FIXED_DIR,
    build_wrapper,
    needs_articulation,
    propose_category,
    run_report,
    scan_dir,
)

# Auto-watch: poll these directories and queue anything new/changed.
# Comma-separated in ASSET_WATCH_DIRS; interval ASSET_WATCH_INTERVAL_S.
WATCH_DIRS = [d for d in os.environ.get("ASSET_WATCH_DIRS", "").split(",") if d.strip()]
WATCH_INTERVAL = int(os.environ.get("ASSET_WATCH_INTERVAL_S", "120"))
DEFAULT_SCAN_DIR = os.environ.get(
    "ASSET_SCAN_DIR", str(Path.home() / "Desktop" / "assets" / "SketchFab_Assets"))
_watch_status = {"last": "watcher not running", "queued_total": 0}

CATEGORIES = ["deformable_verified", "deformable_unverified",
              "articulated_verified", "articulated_unverified",
              "rigid_verified", "rigid_unverified", "rigid_only_baked"]

SEV_COLOR = {"error": "#e5484d", "warning": "#f5a524", "info": "#3e97ff"}


# ---------------------------------------------------------------------------
# data access

def load_queue() -> list[dict]:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for f in sorted(QUEUE_DIR.glob("*.json")):
        try:
            entries.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return entries


def save_queue_entry(entry: dict) -> None:
    (QUEUE_DIR / f"{entry['asset_id']}.json").write_text(json.dumps(entry, indent=1))


def load_registry() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text())
    return {"version": 1, "assets": []}


def write_registry(reg: dict) -> str | None:
    """Validate (when jsonschema is available) and write. Returns error or None."""
    try:
        import jsonschema
        jsonschema.validate(reg, json.loads(SCHEMA.read_text()))
    except ImportError:
        pass
    except Exception as e:  # validation failure — refuse to write
        return f"registry schema validation failed: {e}"
    REGISTRY.write_text(json.dumps(reg, indent=1) + "\n")
    return None


def stamp_usd(file_path: str, category: str, reviewer: str) -> str:
    """Stamp customData['simReady'] on the asset's root/default prim."""
    try:
        from pxr import Usd
    except ImportError:
        return "pxr not importable — USD not stamped (registry updated)"
    if file_path.lower().endswith(".usdz"):
        return ("usdz is an immutable archive — not stamped; the stamp goes on "
                "the sim-ready derivative (make_sim_ready output) instead")
    try:
        stage = Usd.Stage.Open(file_path)
        prim = stage.GetDefaultPrim() or stage.GetPseudoRoot()
        # prefer the first child of a /World default prim (the asset itself)
        if prim.GetName() == "World":
            kids = [c for c in prim.GetChildren()
                    if c.GetTypeName() in ("Xform", "Scope", "")]
            if kids:
                prim = kids[0]
        prim.SetCustomDataByKey("simReady", {
            "category": category,
            "registry": "workspace/knowledge/sim_ready_assets.json",
            "verified": date.today().isoformat(),
            "reviewer": reviewer,
        })
        stage.GetRootLayer().Save()
        return f"stamped {prim.GetPath()}"
    except Exception as e:
        return f"stamp failed: {e}"


# ---------------------------------------------------------------------------
# corrective actions — machine fixes what it can, then re-runs the checks

def _camel(s: str) -> str:
    return "".join(w.capitalize() for w in s.split("_")) or "Asset"


def _re_ingest(entry: dict) -> None:
    """Re-run the automated checks on the entry's current file."""
    report = run_report(entry["file"], entry.get("class_hint"))
    entry["report"] = report
    entry["proposed_category"] = propose_category(report)
    save_queue_entry(entry)


def fix_scale(entry: dict) -> str:
    factor = entry["report"].get("suggested_scale_correction")
    if not factor:
        return "no scale correction suggested for this asset"
    src_mpu = entry["report"].get("meters_per_unit")
    if "original_file" not in entry:
        entry["original_file"] = entry["file"]
    entry["file"] = build_wrapper(entry, float(factor))
    entry["applied_fixes"] = entry.get("applied_fixes", []) + [
        f"scale x{factor} (source units x{src_mpu})"]
    _re_ingest(entry)
    ok = entry["report"].get("ingest_ok")
    return (f"scale fix applied -> {entry['file']} — re-checked: "
            + ("all scale checks pass" if ok or not any(
                c["check"] == "scale" and c["severity"] == "error"
                for c in entry["report"]["callouts"]) else "scale still flagged"))


def draft_articulation(entry: dict) -> str:
    """Propose a joint-spec draft from the asset's part structure. The
    reviewer edits it (types, axes, limits are judgment) and applies."""
    from pxr import Usd, UsdGeom

    if "original_file" not in entry or not entry["file"].startswith(str(FIXED_DIR)):
        if "original_file" not in entry:
            entry["original_file"] = entry["file"]
        entry["file"] = build_wrapper(entry, None)
        entry["report"] = run_report(entry["file"], entry.get("class_hint"))
    stage = Usd.Stage.Open(entry["file"])
    asset_root = f"/World/{_camel(entry['asset_id'])}"
    # tier 1: geometry-driven proposal (symmetry, wheel detection, link
    # grouping) — falls back to the naive parent-of-mesh listing
    try:
        from articulation_draft import propose
        spec = propose(stage, asset_root, asset_root)
        entry["articulation_draft"] = json.dumps(spec, indent=1)
        save_queue_entry(entry)
        a = spec.get("_analysis", {})
        return (f"draft proposed from geometry: {a.get('parts')} parts, "
                f"symmetry axis {a.get('symmetry_axis')}, "
                f"{a.get('wheels_detected')} wheels detected, base "
                f"{str(a.get('base_link', '')).rsplit('/', 1)[-1]}, "
                f"{len(spec['joints'])} joints — review, edit, then Apply")
    except Exception as ex:
        geo_err = str(ex)[:120]
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                              [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    links = {}
    for p in stage.Traverse():
        if p.IsA(UsdGeom.Mesh):
            parent = p.GetParent()
            r = cache.ComputeWorldBound(parent).ComputeAlignedRange()
            if not r.IsEmpty():
                s = r.GetSize()
                links[str(parent.GetPath())] = s[0] * s[1] * s[2]
    if len(links) < 2:
        return ("only one mesh-bearing part found — this asset is baked; "
                "use Segment baked mesh first")
    ordered = sorted(links, key=links.get, reverse=True)
    base, children = ordered[0], ordered[1:]
    spec = {
        "prim_path": asset_root,
        "fixed_base": False,
        "joints": [
            {"name": f"joint_{i}", "joint_type": "revolute|prismatic|fixed",
             "parent_prim": base, "child_prim": c, "axis": "Z",
             "lower_limit": None, "upper_limit": None}
            for i, c in enumerate(children[:12])
        ],
        "_instructions": ("EDIT before applying: set each joint_type, axis "
                          "(X/Y/Z), and limits (deg for revolute, m for "
                          "prismatic); delete joints for parts that are "
                          "fixed to the base (or set joint_type 'fixed'); "
                          "remove this key when done"),
    }
    entry["articulation_draft"] = json.dumps(spec, indent=1)
    save_queue_entry(entry)
    return (f"draft spec proposed (naive fallback; geometry tier: {geo_err}): "
            f"base {base.rsplit('/', 1)[-1]}, {len(children)} candidates — "
            "edit the spec, then Apply")


def apply_articulation(entry: dict, spec_text: str) -> str:
    """Run articulate_asset with the reviewer-edited spec on the derivative."""
    import contextlib
    import io
    import types

    from pxr import Usd

    from service.isaac_assist_service.chat.tools.handlers.physics import (
        _gen_articulate_asset,
    )

    spec = json.loads(spec_text)
    spec.pop("_instructions", None)
    spec.pop("_analysis", None)
    if any("|" in str(j.get("joint_type", "")) for j in spec.get("joints", [])):
        return "spec still has placeholder joint_type values — edit before applying"
    stage = Usd.Stage.Open(entry["file"])
    omni = types.ModuleType("omni")
    omni_usd = types.ModuleType("omni.usd")
    ctx = type("Ctx", (), {"get_stage": lambda self: stage})()
    omni_usd.get_context = lambda: ctx
    omni.usd = omni_usd
    sys.modules["omni"] = omni
    sys.modules["omni.usd"] = omni_usd
    code = _gen_articulate_asset(spec)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<articulate>", "exec"), {"__builtins__": __builtins__})
    stage.GetRootLayer().Save()
    entry["articulation_draft"] = spec_text
    entry["applied_fixes"] = entry.get("applied_fixes", []) + [
        f"articulate_asset: {len(spec.get('joints', []))} joints"]
    _re_ingest(entry)
    verdict = entry["report"].get("verdict", "")
    return f"articulation applied ({len(spec.get('joints', []))} joints) — re-checked: {verdict}"


# ---------------------------------------------------------------------------
# isaac launch

def open_in_isaac(file_path: str) -> str:
    """Open the asset in Isaac Sim: live session first, cold launch second."""
    try:
        req = urllib.request.Request(KIT_RPC + "/health")
        urllib.request.urlopen(req, timeout=2)
        alive = True
    except Exception:
        alive = False
    if alive:
        code = (f"import omni.usd\n"
                f"omni.usd.get_context().open_stage({file_path!r})\n"
                f"print('opened', {file_path!r})")
        body = json.dumps({"code": code, "timeout": 60}).encode()
        req = urllib.request.Request(KIT_RPC + "/exec_sync", body,
                                     {"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=90).read())
        if r.get("success"):
            return "opened in the running Isaac Sim session"
        return f"live session open failed: {r.get('output', '')[:200]}"
    log = Path("/tmp/asset_review_isaac_launch.log")
    subprocess.Popen([str(REPO / "launch_isaac.sh"), file_path],
                     stdout=log.open("w"), stderr=subprocess.STDOUT,
                     start_new_session=True, cwd=str(REPO))
    return ("no running session — launching Isaac Sim with the asset "
            f"(takes a few minutes; log: {log})")


# ---------------------------------------------------------------------------
# html

def page(body: str) -> bytes:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Asset Review Hub</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; background: #101214; color: #e6e9ec; }}
 header {{ padding: 14px 28px; background: #17191c; border-bottom: 1px solid #26292e; }}
 header h1 {{ font-size: 18px; margin: 0; }} header span {{ color: #8b929b; font-size: 13px; }}
 main {{ max-width: 1080px; margin: 0 auto; padding: 20px 28px 60px; }}
 .card {{ background: #17191c; border: 1px solid #26292e; border-radius: 10px; padding: 16px 20px; margin: 14px 0; }}
 .card h2 {{ margin: 0 0 2px; font-size: 16px; }}
 .path {{ color: #8b929b; font-size: 12.5px; word-break: break-all; }}
 .badge {{ display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; margin-left: 8px; vertical-align: 2px; }}
 .pass {{ background: #143c22; color: #4cc38a; }} .fail {{ background: #3c1618; color: #ff8f8f; }}
 .done {{ background: #1b2a41; color: #7cb6ff; }}
 table {{ border-collapse: collapse; margin: 10px 0; width: 100%; font-size: 13.5px; }}
 td {{ border-top: 1px solid #22252a; padding: 5px 10px 5px 0; vertical-align: top; }}
 td.sev {{ font-weight: 700; white-space: nowrap; }}
 .actions {{ margin-top: 12px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
 button, select, input {{ font: inherit; border-radius: 7px; border: 1px solid #33373d; background: #1e2126; color: #e6e9ec; padding: 6px 14px; }}
 button {{ cursor: pointer; }} button.primary {{ background: #2456a6; border-color: #2f6cc9; }}
 button.launch {{ background: #275b33; border-color: #357a45; }} button.reject {{ background: #5b2727; border-color: #7a3535; }}
 .msg {{ background: #1b2a41; border: 1px solid #2f4a73; border-radius: 8px; padding: 10px 16px; margin: 14px 0; }}
 .meta {{ color: #a7adb5; font-size: 13px; margin: 6px 0; }}
 code {{ background: #22252a; padding: 1px 6px; border-radius: 5px; font-size: 12.5px; }}
</style></head><body>
<header><h1>Asset Review Hub</h1>
<span>machine evidence &rarr; human verification &rarr; sim-ready registry &nbsp;·&nbsp; registry: <code>{html.escape(str(REGISTRY.relative_to(REPO)))}</code></span></header>
<main>{body}</main></body></html>""".encode()


def render_entry(e: dict) -> str:
    r = e.get("report", {})
    ok = r.get("ingest_ok")
    status = e.get("status", "pending_review")
    if status == "approved":
        badge = '<span class="badge done">APPROVED</span>'
    elif status == "rejected":
        badge = '<span class="badge fail">REJECTED</span>'
    elif ok:
        badge = '<span class="badge pass">PASS — pending review</span>'
    else:
        badge = '<span class="badge fail">CALLOUTS</span>'
    rows = "".join(
        f'<tr><td class="sev" style="color:{SEV_COLOR.get(c["severity"], "#ccc")}">'
        f'{c["severity"].upper()}</td><td>{html.escape(c["check"])}</td>'
        f'<td>{html.escape(c["message"])}</td></tr>'
        for c in r.get("callouts", []))
    callouts = (f"<table>{rows}</table>" if rows
                else '<p class="meta">no callouts — all automated checks passed</p>')
    joints = r.get("structure", {}).get("joints", [])
    cls_note = (" (filename guess — confirm against the image)"
                if e.get("class_source", "filename_guess") == "filename_guess" else "")
    meta = (f'class <code>{html.escape(str(r.get("matched_class")))}</code>{cls_note} · '
            f'max dim <code>{r.get("max_dim_m", "?")} m</code> · '
            f'{r.get("structure", {}).get("meshes", "?")} meshes · '
            f'{len(joints)} joints · proposed '
            f'<code>{html.escape(e.get("proposed_category", "?"))}</code>')
    thumb = ""
    if e.get("thumbnail") and Path(e["thumbnail"]).exists():
        thumb = (f'<img src="/thumb/{html.escape(Path(e["thumbnail"]).name)}" '
                 'style="float:right;max-height:150px;max-width:210px;'
                 'border-radius:8px;background:#0b0c0e;margin-left:14px" '
                 'alt="render — verify this matches the class">')
    cert = ""
    if r.get("certifications"):
        c0 = list(r["certifications"].values())[0]
        cert = f'<p class="meta">already certified: <code>{html.escape(c0.get("category", ""))}</code></p>'
    review_note = ""
    if status in ("approved", "rejected") and e.get("review"):
        rv = e["review"]
        review_note = (f'<p class="meta">{status} by <b>{html.escape(rv.get("reviewer", "?"))}</b> '
                       f'on {html.escape(rv.get("date", "?"))}'
                       + (f' — {html.escape(rv.get("notes", ""))}' if rv.get("notes") else "")
                       + '</p>')
    aid = html.escape(e["asset_id"])
    options = "".join(
        f'<option value="{c}"{" selected" if c == e.get("proposed_category") else ""}>{c}</option>'
        for c in CATEGORIES)
    errors = [c for c in r.get("callouts", []) if c["severity"] == "error"]
    fixes = ""
    if e.get("applied_fixes"):
        fixes = ('<p class="meta">applied fixes: '
                 + " · ".join(html.escape(f) for f in e["applied_fixes"]) + "</p>")
    actions = ""
    if status not in ("approved", "rejected"):
        # judgment actions only — mechanical fixes run automatically at
        # ingest (scale, orientation, rigid physics) or at approve (promote)
        corrective = ""
        arti_needed = any(c["check"] == "articulation" and "articulate_asset" in c["message"]
                          for c in r.get("callouts", []))
        baked = any("baked asset" in c["message"] for c in r.get("callouts", []))
        if baked:
            corrective += ('<button name="do" value="segment">Segment baked mesh '
                           '(split fused parts)</button>')
        if arti_needed:
            corrective += ('<button name="do" value="draft_arti">Draft articulation '
                           'spec</button>')
        corrective += '<button name="do" value="vlm">Classify visually (VLM)</button>'
        corrective += '<button name="do" value="recheck">Re-run checks</button>'
        gate = ""
        approve_btn = '<button class="primary" name="do" value="approve">Approve &rarr; registry</button>'
        if errors:
            gate = ('<p class="meta" style="color:#ff8f8f">approval blocked: resolve the '
                    'error callouts with the corrective actions (or classify as '
                    'rigid_only_baked / reject). A physically incorrect asset cannot '
                    'enter the registry.</p>')
        actions = f"""{gate}
<form class="actions" method="post" action="/action">
 <input type="hidden" name="asset_id" value="{aid}">
 <button class="launch" name="do" value="launch">&#9654; Open in Isaac Sim</button>
 {corrective}
 <select name="category">{options}</select>
 <input name="reviewer" placeholder="reviewer" style="width:110px">
 <input name="notes" placeholder="notes (optional)" style="width:200px">
 {approve_btn}
 <button class="reject" name="do" value="reject">Reject</button>
</form>"""
    arti_editor = ""
    if status not in ("approved", "rejected") and e.get("articulation_draft"):
        arti_editor = f"""
<form method="post" action="/action" style="margin-top:8px">
 <input type="hidden" name="asset_id" value="{aid}">
 <p class="meta">articulation spec — edit joint types/axes/limits, then apply:</p>
 <textarea name="spec" rows="12" style="width:100%;font:12px monospace;background:#0b0c0e;color:#d7dbe0;border:1px solid #33373d;border-radius:7px;padding:8px">{html.escape(e["articulation_draft"])}</textarea>
 <div class="actions"><button class="primary" name="do" value="apply_arti">Apply articulation</button></div>
</form>"""
    reclass = "" if status in ("approved", "rejected") else f"""
<form class="actions" method="post" action="/action" style="margin-top:6px">
 <input type="hidden" name="asset_id" value="{aid}">
 <span class="meta" style="align-self:center">image shows something else?</span>
 <input name="class_hint" placeholder="true class (e.g. wheelchair, pan)" style="width:210px">
 <button name="do" value="reclass">Reclassify &amp; re-check</button>
</form>"""
    return (f'<div class="card">{thumb}<h2>{aid}{badge}</h2>'
            f'<div class="path">{html.escape(e.get("file", ""))}</div>'
            f'<p class="meta">{meta}</p>{cert}{fixes}{callouts}{review_note}{actions}'
            f'{arti_editor}{reclass}'
            f'<div style="clear:both"></div></div>')


def render_index(msg: str = "") -> bytes:
    entries = load_queue()
    reg = load_registry()
    pending = [e for e in entries if e.get("status") == "pending_review"]
    body = f'<div class="msg">{html.escape(msg)}</div>' if msg else ""
    watch = (f'auto-watch: {", ".join(WATCH_DIRS)} every {WATCH_INTERVAL}s — '
             f'{html.escape(_watch_status["last"])}' if WATCH_DIRS
             else 'auto-watch off (set ASSET_WATCH_DIRS to enable)')
    body += (f'<p class="meta">{len(pending)} pending review · '
             f'{len(reg.get("assets", []))} in registry · {watch}</p>')
    machine = [a for a in reg.get("assets", [])
               if a.get("review", {}).get("reviewer_type") == "machine"]
    if machine:
        rows = "".join(
            f'<li>{html.escape(a["asset_id"])} — {html.escape(a["category"])}'
            f' (judges: {html.escape(", ".join(a["review"].get("models", [])))})'
            + (' <b>AUDIT: human spot-check requested</b>'
               if a["review"].get("audit_sampled") else "")
            + "</li>" for a in machine)
        body += ('<div class="card"><h2>Machine approvals — audit lane</h2>'
                 f'<ul class="meta">{rows}</ul>'
                 '<p class="meta">The pipeline signed these itself (rigid '
                 'only, unanimous visual QA). Sampled items want a human '
                 'look; use the entry card to reject a wrong one.</p></div>')
    body += f"""
<form class="actions" method="post" action="/scan" style="margin:10px 0 4px">
 <input name="dir" value="{html.escape(DEFAULT_SCAN_DIR)}" style="flex:1;min-width:340px">
 <input name="limit" value="10" style="width:60px" title="max new assets this scan">
 <button class="primary">Scan folder for new assets</button>
</form>"""
    if not entries:
        body += '<div class="card"><h2>Queue is empty</h2><p class="meta">Scan a folder or ingest an asset to start.</p></div>'
    for e in entries:
        body += render_entry(e)
    return page(body)


# ---------------------------------------------------------------------------
# actions

def do_approve(entry: dict, category: str, reviewer: str, notes: str,
               review_extra: dict | None = None) -> str:
    """Sign + promote. review_extra carries machine-reviewer metadata
    (reviewer_type/models/audit_sampled) — schema enforces that machine
    reviews can only land on rigid categories."""
    report = entry.get("report", {})
    # approved must mean finished: articulated categories need their joints
    # authored before a human can sign them off
    if category.startswith("articulated") and not report.get(
            "structure", {}).get("joints"):
        return ("NOT approved — articulated category but no joints authored. "
                "Use 'Draft articulation spec' → Apply first.")
    reg = load_registry()
    reg["assets"] = [a for a in reg["assets"] if a["asset_id"] != entry["asset_id"]]
    # passing Newton drape evidence upgrades a human-approved deformable
    # to deformable_verified — same pattern as the rigid drop-test flip
    drape = entry.get("drape_test") or {}
    if (category == "deformable_unverified"
            and drape.get("drapes_like_cloth")):
        category = "deformable_verified"
    new = {
        "asset_id": entry["asset_id"],
        "file": entry["file"],
        "source_file": entry.get("original_file") or entry["file"],
        "category": category,
        **({"deformable": entry.get("deformable", "cloth"),
            **({"verification": {"newton": drape}} if drape else {})}
           if category.startswith("deformable") else {}),
        "audit": {"ready": category.endswith("_verified"),
                  "simulable": bool(report.get("structure", {}).get("rigid_bodies"))},
        "review": {"approved": True, "reviewer": reviewer,
                   "date": date.today().isoformat(),
                   **({"notes": notes} if notes else {}),
                   **(review_extra or {})},
    }
    if category == "rigid_only_baked":
        new["audit"] = {"ready": False, "simulable": True}
    reg["assets"].append(new)
    err = write_registry(reg)
    if err:
        return f"NOT approved — {err}"
    # promotion IS part of approval: physics ensured, landed in the library,
    # stamped, registry pointed at the library copy
    try:
        from promote_asset import promote
        promote_msg = promote(entry["asset_id"])
    except Exception as ex:
        reg = load_registry()
        reg["assets"] = [a for a in reg["assets"] if a["asset_id"] != entry["asset_id"]]
        write_registry(reg)
        return f"NOT approved — promotion failed: {ex}"
    fresh = next((e for e in load_queue() if e["asset_id"] == entry["asset_id"]), entry)
    fresh["status"] = "approved"
    fresh["review"] = new["review"]
    save_queue_entry(fresh)
    # a HUMAN approving an asset under a provisional (VLM-created) prior
    # class confirms the class — later assets of this kind can
    # machine-approve; the taxonomy grows with one human touch per kind
    if (review_extra or {}).get("reviewer_type") != "machine":
        cls = fresh.get("report", {}).get("matched_class")
        try:
            import json as _json
            priors_path = REPO / "workspace" / "knowledge" / "asset_class_priors.json"
            data = _json.loads(priors_path.read_text())
            prior = data["classes"].get(cls or "", {})
            if prior.get("source") == "vlm":
                prior.pop("source", None)
                prior["confirmed_by"] = reviewer
                prior["confirmed_on"] = date.today().isoformat()
                priors_path.write_text(_json.dumps(data, indent=1))
                promote_msg += f"; confirmed new class '{cls}'"
        except (OSError, ValueError):
            pass
    return f"approved {entry['asset_id']} as {category}; {promote_msg}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, data: bytes, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(b"ok")
            return
        if self.path.startswith("/thumb/"):
            name = Path(urllib.parse.unquote(self.path[len("/thumb/"):])).name
            f = QUEUE_DIR / "thumbs" / name
            if f.exists() and f.suffix == ".png":
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send(b"not found", 404)
            return
        self._send(render_index())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        get = lambda k: form.get(k, [""])[0].strip()
        if self.path.startswith("/scan"):
            try:
                limit = int(get("limit") or "10")
            except ValueError:
                limit = 10
            try:
                res = scan_dir(get("dir") or DEFAULT_SCAN_DIR, limit=limit)
                msg = (f"scan: {len(res['queued'])} queued, {len(res['skipped'])} skipped"
                       + (f", {len(res['errors'])} errors" if res["errors"] else ""))
            except Exception as ex:
                msg = f"scan failed: {ex}"
            self._send(render_index(msg))
            return
        asset_id, action = get("asset_id"), get("do")
        entry = next((e for e in load_queue() if e["asset_id"] == asset_id), None)
        if entry is None:
            self._send(render_index(f"unknown asset: {asset_id}"))
            return
        if action == "launch":
            msg = open_in_isaac(entry["file"])
        elif action == "recheck":
            try:
                _re_ingest(entry)
                msg = f"re-checked {asset_id}: {entry['report'].get('verdict')}"
            except Exception as ex:
                msg = f"re-check failed: {ex}"
        elif action == "vlm":
            try:
                from vlm_classify import classify_entry
                msg = classify_entry(asset_id)
            except Exception as ex:
                msg = f"VLM classification failed: {ex}"
        elif action == "segment":
            try:
                from segment_mesh import segment_entry
                msg = segment_entry(asset_id)
            except Exception as ex:
                msg = f"segmentation failed: {ex}"
        elif action == "draft_arti":
            try:
                msg = draft_articulation(entry)
            except Exception as ex:
                msg = f"draft failed: {ex}"
        elif action == "apply_arti":
            try:
                msg = apply_articulation(entry, get("spec"))
            except Exception as ex:
                msg = f"articulation failed: {ex}"
        elif action == "reclass":
            hint = get("class_hint")
            if not hint:
                msg = "provide the true class (a key from asset_class_priors.json)"
            else:
                try:
                    entry["class_hint"] = hint
                    entry["class_source"] = "human_visual"
                    _re_ingest(entry)
                    if entry["report"].get("suggested_scale_correction"):
                        msg = f"reclassified as {hint}; " + fix_scale(entry)
                    else:
                        msg = (f"reclassified as {hint}: "
                               f"{entry['report'].get('verdict')}")
                    save_queue_entry(entry)
                except Exception as ex:
                    msg = f"reclassify failed: {ex}"
        elif action == "approve":
            errors = [c for c in entry.get("report", {}).get("callouts", [])
                      if c["severity"] == "error"]
            if errors and get("category") != "rigid_only_baked":
                msg = (f"approval refused: {len(errors)} unresolved error callout(s) — "
                       "a physically incorrect asset cannot enter the registry. Use the "
                       "corrective actions, or classify as rigid_only_baked, or reject.")
            else:
                reviewer = get("reviewer") or os.environ.get("USER", "reviewer")
                msg = do_approve(entry, get("category"), reviewer, get("notes"))
        elif action == "reject":
            entry["status"] = "rejected"
            entry["review"] = {"approved": False,
                               "reviewer": get("reviewer") or os.environ.get("USER", "reviewer"),
                               "date": date.today().isoformat(),
                               **({"notes": get("notes")} if get("notes") else {})}
            save_queue_entry(entry)
            msg = f"rejected {asset_id}"
        else:
            msg = f"unknown action: {action}"
        self._send(render_index(msg))


def _watch_loop():
    import time
    from datetime import datetime
    while True:
        for d in WATCH_DIRS:
            try:
                res = scan_dir(d.strip(), limit=int(os.environ.get("ASSET_WATCH_LIMIT", "20")))
                if os.environ.get("VISUAL_QA_AUTO") == "1" and res["queued"]:
                    # fire-and-forget: judges verdict + machine sign-off for
                    # what passes; everything else stays in this queue
                    ids = [q["asset_id"] for q in res["queued"]]
                    subprocess.Popen(
                        [sys.executable,
                         str(Path(__file__).resolve().parent / "visual_qa.py"),
                         *ids, "--approve"],
                        stdout=open(QUEUE_DIR / "visual_qa_auto.log", "a"),
                        stderr=subprocess.STDOUT)
                _watch_status["queued_total"] += len(res["queued"])
                _watch_status["last"] = (
                    f"last pass {datetime.now().strftime('%H:%M:%S')}: "
                    f"{len(res['queued'])} new queued "
                    f"({_watch_status['queued_total']} total this session)")
            except Exception as e:
                _watch_status["last"] = f"watcher error: {str(e)[:120]}"
        time.sleep(WATCH_INTERVAL)


def main():
    if WATCH_DIRS:
        import threading
        threading.Thread(target=_watch_loop, daemon=True).start()
        print(f"watching for new assets: {WATCH_DIRS} every {WATCH_INTERVAL}s")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Asset Review Hub: http://127.0.0.1:{PORT}  (queue: {QUEUE_DIR})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
