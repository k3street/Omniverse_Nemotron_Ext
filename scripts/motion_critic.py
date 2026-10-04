#!/usr/bin/env python3
"""Motion critic: does an articulated asset move the way the real object does?

animate_asset checks that each joint REACHES its travel in PhysX; it cannot
tell whether that travel is the right motion. A nail clipper's lever drafted
as a scissor pivot swings flat across the body, reaches its 30 degrees, and
passes. This critic looks at the animation the way a reviewer would: for
each moving joint (a few of each kind on a keyboard's hundred keys), the
frame at rest beside the frame where that joint is furthest from rest, with
the object's name, class and the parts the classifying VLM said move. The
judge answers, per joint, whether that is how that part of that object
moves - the right part, about the right axis, the right way, nothing
passing through or coming off - and if not, what the motion should be.

Fail-closed like visual_qa: a joint the judge could not see, or any judge
error, is not a pass. Evidence goes to the queue entry under "motion_qa".

    python scripts/motion_critic.py <asset_id> [...] [--judge claude|cosmos|gemma]

Needs workspace/asset_animations/<id>/ (frames/, joints.csv, summary.json)
from scripts/animate_asset.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ANIM_DIR = REPO / "workspace" / "asset_animations"
QUEUE_DIR = REPO / "workspace" / "review_queue"
PER_KIND = 3          # joints judged per kind (button, rotor, pivot ...) when there are many


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "moving_part": {"type": "string", "description": "which part of the object moves between the frames"},
            "motion_seen": {"type": "string", "description": "how it moves: about/along what, which way"},
            "motion_ok": {"type": "boolean", "description": "is that how this part of this object really moves?"},
            "problem": {"type": "string", "description": "what is wrong, or empty"},
            "expected_motion": {"type": "string", "description": "how this part should move on the real object"},
            "confidence": {"type": "number", "description": "0..1"},
        },
        "required": ["moving_part", "motion_seen", "motion_ok", "problem", "expected_motion", "confidence"],
        "additionalProperties": False,
    }


def _prompt(entry: dict, joint: str, info: dict, value: float) -> str:
    vlm = entry.get("vlm") or {}
    unit = "deg" if info.get("type") == "revolute" else "m"
    return (
        f"These two frames are from a physics simulation of a 3D asset: {vlm.get('object_name') or entry['asset_id']}"
        f" (class {entry.get('class_hint') or entry.get('report', {}).get('matched_class')}). "
        f"Parts a classifier expected to move: {', '.join(vlm.get('visible_moving_parts') or []) or 'none listed'}.\n"
        f"Frame 1: the asset at rest. Frame 2: its {info.get('type')} joint '{joint}' driven to {value:.3g} {unit} "
        "(other joints at rest; the camera does not move).\n"
        "Judge the MOTION, not the model's looks: is the part that moved the part that moves on the real object, "
        "about (or along) the right axis, the right way, through a plausible range - without passing through "
        "another part, detaching, or the whole object moving instead? Parts joined at the pivot must stay joined "
        "there: a jaw or head pulling away from its mate means the pivot is in the wrong place. "
        "If you cannot see a difference, say so and "
        "answer motion_ok false. A red ring marks the joint's pivot where one is drawn: the part turns about it.\n"
        + measured_motion(entry, joint)
    )


def measured_motion(entry: dict, joint: str) -> str:
    """What the joint does, in the object's own terms, from its authored axis:
    one oblique view cannot tell a lever lifting from one swinging flat (a
    nail clipper's drafted as a scissor pivot fooled the judge)."""
    try:
        spec = json.loads(entry.get("articulation_draft") or "{}")
        j = next(x for x in spec.get("joints", []) if x["name"] == joint)
        dims = None
    except (StopIteration, ValueError):
        return ""
    axis = "XYZ".index(j.get("axis", "Z")) if j.get("axis") in ("X", "Y", "Z") else None
    if axis is None:
        return ""
    lo = None
    if dims is None:
        from pxr import Usd, UsdGeom

        st = Usd.Stage.Open(entry["file"])
        r = UsdGeom.BBoxCache(0, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]).ComputeWorldBound(
            st.GetPseudoRoot()).ComputeAlignedRange()
        dims, lo = list(r.GetSize()), list(r.GetMin())
    long_axis = max((0, 1), key=lambda k: dims[k])         # the length, lying on the ground
    where = ""
    if j.get("anchor") and lo is not None:
        f = (j["anchor"][long_axis] - lo[long_axis]) / max(dims[long_axis], 1e-9)
        f = min(max(f, 0.0), 1.0)
        where = (f" The pivot is {100 * min(f, 1 - f):.0f}% of the object's length from one END"
                 if min(f, 1 - f) < 0.2 else f" The pivot is {100 * f:.0f}% along the object's length, in its MIDDLE part")
        where += ("; check it is where this part really hinges (a rivet, a pin, a hinge), not at the wrong end."
                  if j["joint_type"] == "revolute" else ".")
    if j["joint_type"] == "revolute":
        what = {2: "a VERTICAL axis (perpendicular to the ground): the part swings SIDEWAYS, flat in the "
                   "ground plane - it does not lift or tilt",
                long_axis: "the object's LENGTH: the part rolls/twists about it",
                }.get(axis, "a horizontal axis ACROSS the object's length: the part's far end lifts up or dips down")
        return f"Measured from the joint (not from the image): it turns about {what}.{where}"
    what = {2: "VERTICALLY (up/down)", long_axis: "ALONG the object's length"}.get(axis, "SIDEWAYS, across the length")
    return f"Measured from the joint (not from the image): the part slides {what}.{where}"


