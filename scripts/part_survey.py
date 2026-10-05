#!/usr/bin/env python3
"""Part survey: what each part of an asset is, what it is made of, and how it
moves - from renders where every part has its own colour and number.

The classifier names the object and what it does (vlm.functions); a drafting
tier knows a few kinds of object by geometry. Neither says, for a new kind
of object, WHICH part is the rocker paddle and what the plate is made of.
This survey renders the asset three ways with each part (mesh) flat-coloured
and numbered at its centre, plus a legend with each part's size, and asks a
vision model, part by part:

  role       what the part is ("rocker paddle", "wall plate", "screw")
  material   its physics material (the materials database's keys): one per
             part - an asset is multi-material (a steel blade, a rubber grip)
  motion     none | hinge | slide | spin | press | detach | flex
  relative_to  the part it moves against (its parent)
  axis       part_long | part_short | part_face_normal | vertical |
             horizontal_long | horizontal_short   (resolved from geometry)
  pivot      center | end_near_parent | end_far_from_parent | top | bottom |
             contact_with_parent                    (resolved from geometry)
  range      degrees (hinge) or millimetres (slide, press)

The answer goes to entry["part_survey"]. materials (apply_part_materials)
and the generic articulation drafter (survey_draft.py) read it.

    python scripts/part_survey.py <asset_id> [...] [--max-parts 24]
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"
MATERIALS = REPO / "workspace" / "knowledge" / "physics_materials.json"
WIDTH = 768
MOTIONS = ["none", "hinge", "slide", "spin", "press", "detach", "flex"]
AXES = ["part_long", "part_short", "part_face_normal", "vertical", "horizontal_long", "horizontal_short"]
PIVOTS = ["center", "end_near_parent", "end_far_from_parent", "top", "bottom", "contact_with_parent"]
# 24 colours far apart in hue and lightness (tab20 + 4)
PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
           "#bcbd22", "#17becf", "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5", "#c49c94",
           "#f7b6d2", "#000000", "#dbdb8d", "#9edae5", "#ffd700", "#00ff7f", "#8b0000", "#00008b"]


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def labelled_parts(stage, root: str, max_parts: int):
    """The parts to label: the biggest max_parts meshes; smaller ones are
    folded into the labelled part they sit closest to."""
    from articulation_draft import collect_parts

    parts = sorted([p for p in collect_parts(stage, root) if max(p["size"]) > 0], key=lambda p: -p["volume"])
    keep, rest = parts[:max_parts], parts[max_parts:]
    for i, p in enumerate(keep):
        p["id"], p["members"] = i + 1, [p["path"]]
    for q in rest:
        host = min(keep, key=lambda p: np.linalg.norm(np.array(p["centroid"]) - q["centroid"]))
        host["members"].append(q["path"])
    return keep


def coloured_layer(entry: dict, parts: list, root: str) -> Path:
    """A layer over the asset with each labelled part (and what is folded into
    it) bound to one flat colour. The asset file is not touched."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

    out = QUEUE / "thumbs" / f"{entry['asset_id']}__parts.usda"
    out.parent.mkdir(parents=True, exist_ok=True)
    layer = Sdf.Layer.FindOrOpen(str(out))
    if layer:
        layer.Clear()
    else:
        layer = Sdf.Layer.CreateNew(str(out))
    layer.subLayerPaths.append(entry["file"])
    stage = Usd.Stage.Open(layer)
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    # the layer over the asset carries the asset's own up axis and units: left
    # out, the renderer takes Y up and lays a Z-up model on its side (a toaster
    # read as lying down, a rocker's axis judged in a tipped-over view)
    src = Usd.Stage.Open(entry["file"])
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(src))
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(src))
    for p in parts:
        col = Gf.Vec3f(*_rgb(PALETTE[(p["id"] - 1) % len(PALETTE)]))
        mat = UsdShade.Material.Define(stage, f"/World/PartColors/C{p['id']:02d}")
        sh = UsdShade.Shader.Define(stage, f"/World/PartColors/C{p['id']:02d}/S")
        sh.CreateIdAttr("UsdPreviewSurface")
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(col)
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.8)
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        for path in p["members"]:
            for prim in Usd.PrimRange(stage.GetPrimAtPath(path)):
                if prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Subset):
                    UsdShade.MaterialBindingAPI.Apply(prim).Bind(
                        mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants)
                if prim.IsA(UsdGeom.Mesh):
                    UsdGeom.Mesh(prim).CreateDisplayColorAttr([col])
    layer.Save()
    return out


