#!/usr/bin/env python3
"""VLM visual classification for asset ingest (BACKLOG #5).

The filename may have nothing to do with the object — this classifies the
asset from its rendered thumbnail using Claude vision, constrained to the
class keys in workspace/knowledge/asset_class_priors.json. The result
becomes the entry's class_hint (class_source: "vlm") and the checks re-run
under that prior, auto-rescaling if the corrected class changes the
expected size.

The human reviewer still approves; this replaces the *filename guess*, not
the sign-off.

Usage:
    python scripts/vlm_classify.py <asset_id> [...]

Requires ANTHROPIC_API_KEY (or an active `ant auth login` profile).
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_asset import QUEUE_DIR, propose_category, run_report  # noqa: E402

PRIORS_PATH = REPO / "workspace" / "knowledge" / "asset_class_priors.json"

MATERIALS_PATH = REPO / "workspace" / "knowledge" / "physics_materials.json"


def _schema() -> dict:
    materials = sorted(json.loads(
        MATERIALS_PATH.read_text())["materials"].keys())
    return {
        "type": "object",
        "properties": {
            "object_name": {"type": "string",
                            "description": "What the object actually is, in a few words"},
            "asset_class": {"anyOf": [{"type": "string"}, {"type": "null"}],
                            "description": "Best matching class key from the provided list, or null if none fits"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "articulable": {"type": "boolean",
                            "description": "Does the real-world object have moving parts?"},
            "visible_moving_parts": {"type": "array", "items": {"type": "string"},
                                     "description": "Moving parts visible in the render (wheels, lids, drawers...)"},
            "notes": {"type": "string",
                      "description": "Anything a sim2real reviewer should know (orientation, damage, scale cues)"},
            # unknown-class path: the taxonomy grows itself. These fields
            # let an unrecognized object REGISTER its own prior class
            # (marked provisional until a human confirms it).
            "proposed_class_key": {"anyOf": [{"type": "string"},
                                             {"type": "null"}],
                                   "description": "If no class fits: a new snake_case class key for this KIND of object (e.g. 'watering_can')"},
            "est_max_dim_m": {"anyOf": [{"type": "array",
                                         "items": {"type": "number"}},
                                        {"type": "null"}],
                              "description": "Plausible [min,max] largest dimension of the real object, meters"},
            "est_mass_kg": {"anyOf": [{"type": "array",
                                       "items": {"type": "number"}},
                                      {"type": "null"}],
                            "description": "Plausible [min,max] mass of the real object, kg"},
            "primary_material": {"anyOf": [{"type": "string",
                                            "enum": materials},
                                           {"type": "null"}],
                                 "description": "Dominant physical material"},
            "content_kind": {"type": "string",
                             "enum": ["single_object", "object_set", "scene_fragment", "scene"],
                             "description": "One object; several separate objects (a chess set, a screw and a "
                                            "loose washer); part of a larger thing (one wall of an elevator "
                                            "car, a lone drawer); or a whole scene or room"},
            "functions": {
                "type": "array",
                "description": "What the real object DOES when used - one entry per function: a drill "
                               "spins its chuck (trigger sets speed, direction switch sets which way, needs "
                               "its battery); a powered wheelchair drives on its wheels (joystick, needs "
                               "power) with front casters swivelling; a crash-bar door opens only once the "
                               "bar is pushed. Empty for an object that does nothing by itself (a mug).",
                "items": {"type": "object", "properties": {
                    "does": {"type": "string", "description": "the function, in a few words"},
                    "moving_part": {"type": "string", "description": "the part that moves"},
                    "controls": {"type": "array", "items": {"type": "string"},
                                 "description": "the parts a user works to make it happen"},
                    "requires": {"type": "array", "items": {"type": "string"},
                                 "description": "conditions: power, a battery, a key, another part first"},
                    "kind": {"type": "string",
                             "enum": ["manual", "motor", "wheeled_base", "gate", "spring_return",
                                      "detachable", "other"],
                             "description": "manual: a part moved by hand (a lid, a drawer); motor: a powered "
                                            "part whose speed/direction the controls set; wheeled_base: wheels "
                                            "that drive/roll the object, with steering or swivelling casters; "
                                            "gate: one part moves only after another (a latch, a lock); "
                                            "spring_return: snaps back when released (a button, a trigger); "
                                            "detachable: comes off (a cap, a battery)"}},
                    "required": ["does", "moving_part", "controls", "requires", "kind"],
                    "additionalProperties": False}},
            "deformable_type": {"anyOf": [{"type": "string",
                                           "enum": ["cloth", "sponge",
                                                    "rubber", "gel", "rope"]},
                                          {"type": "null"}],
                                "description": "Soft-body type if the real object is deformable, else null"},
        },
        "required": ["object_name", "asset_class", "confidence", "articulable",
                     "visible_moving_parts", "notes", "proposed_class_key",
                     "est_max_dim_m", "est_mass_kg", "primary_material",
                     "content_kind", "deformable_type", "functions"],
        "additionalProperties": False,
    }


def register_provisional_class(result: dict, asset_id: str) -> str | None:
    """Create a prior class from the VLM's estimates. Marked source='vlm'
    (provisional): visual QA fails closed on provisional classes, so the
    FIRST asset of a new kind always crosses a human — approving it
    confirms the class, and later assets machine-approve normally."""
    import re as _re
    from datetime import date as _date

    key = result.get("proposed_class_key")
    dims = result.get("est_max_dim_m")
    mass = result.get("est_mass_kg")
    if (not key or not dims or not mass
            or len(dims) != 2 or len(mass) != 2):
        return None
    key = _re.sub(r"[\W]+", "_", key.strip().lower()).strip("_")
    data = json.loads(PRIORS_PATH.read_text())
    if key in data["classes"]:
        return key  # raced/already registered — just use it
    # Only the class key's own words become keywords. The free-text object
    # name ("Socket head cap screw with partially threaded shank") yields
    # "with", "cap", "head": under longest-keyword matching those capture
    # unrelated files ("Bottle_with_cap" -> bolt_fastener). A human widens
    # the keywords when confirming the class.
    # ...and not even those one by one: 'blister_pack' gave 'pack', which
    # then claimed a blood pack ('record', 'robot', 'model' did the same).
    # A multi-word key is matched as its whole phrase.
    tokens = [t for t in _re.split(r"[\W_]+", key.lower()) if len(t) > 2]
    phrase = " ".join(t for t in _re.split(r"[\W_]+", key.lower()) if t)
    data["classes"][key] = {
        "keywords": [phrase] if len(tokens) > 1 else sorted(set(tokens)),
        "max_dim_m": [float(dims[0]), float(dims[1])],
        "mass_kg": [float(mass[0]), float(mass[1])],
        # a soft body is simulated as one deformable, never jointed
        "articulable": bool(result.get("articulable")) and not result.get("deformable_type"),
        **({"deformable": result["deformable_type"]}
           if result.get("deformable_type") else {}),
        "typical_materials": ([result["primary_material"]]
                              if result.get("primary_material") else []),
        # what it does: the behavior conventions the class carries (behaviors.py)
        **({"behaviors": sorted({f["kind"] for f in result.get("functions") or []
                                 if f.get("kind") in ("motor", "wheeled_base", "gate")})}
           if any(f.get("kind") in ("motor", "wheeled_base", "gate") for f in result.get("functions") or [])
           else {}),
        "source": "vlm",
        "proposed_by": asset_id,
        "proposed_on": _date.today().isoformat(),
    }
    PRIORS_PATH.write_text(json.dumps(data, indent=1))
    # the ingest report reads priors through an lru_cache: without this the
    # re-check right after registering cannot see the new class, falls back
    # to "no class", and skips the scale correction the class exists for
    from service.isaac_assist_service.chat.tools.handlers.physics import _load_asset_priors
    _load_asset_priors.cache_clear()
    return key


def _hint_text(hints: dict | None) -> str:
    """What the renders cannot show: the file's name and measured size, as
    evidence only. A render has no scale - a 30 cm vinyl record and a 6 mm
    washer are the same thin annulus - but names are often wrong (an
    'elevator key' that is a call panel) and sizes are off by unit slips."""
    if not hints:
        return ""
    out = ["\n\nEvidence beyond the image - weigh it, do not trust it blindly:"]
    if hints.get("file_name"):
        out.append(f"- The file is named {hints['file_name']!r}. Names are often wrong or generic; "
                   "if the image clearly shows something else, say what the image shows.")
    if hints.get("max_dim_m"):
        out.append(f"- Its largest dimension measures {hints['max_dim_m']:.3g} m as authored. This can be "
                   "off by a unit factor (x10, x100, x1000) or have been fitted to a guessed class; "
                   "use it to tell apart objects of the same shape and very different size.")
    return "\n".join(out)


def classify_thumbnail(png_path: str, views: list[str] = (), hints: dict | None = None) -> dict:
    """One vision call: the thumbnail and the orbit views -> structured
    classification. One view is often edge-on; four from around the object
    are what a person would look at."""
    import anthropic

    classes = json.loads(PRIORS_PATH.read_text())["classes"]
    class_list = "\n".join(
        f"- {k}: keywords {v['keywords']}, plausible max dimension "
        f"{v['max_dim_m'][0]}-{v['max_dim_m'][1]} m"
        for k, v in classes.items())
    images = [{"type": "image",
               "source": {"type": "base64", "media_type": "image/png",
                          "data": base64.standard_b64encode(Path(p).read_bytes()).decode()}}
              for p in [png_path, *[v for v in views if Path(v).exists()]]]

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": [
                *images,
                {"type": "text", "text": (
                    "These are renders of one 3D asset (a hero view, then views from "
                    "around it) being ingested into a robotics "
                    "simulation pipeline. Identify what the object is, whether the "
                    "real-world object has moving parts, and which class from this "
                    "list fits best (null if none). If NO class fits, propose a new "
                    "snake_case class key for this KIND of object plus plausible "
                    "real-world max-dimension and mass ranges, its dominant "
                    "material, and whether it is deformable. Say whether the render shows "
                    "one object, a set of separate objects, a fragment of something larger, "
                    "or a whole scene:\n\n" + class_list + _hint_text(hints))},
            ],
        }],
        output_config={"format": {"type": "json_schema", "schema": _schema()}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the classification request")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def class_from_name(stem: str) -> str | None:
    """The class a file's name claims, by ingest's whole-word keyword rule
    (longest keyword wins), or None."""
    import re

    words = [w for w in re.split(r"[\W_]+", stem.lower()) if w]
    text, tokens = " " + " ".join(words) + " ", set(words)
    best, best_kw = None, ""
    for key, prior in json.loads(PRIORS_PATH.read_text())["classes"].items():
        for kw in prior.get("keywords", []):
            hit = (" " + kw + " " in text) if " " in kw else (kw in tokens)
            if hit and len(kw) > len(best_kw):
                best, best_kw = key, kw
    return best


def set_class(asset_id: str, cls: str, why: str) -> str:
    """Apply a class a reviewer settled on (the file's name was right and the
    VLM wrong - a cutting board it saw as a plate): re-check, rebuild the
    derivative at the class's size, re-author rigid physics and re-render.
    Never touches a derivative with joints."""
    from ingest_asset import apply_rigid_physics, build_wrapper, refresh_renders

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    # an authored articulation is never rebuilt; the fixed joints set
    # physics makes inside each object of a set are rebuilt with it
    fixes = entry.get("applied_fixes", [])
    done = [i for i, f in enumerate(fixes) if f.startswith("articulate_asset")]
    undone = [i for i, f in enumerate(fixes) if f.startswith("unarticulated")]
    if done and not (undone and undone[-1] > done[-1]):
        return f"{asset_id}: has an authored articulation - not rebuilt"
    entry["class_hint"], entry["class_source"] = cls, "curated"
    entry["report"] = run_report(entry["file"], cls)
    factor = entry["report"].get("suggested_scale_correction")
    entry.setdefault("original_file", entry["file"])
    entry["file"] = build_wrapper(entry, float(factor) if factor else None)
    entry["report"] = run_report(entry["file"], cls)
    fixes = [f"class set to {cls} ({why})" + (f", auto scale x{factor}" if factor else "")]
    note = apply_rigid_physics(entry)
    if note:
        fixes.append(note)
        entry["report"] = run_report(entry["file"], cls)
    entry.setdefault("applied_fixes", []).extend(fixes)
    entry["proposed_category"] = propose_category(entry["report"])
    entry.pop("identity_mismatch", None)
    refresh_renders(entry)
    qf.write_text(json.dumps(entry, indent=1))
    return f"{asset_id}: {'; '.join(fixes)}; {entry['report'].get('max_dim_m')} m"


def _fit_factor(size: float, lo: float, hi: float) -> float | None:
    """Scale that brings `size` into [lo, hi]: a power of ten when one lands
    in range (a unit slip), else the range's geometric middle; None if in range."""
    import math

    if lo <= size <= hi or size <= 0:
        return None
    mid = (lo * hi) ** 0.5
    fits = [10.0 ** k for k in range(-4, 5) if lo <= size * 10.0 ** k <= hi]
    return min(fits, key=lambda u: abs(math.log(size * u / mid))) if fits else mid / size


