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

SCHEMA = {
    "type": "object",
    "properties": {
        "object_name": {"type": "string",
                        "description": "What the object actually is, in a few words"},
        "asset_class": {"type": ["string", "null"],
                        "description": "Best matching class key from the provided list, or null if none fits"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "articulable": {"type": "boolean",
                        "description": "Does the real-world object have moving parts?"},
        "visible_moving_parts": {"type": "array", "items": {"type": "string"},
                                 "description": "Moving parts visible in the render (wheels, lids, drawers...)"},
        "notes": {"type": "string",
                  "description": "Anything a sim2real reviewer should know (orientation, damage, scale cues)"},
    },
    "required": ["object_name", "asset_class", "confidence", "articulable",
                 "visible_moving_parts", "notes"],
    "additionalProperties": False,
}


def classify_thumbnail(png_path: str) -> dict:
    """One vision call: thumbnail -> structured classification."""
    import anthropic

    classes = json.loads(PRIORS_PATH.read_text())["classes"]
    class_list = "\n".join(
        f"- {k}: keywords {v['keywords']}, plausible max dimension "
        f"{v['max_dim_m'][0]}-{v['max_dim_m'][1]} m"
        for k, v in classes.items())
    image_data = base64.standard_b64encode(Path(png_path).read_bytes()).decode()

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png",
                            "data": image_data}},
                {"type": "text", "text": (
                    "This is a render of a 3D asset being ingested into a robotics "
                    "simulation pipeline. Identify what the object is, whether the "
                    "real-world object has moving parts, and which class from this "
                    "list fits best (null if none):\n\n" + class_list)},
            ],
        }],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the classification request")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def classify_entry(asset_id: str) -> str:
    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    thumb = entry.get("thumbnail")
    if not thumb or not Path(thumb).exists():
        return f"{asset_id}: no thumbnail — render one first (re-ingest)"
    result = classify_thumbnail(thumb)
    entry["vlm"] = result
    old_class = entry.get("report", {}).get("matched_class")
    new_class = result.get("asset_class")
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
        if factor or (new_class != old_class and not has_joints):
            from ingest_asset import (apply_rigid_physics, build_wrapper,
                                      refresh_renders)
            entry.setdefault("original_file", entry["file"])
            entry["file"] = build_wrapper(
                entry, float(factor) if factor else None)
            entry.setdefault("applied_fixes", []).append(
                (f"auto scale x{factor} " if factor else "physics rebuild ")
                + f"(after VLM reclass to {new_class})")
            entry["report"] = run_report(entry["file"], new_class)
            note = apply_rigid_physics(entry)
            if note:
                entry["applied_fixes"].append(note)
                entry["report"] = run_report(entry["file"], new_class)
            entry["proposed_category"] = propose_category(entry["report"])
            # renders must follow the file they judge — a stale image is
            # evidence about the wrong asset
            refresh_renders(entry)
    qf.write_text(json.dumps(entry, indent=1))
    change = (f"{old_class} -> {new_class}" if new_class and new_class != old_class
              else f"confirmed {old_class}" if new_class else "no class fits")
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