def _frames(asset_id: str):
    d = ANIM_DIR / asset_id
    summary = json.loads((d / "summary.json").read_text())
    rows = list(csv.DictReader(open(d / "joints.csv")))
    frames = sorted((d / "frames").glob("f*.png"))
    return summary, rows, frames


def _pick_joints(summary: dict) -> list[str]:
    """Every moving joint, or PER_KIND of each kind when there are many."""
    moving = [k for k, v in summary["joints"].items() if not v.get("follower")]
    if len(moving) <= 8:
        return moving
    kinds: dict[str, list[str]] = {}
    for k in moving:
        kinds.setdefault(k.rstrip("0123456789_"), []).append(k)
    out = []
    for names in kinds.values():
        step = max(1, len(names) // PER_KIND)
        out += names[::step][:PER_KIND]
    return out


def project(p, cam: dict, frame: int):
    """A world point to pixel (x, y) through animate_asset's logged camera."""
    import math

    import numpy as np

    eye, target = (np.array(v, float) for v in cam["frames"][frame])
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, cam.get("up", [0, 0, 1]))
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    d = np.array(p, float) - eye
    z = d @ f
    if z <= 0:
        return None
    t = math.tan(math.radians(cam["hfov_deg"]) / 2)
    w, h = cam["width"], cam["height"]
    return (w / 2 * (1 + (d @ r) / (z * t)), h / 2 * (1 - (d @ u) / (z * t * h / w)))


def _mark(img, xy, scale):
    from PIL import ImageDraw

    if xy is None:
        return img
    x, y = xy[0] * scale[0], xy[1] * scale[1]
    g = ImageDraw.Draw(img)
    for rad, col in ((14, (255, 255, 255)), (12, (230, 20, 20)), (11, (230, 20, 20))):
        g.ellipse((x - rad, y - rad, x + rad, y + rad), outline=col, width=3)
    g.text((x + 16, y - 8), "pivot", fill=(230, 20, 20))
    return img


def _side_by_side(a: Path, b: Path, out: Path, marks=(None, None), cam=None) -> Path:
    from PIL import Image

    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if cam:
        sc = (ia.width / cam["width"], ia.height / cam["height"])
        ia, ib = _mark(ia, marks[0], sc), _mark(ib, marks[1], sc)
    img = Image.new("RGB", (ia.width + ib.width, max(ia.height, ib.height)), "white")
    img.paste(ia, (0, 0))
    img.paste(ib, (ia.width, 0))
    img.save(out)
    return out