def fit_to_object_size(asset_id: str) -> str | None:
    """Size the asset to what the VLM says THIS object measures (a ball gown
    1.1-1.7 m), which is tighter than its class (any garment, 0.3-1.8 m).
    Only for confident looks; never on a derivative with joints."""
    from ingest_asset import apply_rigid_physics, build_wrapper, refresh_renders

    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    v = entry.get("vlm") or {}
    est, size = v.get("est_max_dim_m"), entry.get("report", {}).get("max_dim_m")
    if (not est or len(est) != 2 or not size or v.get("confidence") not in ("high", "medium")
            or entry.get("report", {}).get("structure", {}).get("joints")):
        return None
    factor = _fit_factor(float(size), float(min(est)), float(max(est)))
    if factor is None:
        return None
    cls = entry.get("class_hint") or entry["report"].get("matched_class")
    entry.setdefault("original_file", entry["file"])
    entry["file"] = build_wrapper(entry, factor)
    entry["report"] = run_report(entry["file"], cls)
    fixes = [f"sized to the object x{factor:.4g}: {size} m -> {entry['report'].get('max_dim_m')} m "
             f"(the VLM puts a {v.get('object_name')} at {est[0]}-{est[1]} m)"]
    note = apply_rigid_physics(entry)
    if note:
        fixes.append(note)
        entry["report"] = run_report(entry["file"], cls)
    entry.setdefault("applied_fixes", []).extend(fixes)
    entry["proposed_category"] = propose_category(entry["report"])
    refresh_renders(entry)
    qf.write_text(json.dumps(entry, indent=1))
    return fixes[0]


