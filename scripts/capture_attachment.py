#!/usr/bin/env python3
"""Capture a HUMAN-corrected cord attachment from the live sim.

Where a cord meets a plug or tool is a property of that asset, and a
person dragging it into place in the viewport knows it better than any
heuristic. This reads the corrected pose out of the running stage,
converts it into the asset's OWN space (so it survives any future
placement), and stores it in workspace/knowledge/cord_attachments.json.
make_cable.attach_frame prefers a stored attachment over its geometric
guess, so the correction is made once and reused forever.

Usage (Isaac running, asset placed and moved into position):
    python scripts/capture_attachment.py \\
        --asset power_plug_european \\
        --prim /World/Kettle/Plug --cord /World/Kettle/Cord [--port 8002]

Move the part in the viewport FIRST. Note that reloading the asset's
layer discards viewport edits — capture before reloading.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "workspace" / "knowledge" / "cord_attachments.json"

PROBE = '''
import json
import omni.usd
from pxr import Usd, UsdGeom, Gf
stage = omni.usd.get_context().get_stage()
cache = UsdGeom.XformCache()
part = stage.GetPrimAtPath("{prim}")
if not part or not part.IsValid():
    print(json.dumps({{"error": "prim not found: {prim}"}}))
else:
    m = cache.GetLocalToWorldTransform(part)
    segs = sorted([p for p in Usd.PrimRange(stage.GetPrimAtPath("{cord}"))
                   if p.GetName().startswith("seg_")], key=lambda p: p.GetName())
    if len(segs) < 2:
        print(json.dumps({{"error": "cord segments not found under {cord}"}}))
    else:
        last, prev = segs[-1], segs[-2]
        half = UsdGeom.Capsule(last).GetHeightAttr().Get() / 2.0
        lm = cache.GetLocalToWorldTransform(last)
        tip_a = lm.Transform(Gf.Vec3d(half, 0, 0))
        tip_b = lm.Transform(Gf.Vec3d(-half, 0, 0))
        pw = m.ExtractTranslation()
        # whichever capsule end is nearer the part is the real join
        tip = tip_a if (tip_a - pw).GetLength() <= (tip_b - pw).GetLength() else tip_b
        other = tip_b if tip is tip_a else tip_a
        inv = m.GetInverse()
        local_point = inv.Transform(tip)
        # direction the cord leaves the part, in the part's own space
        d = (tip - other).GetNormalized()
        local_dir = inv.TransformDir(Gf.Vec3d(d)).GetNormalized()
        print(json.dumps({{
            "local_point": [float(v) for v in local_point],
            "local_dir": [float(v) for v in local_dir],
            "gap_m": float((tip - inv.GetInverse().Transform(local_point)).GetLength()),
        }}))
'''


def capture(asset: str, prim: str, cord: str, port: str) -> dict:
    code = PROBE.format(prim=prim, cord=cord)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/exec_sync",
        json.dumps({"code": code}).encode(), {"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=120).read())
    if not out.get("success"):
        raise RuntimeError(f"probe failed: {str(out)[:200]}")
    data = json.loads(out["output"].strip().splitlines()[-1])
    if "error" in data:
        raise RuntimeError(data["error"])

    store = json.loads(STORE.read_text()) if STORE.exists() else {
        "_doc": "Human-corrected cord attachment points, in each asset's "
                "own space (meters). make_cable.attach_frame prefers these "
                "over its geometric guess.", "attachments": {}}
    store["attachments"][asset] = {
        "local_point": [round(v, 6) for v in data["local_point"]],
        "local_dir": [round(v, 6) for v in data["local_dir"]],
        "source": "human", "captured": date.today().isoformat(),
    }
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(store, indent=1))
    return store["attachments"][asset]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asset", required=True,
                    help="asset key, e.g. power_plug_european")
    ap.add_argument("--prim", required=True,
                    help="the moved part, e.g. /World/Kettle/Plug")
    ap.add_argument("--cord", required=True,
                    help="the cord xform, e.g. /World/Kettle/Cord")
    ap.add_argument("--port", default="8002")
    args = ap.parse_args()
    try:
        rec = capture(args.asset, args.prim, args.cord, args.port)
    except Exception as e:
        print(f"capture failed: {str(e)[:200]}")
        return 1
    print(f"captured attachment for {args.asset}:")
    print(f"  point {rec['local_point']}  dir {rec['local_dir']}")
    print(f"  stored in {STORE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
