#!/usr/bin/env python3
"""Downloads -> sim-ready library, end to end.

What was done by hand for each batch of downloads, as one command:

  1. survey    every USD file in the downloads folder, by CONTENT (length,
               then SHA-1) against the whole library: new, already in the
               library, or a copy of another download. A file whose bytes
               are not USD (an OpenSCAD script named .usda) is left alone.
  2. dedupe    with --delete-duplicates, files byte-identical to a library
               file are deleted (compared byte for byte first).
  3. stage     new files move into <library>/_incoming, never overwriting:
               a same-named different file gets its hash in its name.
  4. ingest    each is queued (scale, up axis, backdrop, rigid physics).
  5. classify  the VLM looks at framed, lit renders with the file's name and
               size as evidence: class, set/fragment/scene, object size.
  6. file      each moves to <library>/<folder> for its class
               (workspace/knowledge/asset_folders.json); queue entries are
               relinked by content.
  7. articulate  assets of articulable classes, or with a drafting tier in
               their class prior, are segmented when fused, drafted, and the
               draft applied when it is a real proposal (not the naive
               fallback) - a human still reviews it in the hub.
  8. verify    with --animate, articulated assets are animated in PhysX
               (one at a time, in the machine's Isaac slot); with --soft,
               cloth gets a simulation proxy and a Newton drape test and
               soft bodies a squish test.

A JSON report of the run goes to workspace/review_queue/_runs/.

Usage (needs pxr and, for step 5, ANTHROPIC_API_KEY - process_downloads.sh
sets both up):
    process_downloads.sh [--downloads ~/Downloads] [--library DIR]
        [--delete-duplicates] [--dry-run] [--no-vlm] [--no-articulate]
        [--animate] [--soft]
    process_downloads.sh --refile        # re-file library assets by the map
    process_downloads.sh --backfill [--limit N]   # library files with no entry yet
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

ASSET_EXTS = {".usd", ".usda", ".usdc", ".usdz"}
FOLDERS = REPO / "workspace" / "knowledge" / "asset_folders.json"
ISAAC_PYTHON = os.environ.get("ISAAC_PYTHON", "/home/kimate/Documents/Github/isaacsim/_build/linux-aarch64/release/python.sh")
NEWTON_PYTHON = str(REPO / ".venv-newton" / "bin" / "python")


# --- 1-3: survey, dedupe, stage -----------------------------------------------

def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_usd(path: Path) -> bool:
    """By the bytes, not the name: usdz is a zip, usdc starts PXR-USDC,
    usda starts #usda."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    return head.startswith(b"PK") or head.startswith(b"PXR-USDC") or head.startswith(b"#usda")


def library_index(root: Path) -> dict[int, list[Path]]:
    by_size: dict[int, list[Path]] = {}
    for p in root.rglob("*"):
        if p.suffix.lower() in ASSET_EXTS and p.is_file():
            try:
                by_size.setdefault(p.stat().st_size, []).append(p)
            except OSError:
                pass
    return by_size


def survey(downloads: Path, library_root: Path) -> list[dict]:
    """Every asset file in downloads, by content: new / in_library / copy / not_usd."""
    index = library_index(library_root)
    seen: dict[str, Path] = {}
    out = []
    for p in sorted(downloads.iterdir()):
        if not p.is_file() or p.suffix.lower() not in ASSET_EXTS:
            continue
        if not is_usd(p):
            out.append({"file": str(p), "kind": "not_usd"})
            continue
        size = p.stat().st_size
        digest = sha1(p)
        twin = next((q for q in index.get(size, []) if sha1(q) == digest), None)
        if twin:
            out.append({"file": str(p), "kind": "in_library", "same_as": str(twin), "sha1": digest})
        elif digest in seen:
            out.append({"file": str(p), "kind": "copy", "same_as": str(seen[digest]), "sha1": digest})
        else:
            seen[digest] = p
            out.append({"file": str(p), "kind": "new", "sha1": digest})
    return out


