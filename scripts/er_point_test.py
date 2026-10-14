#!/usr/bin/env python3
"""Ask Gemini Robotics-ER to point at and box the target in a rendered
egocentric scene, and score the answer against the scene's ground truth.

Reads <scene>/render/{head_rgb.png, gt.json, target_mask.png} written by
render_er_scene.py. Two queries, in the model's documented conventions
([y, x] and [ymin, xmin, ymax, xmax], normalised to 0-1000):

  point  "Point to <label>"            scored: lands on the target's visible
                                       mask; pixel distance to the mask centre
  box    "Detect <label>, box_2d"      scored: IoU with the mask's box

    python scripts/er_point_test.py workspace/scenes/<name> [--model gemini-robotics-er-2-preview] [--runs 3]
writes <scene>/render/er_<model>.json and er_<model>_overlay.png
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
API = "https://generativelanguage.googleapis.com/v1beta/models"

POINT_PROMPT = ("Point to {label}. The answer should follow the json format: "
                "[{{\"point\": <point>, \"label\": <label1>}}, ...]. "
                "The points are in [y, x] format normalized to 0-1000.")
PLACE_PROMPT = ("Point to {label} on the chessboard. The answer should follow the json format: "
                "[{{\"point\": <point>, \"label\": <label1>}}, ...]. "
                "The points are in [y, x] format normalized to 0-1000.")
INSTRUCTION_PROMPT = ("{instruction} Point to the piece to pick up and to the place to put it down. "
                      "Answer in json: [{{\"point\": [y, x], \"label\": \"pick\"}}, "
                      "{{\"point\": [y, x], \"label\": \"place\"}}], points normalized to 0-1000.")
BOX_PROMPT = ("Detect {label} in the image. Return a json array with one entry: "
              "[{{\"box_2d\": [ymin, xmin, ymax, xmax], \"label\": <label>}}], "
              "coordinates normalized to 0-1000.")


def _key() -> str:
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(k):
            return os.environ[k]
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, sep, v = line.strip().removeprefix("export ").partition("=")
            if sep and k in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and v.strip():
                return v.strip().strip("'\"")
    raise SystemExit("no GEMINI_API_KEY or GOOGLE_API_KEY (environment or .env)")


def ask(model: str, image: Path, prompt: str, thinking: int) -> tuple[list, str, float]:
    body = {"contents": [{"parts": [
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image.read_bytes()).decode()}},
        {"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                             "thinkingConfig": {"thinkingBudget": thinking}}}
    req = urllib.request.Request(f"{API}/{model}:generateContent", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": _key()})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    dt = time.time() - t0
    text = "".join(p.get("text", "") for p in resp["candidates"][0]["content"]["parts"] if not p.get("thought"))
    m = re.search(r"\[.*\]", text, re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except json.JSONDecodeError:
        data = []
    return (data if isinstance(data, list) else [data]), text, dt


def main() -> int:
    import numpy as np
    from PIL import Image, ImageDraw

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--model", default="gemini-robotics-er-2-preview")
    ap.add_argument("--label", help="how the target is named in the prompt (default: the scene's label)")
    ap.add_argument("--runs", type=int, default=3, help="repeat each query; the answers vary")
    ap.add_argument("--thinking", type=int, default=1024)
    args = ap.parse_args()

    rd = Path(args.scene_dir) / "render"
    gt = json.loads((rd / "gt.json").read_text())
    img = rd / gt["image"]
    W, H = gt["width"], gt["height"]
    mask = np.array(Image.open(rd / "target_mask.png")) > 0
    ids = np.load(rd / "head_instance_ids.npy") if (rd / "head_instance_ids.npy").exists() else None
    paths = json.loads((rd / "head_instance_paths.json").read_text()) if (rd / "head_instance_paths.json").exists() else {}

    def what_is_at(py, pxx):
        """The prim the camera sees at that pixel (a point on the robot's own
        thumb in front of the can is not a point on the can)."""
        if ids is None:
            return None
        v = int(ids[min(H - 1, max(0, int(py))), min(W - 1, max(0, int(pxx)))])
        return paths.get(str(v), str(v))
    t = gt["target"]
    label = args.label or t["label"]
    gy, gx = t["point_px_yx"]
    gb = t["box_px_yxyx"]

    def iou(a, b):
        y0, x0, y1, x1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
        inter = max(0, y1 - y0) * max(0, x1 - x0)
        ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / ua if ua > 0 else 0.0

    dest = gt.get("destination") or {}
    cam = gt.get("camera_world") or {}
    intr = gt.get("intrinsics") or {}

    def board_square_at(py, pxx):
        """The square a pixel looks at: its ray met with the board's plane."""
        grid = dest.get("grid")
        if not grid or not cam:
            return None
        ray_c = np.array([(pxx - intr["cx"]) / intr["fx"], (py - intr["cy"]) / intr["fy"], 1.0])
        ray = np.asarray(cam["R_optical"], float) @ ray_c
        o = np.asarray(cam["position"], float)
        a1 = np.asarray(grid["a1_centre_world"], float)
        fd, rd = np.asarray(grid["file_dir"], float), np.asarray(grid["rank_dir"], float)
        n = np.cross(fd, rd)
        den = float(ray @ n)
        if abs(den) < 1e-9:
            return None
        hit = o + ray * float((a1 - o) @ n) / den
        sq = grid["square_m"]
        f, r = (hit - a1) @ fd / sq, (hit - a1) @ rd / sq
        fi, ri = int(np.floor(f + 0.5)), int(np.floor(r + 0.5))
        name = f"{'abcdefgh'[fi]}{ri + 1}" if 0 <= fi < 8 and 0 <= ri < 8 else "off the board"
        return {"square": name, "from_centre_of_square_m": round(float(np.hypot(f - fi, r - ri) * sq), 4),
                "world": hit.round(4).tolist()}

    def in_poly(pt, poly):
        x, y, inside = pt[0], pt[1], False
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
                inside = not inside
        return inside

    def score_place(p):
        py, pxx = p[0] / 1000 * H, p[1] / 1000 * W
        out = {"point_yx_1000": p, "point_px_yx": [round(py, 1), round(pxx, 1)]}
        if dest.get("polygon_px_xy"):
            cxp, cyp = dest["centre_px_xy"]
            out.update(in_square=in_poly((pxx, py), dest["polygon_px_xy"]),
                       px_from_centre=round(float(np.hypot(pxx - cxp, py - cyp)), 1))
        sq = board_square_at(py, pxx)
        if sq:
            out["landed_on_square"] = sq
        return out

    def score_pick(p):
        py, pxx = p[0] / 1000 * H, p[1] / 1000 * W
        out = {"point_yx_1000": p, "on_target": bool(mask[min(H - 1, max(0, int(py))), min(W - 1, max(0, int(pxx)))]),
               "landed_on": what_is_at(py, pxx), "px_from_centre": round(float(np.hypot(py - gy, pxx - gx)), 1)}
        sq = board_square_at(py, pxx)
        if sq:
            out["board_square_below"] = sq["square"]
        return out

    runs = []
    for i in range(args.runs):
        pts, ptxt, pdt = ask(args.model, img, POINT_PROMPT.format(label=label), args.thinking)
        bxs, btxt, bdt = ask(args.model, img, BOX_PROMPT.format(label=label), args.thinking)
        row = {"run": i, "point_raw": ptxt[:400], "box_raw": btxt[:400], "point_s": round(pdt, 2), "box_s": round(bdt, 2)}
        p = next((q.get("point") for q in pts if isinstance(q, dict) and q.get("point")), None)
        if p and len(p) == 2:
            py, pxx = p[0] / 1000 * H, p[1] / 1000 * W
            on = bool(mask[min(H - 1, max(0, int(py))), min(W - 1, max(0, int(pxx)))])
            y0, x0, y1, x1 = gb
            row.update(point_yx_1000=p, point_px_yx=[round(py, 1), round(pxx, 1)], on_target=on,
                       in_target_box=bool(y0 <= py <= y1 and x0 <= pxx <= x1),
                       landed_on=what_is_at(py, pxx),
                       px_from_centre=round(float(np.hypot(py - gy, pxx - gx)), 1))
        b = next((q.get("box_2d") for q in bxs if isinstance(q, dict) and q.get("box_2d")), None)
        if b and len(b) == 4:
            bp = [b[0] / 1000 * H, b[1] / 1000 * W, b[2] / 1000 * H, b[3] / 1000 * W]
            row.update(box_yxyx_1000=b, box_px_yxyx=[round(v, 1) for v in bp], iou=round(iou(bp, gb), 3))
        if dest.get("label"):
            pts2, ptxt2, pdt2 = ask(args.model, img, PLACE_PROMPT.format(label=dest["label"]), args.thinking)
            q = next((x.get("point") for x in pts2 if isinstance(x, dict) and x.get("point")), None)
            row["place"] = {"raw": ptxt2[:300], "s": round(pdt2, 2), **(score_place(q) if q and len(q) == 2 else {})}
        if gt.get("instruction"):
            pts3, ptxt3, pdt3 = ask(args.model, img, INSTRUCTION_PROMPT.format(instruction=gt["instruction"]), args.thinking)
            pk = next((x.get("point") for x in pts3 if isinstance(x, dict) and "pick" in str(x.get("label", "")).lower()), None)
            pl = next((x.get("point") for x in pts3 if isinstance(x, dict) and "place" in str(x.get("label", "")).lower()), None)
            row["instruction"] = {"raw": ptxt3[:400], "s": round(pdt3, 2),
                                  "pick": score_pick(pk) if pk and len(pk) == 2 else None,
                                  "place": score_place(pl) if pl and len(pl) == 2 else None}
        runs.append(row)
        if "place" in row:
            pl_ = row["place"]
            print(f"run {i}: place point {pl_.get('point_yx_1000')} in the square={pl_.get('in_square')}, "
                  f"lands on {(pl_.get('landed_on_square') or {}).get('square')} ({pl_.get('px_from_centre')} px from centre)", flush=True)
        if row.get("instruction"):
            ins = row["instruction"]
            print(f"run {i}: instruction -> pick {(ins.get('pick') or {}).get('on_target')} "
                  f"({str((ins.get('pick') or {}).get('landed_on'))[-40:]}), place on "
                  f"{((ins.get('place') or {}).get('landed_on_square') or {}).get('square')}", flush=True)
        print(f"run {i}: point {row.get('point_yx_1000')} on the target's visible pixels={row.get('on_target')}, "
              f"in its box={row.get('in_target_box')}, lands on {str(row.get('landed_on'))[-45:]} "
              f"({row.get('px_from_centre')} px from centre); box {row.get('box_yxyx_1000')} IoU {row.get('iou')}", flush=True)

    summary = {"model": args.model, "label": label, "image": str(img), "gt_point_yx_1000": t["point_yx_1000"],
               "gt_box_yxyx_1000": t["box_yxyx_1000"], "runs": runs,
               "point_on_target": sum(1 for r in runs if r.get("on_target")),
               "point_in_target_box": sum(1 for r in runs if r.get("in_target_box")),
               "target_occluded_by": {k: v for k, v in (t.get("box_contents_px") or {}).items()
                                      if "/World/Objects/" + t["name"] not in k and v},
               "mean_px_from_centre": round(float(np.mean([r["px_from_centre"] for r in runs if "px_from_centre" in r])), 1)
               if any("px_from_centre" in r for r in runs) else None,
               "mean_iou": round(float(np.mean([r["iou"] for r in runs if "iou" in r])), 3) if any("iou" in r for r in runs) else None}
    if dest.get("label"):
        summary["destination"] = {"label": dest["label"], "square": dest.get("square"),
                                  "place_in_square": sum(1 for r in runs if (r.get("place") or {}).get("in_square")),
                                  "place_squares": [((r.get("place") or {}).get("landed_on_square") or {}).get("square") for r in runs]}
    if gt.get("instruction"):
        summary["instruction"] = {"text": gt["instruction"],
                                  "pick_on_target": sum(1 for r in runs if ((r.get("instruction") or {}).get("pick") or {}).get("on_target")),
                                  "place_squares": [(((r.get("instruction") or {}).get("place") or {}).get("landed_on_square") or {}).get("square")
                                                    for r in runs]}
    tag = re.sub(r"[^a-z0-9]+", "_", args.model.lower())
    (rd / f"er_{tag}.json").write_text(json.dumps(summary, indent=1))
    ov = Image.open(img).convert("RGB")
    dr = ImageDraw.Draw(ov)
    dr.rectangle([gb[1], gb[0], gb[3], gb[2]], outline=(0, 255, 0), width=3)
    for r in runs:
        if "box_px_yxyx" in r:
            y0, x0, y1, x1 = r["box_px_yxyx"]
            dr.rectangle([x0, y0, x1, y1], outline=(255, 64, 64), width=2)
        if "point_px_yx" in r:
            py, pxx = r["point_px_yx"]
            dr.ellipse([pxx - 7, py - 7, pxx + 7, py + 7], outline=(255, 255, 0), width=3)
    if dest.get("polygon_px_xy"):
        dr.polygon([tuple(q) for q in dest["polygon_px_xy"]], outline=(0, 160, 255), width=3)
    for r in runs:
        for key, colour in (("place", (0, 220, 255)),):
            q = (r.get(key) or {}).get("point_px_yx")
            if q:
                dr.rectangle([q[1] - 6, q[0] - 6, q[1] + 6, q[0] + 6], outline=colour, width=3)
    ov.save(rd / f"er_{tag}_overlay.png")
    print(f"ER_TEST {args.model}: point on the target's visible pixels {summary['point_on_target']}/{len(runs)}, "
          f"inside its box {summary['point_in_target_box']}/{len(runs)}, "
          f"mean {summary['mean_px_from_centre']} px from the mask centre; mean box IoU {summary['mean_iou']}"
          + (f"; {summary['destination']['label']}: in the square {summary['destination']['place_in_square']}/{len(runs)} "
             f"(squares {summary['destination']['place_squares']})" if "destination" in summary else "")
          + (f"; instruction: pick on target {summary['instruction']['pick_on_target']}/{len(runs)}, "
             f"place squares {summary['instruction']['place_squares']}" if "instruction" in summary else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