def judge(image: Path, prompt: str, which: str) -> dict:
    import visual_qa as vq

    if which == "claude":
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-5", max_tokens=16000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": vq._b64(str(image))}},
                {"type": "text", "text": prompt}]}],
            output_config={"format": {"type": "json_schema", "schema": _schema()}},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined")
        return json.loads(next(b.text for b in response.content if b.type == "text"))
    if which == "gemma":
        out = vq._post_json(f"{vq.OLLAMA_URL}/api/chat", {
            "model": vq.GEMMA_MODEL, "stream": False, "format": _schema(), "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt, "images": [vq._b64(str(image))]}]})
        return json.loads(out["message"]["content"])
    out = vq._post_json(f"{vq.COSMOS_URL}/chat/completions", {
        "model": vq.COSMOS_MODEL, "temperature": 0, "max_tokens": 1024,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{vq._b64(str(image))}"}},
            {"type": "text", "text": prompt + "\n\nAnswer as JSON with keys " + ", ".join(_schema()["required"])}]}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "motion_qa", "schema": _schema()}}})
    return vq._extract_json(out["choices"][0]["message"]["content"])


def critique(asset_id: str, which: str = "claude") -> dict:
    entry = json.loads((QUEUE_DIR / f"{asset_id}.json").read_text())
    summary, rows, frames = _frames(asset_id)
    out_dir = ANIM_DIR / asset_id / "motion_qa"
    out_dir.mkdir(exist_ok=True)
    verdicts = {}
    for name in _pick_joints(summary):
        col = f"{name}_meas"
        vals = [abs(float(r[col])) if r.get(col) not in (None, "") else 0.0 for r in rows]
        if not vals or max(vals) == 0.0:
            verdicts[name] = {"motion_ok": False, "problem": "the joint never moved in the animation"}
            continue
        n = min(len(vals), len(frames))
        signed = [float(rows[i][col]) if rows[i].get(col) not in (None, "") else 0.0 for i in range(n)]
        # each way the joint went (pliers open AND close), if it went far that way
        extremes = [max(range(n), key=lambda i: signed[i]), min(range(n), key=lambda i: signed[i])]
        top = max(abs(signed[i]) for i in extremes)
        extremes = [i for i in extremes if abs(signed[i]) >= 0.15 * top]
        cam, pivot = summary.get("camera"), summary["joints"][name].get("pivot_world")
        for k, far in enumerate(extremes):
            key = name if k == 0 else f"{name} (other way)"
            marks = (project(pivot, cam, 0), project(pivot, cam, far)) if cam and pivot and \
                summary["joints"][name].get("type") == "revolute" else (None, None)
            image = _side_by_side(frames[0], frames[far], out_dir / f"{name}{'_b' if k else ''}.png", marks,
                                  cam if marks[0] else None)
            try:
                v = judge(image, _prompt(entry, name, summary["joints"][name], signed[far]), which)
            except Exception as ex:  # noqa: BLE001 - fail closed
                v = {"motion_ok": False, "problem": f"judge error: {str(ex)[:160]}"}
            v["image"] = str(image.relative_to(REPO))
            verdicts[key] = v
    ok = bool(verdicts) and all(v.get("motion_ok") for v in verdicts.values())
    result = {"date": date.today().isoformat(), "judge": which, "pass": ok,
              "joints_judged": len(verdicts), "joints": verdicts}
    entry["motion_qa"] = result
    (QUEUE_DIR / f"{asset_id}.json").write_text(json.dumps(entry, indent=1))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("assets", nargs="+")
    ap.add_argument("--judge", default="claude", choices=("claude", "cosmos", "gemma"))
    args = ap.parse_args()
    for a in args.assets:
        r = critique(a, args.judge)
        bad = {k: v.get("problem") or v.get("expected_motion") for k, v in r["joints"].items() if not v.get("motion_ok")}
        print(f"{a}: {'PASS' if r['pass'] else 'FAIL'} ({r['joints_judged']} joints judged)"
              + "".join(f"\n   {k}: {p}" for k, p in bad.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