def same_bytes(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def free_name(folder: Path, name: str, digest: str) -> Path:
    """Never overwrite: a same-named different file gets its hash in its name."""
    target = folder / name
    if not target.exists():
        return target
    stem, ext = os.path.splitext(name)
    return folder / f"{stem}_{digest[:6]}{ext}"


# --- 4-7: ingest, classify, file, articulate -------------------------------------

def ingest(path: Path) -> tuple[str | None, str]:
    from ingest_asset import queue_file, resolve_identity

    asset_id, reason = resolve_identity(str(path))
    if reason:
        return None, reason
    entry = queue_file(str(path), None, asset_id)
    return entry["asset_id"], entry["report"].get("verdict", "")


def folder_for(entry: dict) -> str:
    """A reviewer's pin, else a scene by what the VLM saw, else the class's folder."""
    m = json.loads(FOLDERS.read_text())
    if entry.get("asset_id") in m.get("by_asset", {}):
        return m["by_asset"][entry["asset_id"]]
    if (entry.get("vlm") or {}).get("content_kind") in ("scene", "scene_fragment") and m.get("scenes"):
        return m["scenes"]
    cls = class_of(entry)
    for folder, classes in m["folders"].items():
        if cls in classes:
            return folder
    return m.get("default", "Misc")


def entry_of(asset_id: str) -> dict:
    from ingest_asset import QUEUE_DIR

    return json.loads((QUEUE_DIR / f"{asset_id}.json").read_text())


def class_of(e: dict) -> str | None:
    return e.get("class_hint") or e.get("report", {}).get("matched_class")


def file_assets(asset_ids: list[str], library_root: Path, dry: bool) -> list[str]:
    """Move each source to its class's folder, then relink by content."""
    from ingest_asset import relink_sources

    log = []
    for a in asset_ids:
        e = entry_of(a)
        src = Path(e.get("original_file") or e["file"])
        if not src.exists() or library_root not in src.parents:
            continue
        folder = library_root / folder_for(e)
        if src.parent == folder:
            continue
        target = free_name(folder, src.name, e.get("source_sha1") or sha1(src))
        log.append(f"{a}: {src.relative_to(library_root)} -> {target.relative_to(library_root)}")
        if not dry:
            folder.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
    if not dry and log:
        log += relink_sources(str(library_root))
    if not dry:
        from asset_review_hub import save_queue_entry
        from processing import record
        for a in asset_ids:                     # after the relink, which rewrites entries
            e = entry_of(a)
            record(e, "file", folder=folder_for(e))
            save_queue_entry(e)
    return log


def articulate(asset_id: str, dry: bool) -> str:
    """Segment if fused, draft, and apply a real proposal; an articulable
    asset left unjointed gets a provisional rigid body."""
    note = _articulate(asset_id, dry)
    if dry or "articulation applied" in note or note.startswith(("rigid:", "already")):
        return note
    from ingest_asset import apply_rigid_physics

    e = entry_of(asset_id)
    fixes = " | ".join(e.get("applied_fixes", []))
    if "physics:" in fixes and fixes.rfind("physics:") > max(fixes.rfind("unarticulated"), fixes.rfind("segmentation"),
                                                              fixes.rfind("key split")):
        return note
    rigid = apply_rigid_physics(e, provisional=True)
    if rigid:
        e["applied_fixes"] = e.get("applied_fixes", []) + [f"provisional (until articulated): {rigid}"]
        from asset_review_hub import save_queue_entry
        save_queue_entry(e)
        note += f"; provisional rigid body until articulated ({rigid})"
    return note


def _articulate(asset_id: str, dry: bool) -> str:
    from asset_review_hub import _load_priors_fresh, apply_articulation, draft_articulation
    from ingest_asset import needs_articulation
    from segment_mesh import segment_entry

    e = entry_of(asset_id)
    fixes = e.get("applied_fixes", [])
    done = [i for i, f in enumerate(fixes) if f.startswith("articulate_asset")]
    undone = [i for i, f in enumerate(fixes) if f.startswith("unarticulated")]
    if done and not (undone and undone[-1] > done[-1]):
        return "already articulated"
    prior = _load_priors_fresh().get(class_of(e) or "", {})
    # what it should do needs joints too: a class with behaviors (a wheelchair
    # drives its wheels) is drafted like one with mechanism templates
    if not (needs_articulation(e.get("report", {})) or prior.get("mechanism_templates") or prior.get("behaviors")) \
            or prior.get("deformable"):
        return "rigid: nothing to articulate"
    if dry:
        return "would draft"
    notes = []
    if any("baked" in c.get("message", "") for c in e.get("report", {}).get("callouts", [])):
        notes.append(segment_entry(asset_id))
    try:
        draft = draft_articulation(entry_of(asset_id))
    except Exception as ex:  # noqa: BLE001 - a drafter's refusal is a finding
        # a refusal that a split answers is retried once: keys merged into one
        # mesh (a keyboard), or parts fused into one (a drone's props)
        from segment_mesh import key_split_entry
        split = (key_split_entry if "nothing to press" in str(ex)
                 else segment_entry if "segment the mesh first" in str(ex) and not notes else None)
        if split is None:
            return "; ".join(notes + [f"draft failed: {str(ex)[:160]}"])
        notes.append(split(asset_id))
        try:
            draft = draft_articulation(entry_of(asset_id))
        except Exception as ex2:  # noqa: BLE001
            return "; ".join(notes + [f"draft failed: {str(ex2)[:160]}"])
    notes.append(draft[:200])
    e = entry_of(asset_id)
    spec = json.loads(e.get("articulation_draft") or "{}")
    if "naive fallback" in draft or any("|" in str(j.get("joint_type", "")) for j in spec.get("joints", [])):
        return "; ".join(notes + ["left for the reviewer (no confident draft)"])
    tier = (spec.get("_analysis") or {}).get("tier")
    seen = " ".join((e.get("vlm") or {}).get("visible_moving_parts") or []).lower()
    if not tier and not any(w in seen for w in ("wheel", "caster", "castor", "roller")):
        # the generic drafter's joints are wheels; it found "wheels" on a Rubik's
        # cube and a wrench board. Applied only where the VLM saw wheels too.
        return "; ".join(notes + ["generic draft, and the VLM saw no wheels: left for the reviewer"])
    if not any(j.get("joint_type") in ("revolute", "prismatic") for j in spec.get("joints", [])):
        # only fixed joints (the generic drafter found no wheel, hinge or slide)
        # is no articulation: leave the rigid body as it is
        return "; ".join(notes + ["no moving joint found: left rigid for the reviewer"])
    try:
        notes.append(apply_articulation(e, e["articulation_draft"]))
    except Exception as ex:  # noqa: BLE001
        notes.append(f"apply failed: {str(ex)[:160]}")
    return "; ".join(notes)


# --- 8: verify ---------------------------------------------------------------------

def animate(asset_id: str) -> str:
    """One Kit at a time: queue on the machine's Isaac slot."""
    summary = REPO / "workspace" / "asset_animations" / asset_id / "summary.json"
    summary.unlink(missing_ok=True)          # an earlier run's results must not pass for this one's
    cmd = (f"source {REPO}/scripts/isaac_slot.sh >/dev/null; "
           f"timeout 900 {ISAAC_PYTHON} {REPO}/scripts/animate_asset.py {asset_id} --seconds-per-joint 3")
    rc = subprocess.run(["bash", "-c", cmd], cwd=REPO, capture_output=True, text=True).returncode
    if not summary.exists():
        return f"animation failed (exit {rc})"
    joints = json.loads(summary.read_text())["joints"]

    def reached(v):
        if v.get("unlimited"):            # no stops: it turned a sweep each way
            return v["measured_range"][0] <= 0.9 * v["limits"][0] and v["measured_range"][1] >= 0.9 * v["limits"][1]
        far = v["limits"][1] if abs(v["limits"][1]) >= abs(v["limits"][0]) else v["limits"][0]
        got = v["measured_range"][1] if far > 0 else v["measured_range"][0]
        return abs(got - far) <= 0.1 * abs(far) + 1e-4

    moving = {k: v for k, v in joints.items() if not v.get("follower")}
    ok = sum(1 for v in moving.values() if reached(v))
    note = f"{ok}/{len(moving)} joints reached their travel in PhysX"
    from asset_review_hub import save_queue_entry
    from processing import record
    e = entry_of(asset_id)
    record(e, "verify", joints_reached=f"{ok}/{len(moving)}")
    save_queue_entry(e)
    beh = json.loads(summary.read_text()).get("behaviors") or {}
    runs = [(k, s, r) for k, v in beh.items() for s, r in v.items()]
    if runs:
        good = sum(1 for _, _, r in runs if r.get("ok"))
        note += f"; behaviors {good}/{len(runs)} scenarios as the law says" + "".join(
            f" ({k} {s} FAILED)" for k, s, r in runs if not r.get("ok"))
    # reaching the travel is not moving right: a vision judge looks at the motion
    if os.environ.get("ANTHROPIC_API_KEY"):
        from motion_critic import critique

        try:
            r = critique(asset_id)
            bad = [k for k, v in r["joints"].items() if not v.get("motion_ok")]
            note += ("; motion critic: PASS" if r["pass"] else
                     f"; motion critic: FAIL on {', '.join(bad[:4])} ({r['joints'][bad[0]].get('problem', '')[:120]})")
        except Exception as ex:  # noqa: BLE001
            note += f"; motion critic: not run ({str(ex)[:80]})"
    return note


def soft_test(asset_id: str) -> str:
    e = entry_of(asset_id)
    dtype = e.get("deformable")
    if not dtype:
        return "not soft"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "LD_LIBRARY_PATH")}
    run = lambda *a: subprocess.run([NEWTON_PYTHON, *a], cwd=REPO, capture_output=True, text=True, env=env)  # noqa: E731
    if dtype == "cloth":
        run("scripts/cloth_proxy.py", asset_id)
        out = run("scripts/verify_asset_newton.py", "drape", asset_id).stdout
    elif dtype == "rope":
        out = run("scripts/verify_asset_newton.py", "cable", asset_id).stdout
    else:
        out = run("scripts/verify_asset_newton.py", "squish", asset_id).stdout
    lines = [ln for ln in out.splitlines() if ln.startswith(("PASS", "FAIL", "ERROR"))]
    verdict = lines[-1] if lines else "no verdict"
    from asset_review_hub import save_queue_entry
    from processing import record
    e = entry_of(asset_id)
    record(e, "soft", kind=dtype, verdict=verdict[:120])
    save_queue_entry(e)
    return verdict