def _project(points, center, radius, az_deg, z_up, width):
    """Pixel positions of world points in ingest_asset._orbit_render's view."""
    from pxr import Gf, Usd, UsdGeom

    from ingest_asset import ELEVATION_DEG, FRAME_MARGIN

    tmp = Usd.Stage.CreateInMemory()          # held: the camera's defaults are read from it
    cam = UsdGeom.Camera.Define(tmp, "/C")
    fl = cam.GetFocalLengthAttr().Get()
    ha, va = cam.GetHorizontalApertureAttr().Get(), cam.GetVerticalApertureAttr().Get()
    v_half = math.atan(0.5 * va / fl)
    dist = FRAME_MARGIN * radius / math.sin(v_half)
    az, el = math.radians(az_deg), math.radians(ELEVATION_DEG)
    off = (np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)]) if z_up else
           np.array([math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)]))
    eye = np.array(center) + dist * off
    up = np.array([0, 0, 1.0]) if z_up else np.array([0, 1.0, 0])
    fwd = (np.array(center) - eye) / np.linalg.norm(np.array(center) - eye)
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    cup = np.cross(right, fwd)
    height = width * va / ha
    out = []
    for p in points:
        d = np.array(p) - eye
        z = d @ fwd
        x, y = (d @ right) / z * fl / ha, (d @ cup) / z * fl / va
        out.append((width * (0.5 + x), height * (0.5 - y)))
    return out, (width, int(round(height)))


def visible_spots(img, parts, min_px: int = 40) -> dict:
    """Where each part's number goes in a part-coloured view: the deepest
    point of the pixels in its own colour. A number at the projected centroid
    lands on whatever is in front of the part (the K-Slim's back water tank
    was numbered on the pod lid, and read as the lid). Parts are matched by
    colour direction (diffuse shading scales a flat colour); near-grey
    colours cannot be told from shading and keep the centroid. "_chroma":
    the parts told by colour (one missing from the spots is not in view)."""
    px = img.reshape(-1, 3).astype(float) / 255.0
    cols = {p["id"]: np.array(_rgb(PALETTE[(p["id"] - 1) % len(PALETTE)])) for p in parts}
    chroma = {i: c for i, c in cols.items() if c.max() - c.min() > 0.25}
    if not chroma:
        return {}
    ids = list(chroma)
    # a lit flat colour renders as s * colour + w * white (the dome washes it
    # toward white): fit s, w >= 0 per pixel and colour; the part is the
    # colour that fits best
    err = np.full((len(px), len(ids)), np.inf)
    for k, i in enumerate(ids):
        A = np.stack([chroma[i], np.ones(3)], 1)
        sw = px @ np.linalg.pinv(A).T
        sw = np.maximum(sw, 0.0)
        fit = sw @ A.T
        e = np.linalg.norm(px - fit, axis=1)
        e[sw[:, 0] < 0.25] = np.inf          # mostly white or dark: not this colour
        err[:, k] = e
    best = err.argmin(1)
    ok = err.min(1) < 0.035
    h, w = img.shape[:2]
    spots = {"_chroma": set(ids)}
    for k, i in enumerate(ids):
        m = (ok & (best == k)).reshape(h, w)
        if m.sum() < min_px:
            continue
        # erode until one more step would empty it: the part's thickest place
        core = m
        while True:
            e = core.copy()
            e[1:] &= core[:-1]; e[:-1] &= core[1:]; e[:, 1:] &= core[:, :-1]; e[:, :-1] &= core[:, 1:]
            if e.sum() < 4:
                break
            core = e
        ys, xs = np.nonzero(core)
        cy, cx = ys.mean(), xs.mean()
        n = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
        spots[i] = (float(xs[n]), float(ys[n]))
    return spots


