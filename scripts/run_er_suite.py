#!/usr/bin/env python3
"""Build, render and score a set of egocentric test scenes against an
embodied-reasoning model, one table at the end.

For each scene spec: build_er_scene (pxr) -> render_er_scene (Kit, on the
machine's Isaac slot, one at a time) -> er_point_test (the model's point and
box, scored against the render's ground truth). A scene whose render does
not pass its own checks (objects off the support, posture not held, target
not seen) is reported and not scored.

    python scripts/run_er_suite.py config/scenes/aida_r5_dining_grasp*.json [--runs 3] [--model ...]
writes workspace/scenes/er_suite_<model>.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
ISAAC_PYTHON = os.environ.get("ISAAC_PYTHON",
                              "/home/kimate/Documents/Github/isaacsim/_build/linux-aarch64/release/python.sh")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--model", default="gemini-robotics-er-2-preview")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--skip-render", action="store_true", help="score the renders already on disk")
    args = ap.parse_args()

    from build_er_scene import build, load_spec

    rows = []
    for sp in args.specs:
        spec = load_spec(sp)
        d = REPO / "workspace" / "scenes" / spec["name"]
        row = {"scene": spec["name"], "purpose": spec.get("purpose", "")[:160]}
        if not args.skip_render:
            build(spec, d)
            (d / "spec.json").write_text(json.dumps(spec, indent=1))
            cmd = (f"source {REPO}/scripts/isaac_slot.sh >/dev/null; "
                   f"timeout 1500 {ISAAC_PYTHON} {REPO}/scripts/render_er_scene.py {d}")
            r = subprocess.run(["bash", "-c", cmd], cwd=REPO, capture_output=True, text=True)
            verdict = next((ln for ln in r.stdout.splitlines() if ln.startswith("ER_SCENE ")),
                           f"ER_SCENE FAIL {spec['name']}: no verdict (exit {r.returncode})")
            row["render"] = verdict[:300]
            print(verdict[:200], flush=True)
            if not verdict.startswith("ER_SCENE OK"):
                rows.append(row)
                continue
        t = subprocess.run([sys.executable, str(REPO / "scripts" / "er_point_test.py"), str(d),
                            "--model", args.model, "--runs", str(args.runs)], capture_output=True, text=True)
        print(t.stdout.strip().splitlines()[-1] if t.stdout.strip() else t.stderr[-300:], flush=True)
        tag = "".join(c if c.isalnum() else "_" for c in args.model.lower())
        res = d / "render" / f"er_{tag}.json"
        if res.exists():
            s = json.loads(res.read_text())
            gt = json.loads((d / "render" / "gt.json").read_text())
            row.update(target_visible_px=gt["target"].get("visible_px"),
                       occluded_by=sorted({k.split("/")[-2] for k in s.get("target_occluded_by", {})
                                           if "/World/Robot/" in k}),
                       runs=len(s["runs"]), point_on_visible=s["point_on_target"], point_in_box=s["point_in_target_box"],
                       mean_px_from_centre=s["mean_px_from_centre"], mean_iou=s["mean_iou"],
                       landed_on=sorted({str(r.get("landed_on")).split("/")[-2] for r in s["runs"] if r.get("landed_on")}))
        rows.append(row)

    out = REPO / "workspace" / "scenes" / f"er_suite_{''.join(c if c.isalnum() else '_' for c in args.model.lower())}.json"
    out.write_text(json.dumps({"model": args.model, "runs_per_query": args.runs, "scenes": rows}, indent=1))
    print(f"\n{'scene':34} {'visible px':>10} {'point on can':>12} {'in box':>7} {'px off':>7} {'box IoU':>8}  lands on")
    for r in rows:
        if "runs" not in r:
            print(f"{r['scene']:34} not scored: {r.get('render', '')[:80]}")
            continue
        print(f"{r['scene']:34} {r['target_visible_px']:>10} {r['point_on_visible']:>9}/{r['runs']} "
              f"{r['point_in_box']:>4}/{r['runs']} {r['mean_px_from_centre']:>7} {r['mean_iou']:>8}  {', '.join(r['landed_on'])}")
    print(f"\nsummary: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