# --- the run ---------------------------------------------------------------------

def run(args) -> dict:
    downloads, library_root = Path(args.downloads).expanduser(), Path(args.library).expanduser()
    report = {"started": datetime.now().isoformat(timespec="seconds"), "downloads": str(downloads),
              "library": str(library_root), "dry_run": args.dry_run, "files": [], "assets": {}}
    files = survey(downloads, library_root)
    report["files"] = files
    for f in files:
        print(f"{f['kind']:11s} {Path(f['file']).name}" + (f"  = {Path(f['same_as']).name}" if "same_as" in f else ""))
    # 2. dedupe
    if args.delete_duplicates:
        for f in files:
            if f["kind"] in ("in_library", "copy") and not args.dry_run:
                p, twin = Path(f["file"]), Path(f["same_as"])
                if twin.exists() and same_bytes(p, twin):
                    p.unlink()
                    f["deleted"] = True
    # 3. stage
    incoming = library_root / "_incoming"
    staged = []
    for f in files:
        if f["kind"] != "new":
            continue
        target = free_name(incoming, Path(f["file"]).name, f["sha1"])
        f["staged_as"] = str(target)
        if not args.dry_run:
            incoming.mkdir(parents=True, exist_ok=True)
            shutil.move(f["file"], str(target))
        staged.append(target)
    if args.dry_run:
        print(f"\n(dry run) {len(staged)} new, nothing moved")
        return report
    process(staged, library_root, args, report, incoming)
    return report