def classify_entry(asset_id: str) -> str:
    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    thumb = entry.get("thumbnail")
    if not thumb or not Path(thumb).exists():
        return f"{asset_id}: no thumbnail — render one first (re-ingest)"
    src = entry.get("original_file") or entry["file"]
    hints = {"file_name": Path(src).stem, "max_dim_m": entry.get("report", {}).get("max_dim_m")}
    result = classify_thumbnail(thumb, entry.get("views") or [], hints)
    entry["vlm"] = result
    old_class = entry.get("report", {}).get("matched_class")
    # what the file's NAME says, kept from the first look: a later reclass
    # overwrites matched_class, and the disagreement is the evidence
    if "name_class" not in entry:
        # from the file's NAME alone: ingest also matches prim and material
        # names ('Glass' on a cassette deck's window), which is no name claim
        src = entry.get("original_file") or entry["file"]
        entry["name_class"] = class_from_name(Path(src).stem)
    new_class = result.get("asset_class")
    if (not new_class and result.get("confidence") in ("high", "medium")):
        new_class = register_provisional_class(result, asset_id)
        if new_class:
            result["asset_class"] = new_class
            result["registered_provisional_class"] = True
    if new_class and result.get("confidence") == "low":
        # A low-confidence look (an untextured slab, an edge-on view) must not
        # rescale the asset: today it turned a dog bowl into a can, a remote
        # into a box and a ring into a 55 cm picture frame. Record it for the
        # reviewer instead.
        entry["vlm_suggestion"] = {"asset_class": new_class, "object_name": result.get("object_name"),
                                   "confidence": "low", "applied": False}
        new_class = None
    if new_class:
        entry["class_hint"] = new_class
        entry["class_source"] = "vlm"
        entry["report"] = run_report(entry["file"], new_class)
        entry["proposed_category"] = propose_category(entry["report"])
        # class change may change the plausible size — re-apply auto-scale;
        # the rebuild discards prior wrapper edits, so re-apply physics too.
        # Even WITHOUT a size change, physics authored under the old class
        # carries the old material/mass — rebuild so the derivative matches
        # the corrected class. Never touch joint-authored derivatives.
        factor = entry["report"].get("suggested_scale_correction")
        has_joints = bool(entry["report"].get("structure", {}).get("joints"))
        # a set seen for the first time needs a body per object
        new_set = (result.get("content_kind") == "object_set"
                   and (entry.get("vlm_prev_kind") != "object_set"))
        if factor or ((new_class != old_class or new_set) and not has_joints):
            from ingest_asset import (apply_rigid_physics, build_wrapper,
                                      refresh_renders)
            entry.setdefault("original_file", entry["file"])
            entry["file"] = build_wrapper(
                entry, float(factor) if factor else None)
            entry.setdefault("applied_fixes", []).append(
                (f"auto scale x{factor} " if factor else "physics rebuild ")
                + (f"(after VLM reclass to {new_class})" if new_class != old_class
                   else "(the VLM sees separate objects)" if new_set else f"(VLM confirmed {new_class})"))
            entry["report"] = run_report(entry["file"], new_class)
            note = apply_rigid_physics(entry)
            if note:
                entry["applied_fixes"].append(note)
                entry["report"] = run_report(entry["file"], new_class)
            entry["proposed_category"] = propose_category(entry["report"])
            # renders must follow the file they judge — a stale image is
            # evidence about the wrong asset
            refresh_renders(entry)
    entry["vlm_prev_kind"] = result.get("content_kind")
    seen = result.get("asset_class")
    if entry.get("name_class") and seen and seen != entry["name_class"]:
        # the file is named for one thing and shows another ('elevator key'
        # that is a call-button panel): the name cannot be trusted for size,
        # mechanisms or search. At low confidence it is only possible.
        entry["identity_mismatch"] = {"name_says": entry["name_class"], "content_is": seen,
                                      "object_name": result.get("object_name"),
                                      "confidence": result.get("confidence")}
    else:
        entry.pop("identity_mismatch", None)
    from processing import record
    record(entry, "classify", object_name=result.get("object_name"), asset_class=result.get("asset_class"))
    qf.write_text(json.dumps(entry, indent=1))
    change = (f"{old_class} -> {new_class}" if new_class and new_class != old_class
              else f"confirmed {old_class}" if new_class
              else f"suggests {entry['vlm_suggestion']['asset_class']} at low confidence: left for the reviewer"
              if entry.get("vlm_suggestion", {}).get("applied") is False else "no class fits")
    # a NAMED product can trade the class-prior range for a published spec
    # (dims + mass with source) — the VLM's identification is the query
    import os
    if os.environ.get("PRODUCT_LOOKUP") == "1" and result.get(
            "confidence") in ("high", "medium"):
        try:
            from product_lookup import enrich_entry
            change += "; " + enrich_entry(asset_id)
        except Exception as e:
            change += f"; spec lookup failed: {str(e)[:80]}"
    sized = fit_to_object_size(asset_id)
    if sized:
        change += f"; {sized}"
    kind = result.get("content_kind", "single_object")
    if kind != "single_object":
        change += f"; content is {kind.replace('_', ' ')}, not one object"
    if entry.get("identity_mismatch"):
        change += (f"; {'POSSIBLE ' if result.get('confidence') == 'low' else ''}"
                   f"NAME MISMATCH: named as {entry['name_class']}")
    return (f"{asset_id}: VLM sees '{result['object_name']}' "
            f"({result['confidence']} confidence) — {change}"
            + (f"; moving parts: {', '.join(result['visible_moving_parts'])}"
               if result["visible_moving_parts"] else "; no moving parts visible"))


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    failures = 0
    for asset_id in args:
        try:
            print(classify_entry(asset_id))
        except Exception as e:
            failures += 1
            print(f"ERROR {asset_id}: {str(e)[:200]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