def render(entry: dict, parts: list, root: str) -> list[str]:
    """Three part-coloured views with each part's number at its centre, and a
    legend: number, colour, size."""
    from PIL import Image, ImageDraw
    from pxr import Usd, UsdGeom

    from ingest_asset import THREE_QUARTER_AZ_DEG, _orbit_render

    layer = coloured_layer(entry, parts, root)
    azs = [THREE_QUARTER_AZ_DEG, THREE_QUARTER_AZ_DEG + 120.0, THREE_QUARTER_AZ_DEG + 240.0]
    frames = _orbit_render(str(layer), f"{entry['asset_id']}__parts", azs, WIDTH)
    if not frames:
        raise RuntimeError("the part render produced no image (usdrecord)")
    st = Usd.Stage.Open(entry["file"])
    rng = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
                            ).ComputeWorldBound(st.GetPseudoRoot()).ComputeAlignedRange()
    center, radius = list(rng.GetMidpoint()), 0.5 * rng.GetSize().GetLength() or 0.5
    z_up = str(UsdGeom.GetStageUpAxis(st)).upper() == "Z"
    out, seen = [], set()
    for f, az in zip(frames, azs):
        img = Image.open(f).convert("RGB")
        xy, _ = _project([p["centroid"] for p in parts], center, radius, az, z_up, img.width)
        spots = visible_spots(np.asarray(img), parts)
        g = ImageDraw.Draw(img)
        for p, (x, y) in zip(parts, xy):
            if p["id"] in spots:
                x, y = spots[p["id"]]
            elif p["id"] in spots.get("_chroma", ()):
                continue        # not seen from this side: no number on whatever is in front of it
            seen.add(p["id"])
            t = str(p["id"])
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)):
                g.text((x - 4 + dx, y - 6 + dy), t, fill=(255, 255, 255))
            g.text((x - 4, y - 6), t, fill=(0, 0, 0))
        img.save(f)
        out.append(f)
    # the legend: number, colour, size in mm
    rows = len(parts)
    leg = Image.new("RGB", (WIDTH, 22 * rows + 10), "white")
    g = ImageDraw.Draw(leg)
    for i, p in enumerate(parts):
        y = 5 + 22 * i
        g.rectangle((8, y, 30, y + 16), fill=PALETTE[(p["id"] - 1) % len(PALETTE)], outline="black")
        s = " x ".join(f"{v * 1000:.0f}" for v in p["size"])
        g.text((40, y + 2), f"#{p['id']}  {s} mm" + (f"  (+{len(p['members']) - 1} small parts)"
                                                    if len(p["members"]) > 1 else "")
               + ("" if p["id"] in seen else "  - hidden inside, not seen in any view"), fill=(0, 0, 0))
    for p in parts:
        p["seen"] = p["id"] in seen
    lf = QUEUE / "thumbs" / f"{entry['asset_id']}__parts_legend.png"
    leg.save(lf)
    return out + [str(lf)]


def _schema(materials: list[str], n: int) -> dict:
    return {"type": "object", "properties": {"parts": {"type": "array", "items": {
        "type": "object", "properties": {
            "id": {"type": "integer", "description": f"the part's number, 1..{n}"},
            "role": {"type": "string", "description": "what the part is, in a few words"},
            "material": {"type": "string", "enum": materials},
            "motion": {"type": "string", "enum": MOTIONS},
            "relative_to": {"anyOf": [{"type": "integer"}, {"type": "null"}],
                            "description": "the part it moves against (its parent), or null"},
            "axis": {"anyOf": [{"type": "string", "enum": AXES}, {"type": "null"}]},
            "pivot": {"anyOf": [{"type": "string", "enum": PIVOTS}, {"type": "null"}]},
            "range": {"anyOf": [{"type": "number"}, {"type": "null"}],
                      "description": "travel: degrees for hinge, millimetres for slide/press; null if none"},
        }, "required": ["id", "role", "material", "motion", "relative_to", "axis", "pivot", "range"],
        "additionalProperties": False}}}, "required": ["parts"], "additionalProperties": False}