def process(staged: list[Path], library_root: Path, args, report: dict, incoming: Path | None = None) -> None:
    """Steps 4-8 for files already in the library tree."""
    # 4. ingest
    ids = []
    for path in staged:
        try:
            asset_id, verdict = ingest(path)
        except Exception as ex:  # noqa: BLE001 - one bad file is a finding, not the end of the run
            asset_id, verdict = None, f"ingest failed: {type(ex).__name__}: {str(ex)[:200]}"
        print(f"ingest     {path.name}: {asset_id or 'skipped'} {verdict}")
        if asset_id:
            ids.append(asset_id)
            report["assets"][asset_id] = {"source": path.name, "ingest": verdict}
        else:
            report["assets"][path.name] = {"skipped": verdict}
    # 5. classify
    if not args.no_vlm:
        from vlm_classify import classify_entry
        for a in ids:
            try:
                note = classify_entry(a)
            except Exception as ex:  # noqa: BLE001
                note = f"VLM failed: {str(ex)[:160]}"
            report["assets"][a]["vlm"] = note
            print(f"classify   {note[:160]}")
    # 6. file
    for line in file_assets(ids, library_root, dry=False):
        print(f"file       {line}")
    if incoming is not None:
        try:
            incoming.rmdir()
        except OSError:
            pass
    for a in ids:
        e = entry_of(a)
        report["assets"][a].update({"class": class_of(e), "file": e.get("original_file") or e["file"]})
    # 7. articulate
    if not args.no_articulate:
        for a in ids:
            note = articulate(a, dry=False)
            report["assets"][a]["articulation"] = note
            print(f"articulate {a}: {note[:200]}")
    # 7b. what it should do: its class's behaviors and the functions the VLM
    # saw, against what the draft can do (behaviors.check) - like scale and
    # materials, a finding for the reviewer when it falls short
    from behaviors import check as behavior_check
    for a in ids:
        e = entry_of(a)
        bc = behavior_check(e)
        if bc:
            e["behavior_check"] = bc
            from asset_review_hub import save_queue_entry
            from processing import record
            record(e, "behaviors", ok=all(v.get("ok") for v in bc.values()))
            save_queue_entry(e)
            report["assets"][a]["behaviors"] = bc
            short = {k: v["missing"] for k, v in bc.items() if not v["ok"]}
            print(f"behaviors  {a}: " + ("all met" if not short else "; ".join(
                f"{k} lacks {', '.join(m) or 'a draft'}" for k, m in short.items())))
    # 8. verify
    for a in ids:
        e = entry_of(a)
        if args.animate and any(f.startswith("articulate_asset") for f in e.get("applied_fixes", [])):
            report["assets"][a]["physx"] = animate(a)
            print(f"animate    {a}: {report['assets'][a]['physx']}")
        if args.soft and e.get("deformable"):
            report["assets"][a]["soft"] = soft_test(a)
            print(f"soft       {a}: {report['assets'][a]['soft']}")


def unprocessed(library_root: Path) -> list[Path]:
    """Library USD files no queue entry has, by content."""
    from ingest_asset import content_index

    from ingest_asset import QUEUE_DIR

    known = content_index()
    # entries from before content hashes were recorded are known by path
    known_paths = set()
    for qf in QUEUE_DIR.glob("*.json"):
        try:
            e = json.loads(qf.read_text())
        except (OSError, ValueError):
            continue
        for k in ("file", "original_file"):
            if e.get(k):
                known_paths.add(str(Path(e[k]).resolve()))
    # ...and their CONTENT is known too: a renamed copy of one is not new
    for kp in known_paths:
        q = Path(kp)
        if q.suffix.lower() in ASSET_EXTS and library_root in q.parents and q.exists():
            known.setdefault(sha1(q), kp)
    out = []
    for p in sorted(library_root.rglob("*")):
        if p.suffix.lower() in ASSET_EXTS and p.is_file() and is_usd(p) and "_incoming" not in p.parts:
            if str(p.resolve()) not in known_paths and sha1(p) not in known:
                out.append(p)
    return out


def resume(args) -> dict:
    """Finish a run that stopped: every USD file still in <library>/_incoming
    goes through steps 4-8 (one already ingested from there is re-ingested)."""
    library_root = Path(args.library).expanduser()
    incoming = library_root / "_incoming"
    todo = sorted(p for p in incoming.glob("*") if p.suffix.lower() in (".usd", ".usda", ".usdc", ".usdz")
                  and is_usd(p)) if incoming.exists() else []
    report = {"started": datetime.now().isoformat(timespec="seconds"), "mode": "resume",
              "library": str(library_root), "dry_run": args.dry_run, "files": [str(p) for p in todo], "assets": {}}
    print(f"{len(todo)} file(s) left in {incoming}")
    if not args.dry_run and todo:
        process(todo, library_root, args, report, incoming)
    return report