def _prompt(entry: dict, parts: list) -> str:
    v = entry.get("vlm") or {}
    fns = "; ".join(f"{f.get('does')} ({f.get('moving_part')}, {f.get('kind')})" for f in v.get("functions") or [])
    return (
        f"The images show a 3D asset: {v.get('object_name') or entry['asset_id']} "
        f"(class {entry.get('class_hint') or (entry.get('report') or {}).get('matched_class')}). "
        f"What it does, as seen before: {fns or 'nothing listed'}.\n"
        "The first three images are the same asset from three sides with every part flat-coloured and "
        "numbered on its own visible surface (a part not seen from a side has no number there; check a "
        "number against its colour in the legend); the last image is "
        "the legend: each number's colour and size in millimetres, and which parts are hidden inside. The image after the legend, if any, "
        "is the asset as modelled.\n"
        "For EVERY numbered part say what it is, its physics material (an object is usually several: a "
        "steel blade and a rubber grip), and how it moves in the real object: none (fixed to its "
        "neighbour), hinge (turns about a pin or edge), slide, spin (turns freely, a wheel or knob), "
        "press (a button pushed in), detach (comes off: a cap, a battery), flex (bends: a cable, a "
        "spring arm). For a moving part give relative_to (the part it moves against), the axis - PREFER "
        "the part's own directions, which hold however the model happens to lie: part_long (along the "
        "part's length), part_short (across it, within its flat face: a rocker paddle rocks about this), "
        "part_face_normal (through its flat face: a knob turns, a button is pressed along this). Use "
        "vertical / horizontal_long / horizontal_short (the object's directions as it stands in the "
        "images) only for a motion tied to gravity that no part direction gives. Then the pivot (where a hinge turns: center, end_near_parent, end_far_from_parent, top, "
        "bottom, contact_with_parent) and the range (degrees for a hinge, millimetres for a slide or "
        "press). Parts that are only paint or labels on another part: motion none, relative_to that "
        "part.")


def survey(asset_id: str, max_parts: int = 24) -> dict:
    import anthropic
    from pxr import Usd

    qf = QUEUE / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    root = "/World/" + "".join(w.capitalize() for w in asset_id.split("_"))
    st = Usd.Stage.Open(entry["file"])
    if not st.GetPrimAtPath(root):
        root = str(next(iter(st.GetPrimAtPath("/World").GetChildren())).GetPath())
    parts = labelled_parts(st, root, max_parts)
    if not parts:
        raise RuntimeError("no mesh parts")
    images = render(entry, parts, root)
    if entry.get("thumbnail") and Path(entry["thumbnail"]).exists():
        images.append(entry["thumbnail"])
    materials = sorted(json.loads(MATERIALS.read_text())["materials"])
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(Path(i).read_bytes()).decode()}}
               for i in images]
    content.append({"type": "text", "text": _prompt(entry, parts)})
    resp = anthropic.Anthropic().messages.create(
        model="claude-opus-5", max_tokens=16000, messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": _schema(materials, len(parts))}})
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined")
    ans = json.loads(next(b.text for b in resp.content if b.type == "text"))
    by_id = {p["id"]: p for p in parts}
    out = []
    for a in ans["parts"]:
        p = by_id.get(a["id"])
        if not p:
            continue
        if not p.get("seen", True) and a.get("motion") not in (None, "none"):
            # inside the shell, seen from no side (the K-Slim's pump was given a
            # spin): nothing outside moves it, nor would a viewer see it move
            a = {**a, "motion": "none", "hidden_motion": a["motion"]}
        out.append({**a, "path": p["path"], "members": p["members"], "seen": p.get("seen", True),
                    "size_m": [round(v, 4) for v in p["size"]], "centroid": [round(v, 5) for v in p["centroid"]]})
    result = {"date": date.today().isoformat(), "root": root, "parts": out,
              "images": [str(Path(i).relative_to(REPO)) for i in images if str(i).startswith(str(REPO))]}
    entry = json.loads(qf.read_text())
    entry["part_survey"] = result
    qf.write_text(json.dumps(entry, indent=1))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("assets", nargs="+")
    ap.add_argument("--max-parts", type=int, default=24)
    args = ap.parse_args()
    for a in args.assets:
        r = survey(a, args.max_parts)
        print(f"== {a}")
        for p in r["parts"]:
            mv = (f" {p['motion']} vs #{p['relative_to']} axis {p['axis']} pivot {p['pivot']} range {p['range']}"
                  if p["motion"] != "none" else "")
            print(f"  #{p['id']:2d} {p['role'][:34]:34s} {p['material']:16s}{mv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