def backfill(args) -> dict:
    library_root = Path(args.library).expanduser()
    todo = unprocessed(library_root)
    report = {"started": datetime.now().isoformat(timespec="seconds"), "mode": "backfill",
              "library": str(library_root), "dry_run": args.dry_run, "files": [str(p) for p in todo], "assets": {}}
    for p in todo:
        print(f"unprocessed {p.relative_to(library_root)}")
    if args.dry_run or not todo:
        print(f"\n{len(todo)} library file(s) without a sim-ready entry" + (" (dry run)" if args.dry_run else ""))
        return report
    process(todo[: args.limit or None], library_root, args, report)
    return report


def refile(args) -> int:
    """Re-file library assets by the folder map (those already in folders)."""
    from ingest_asset import QUEUE_DIR

    library_root = Path(args.library).expanduser()
    ids = []
    for qf in QUEUE_DIR.glob("*.json"):
        e = json.loads(qf.read_text())
        src = Path(e.get("original_file") or e.get("file") or "")
        if library_root in src.parents and src.parent != library_root:
            ids.append(e["asset_id"])
    for line in file_assets(sorted(ids), library_root, dry=args.dry_run):
        print(line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--downloads", default="~/Downloads")
    ap.add_argument("--library", default="~/Desktop/assets/SketchFab_Assets")
    ap.add_argument("--delete-duplicates", action="store_true",
                    help="delete downloads byte-identical to a library file (or to another download)")
    ap.add_argument("--dry-run", action="store_true", help="survey and plan only; move and write nothing")
    ap.add_argument("--no-vlm", action="store_true")
    ap.add_argument("--no-articulate", action="store_true")
    ap.add_argument("--animate", action="store_true", help="verify articulated assets in PhysX (slow)")
    ap.add_argument("--soft", action="store_true", help="drape/squish soft assets in Newton")
    ap.add_argument("--refile", action="store_true", help="re-file library assets by the folder map")
    ap.add_argument("--resume", action="store_true",
                    help="finish a run that stopped: process every USD file left in <library>/_incoming")
    ap.add_argument("--backfill", action="store_true",
                    help="process library files that have no sim-ready entry yet (in place)")
    ap.add_argument("--limit", type=int, default=0, help="with --backfill: at most this many")
    args = ap.parse_args()
    try:
        import pxr  # noqa: F401
    except ImportError:
        print("error: pxr not importable - run through scripts/process_downloads.sh", file=sys.stderr)
        return 1
    if args.refile:
        return refile(args)
    report = backfill(args) if args.backfill else resume(args) if args.resume else run(args)
    runs = REPO / "workspace" / "review_queue" / "_runs"
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / f"downloads_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    if report.get("mode") == "backfill":
        print(f"{len(report['assets'])} processed; report: {out.relative_to(REPO)}")
        return 0
    new = sum(1 for f in report["files"] if f["kind"] == "new")
    print(f"\n{new} new, {sum(1 for f in report['files'] if f.get('deleted'))} duplicates deleted, "
          f"{len(report['assets'])} queued; report: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
