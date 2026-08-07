#!/usr/bin/env python3
"""Real-world product spec lookup for asset ingest.

Class priors give a plausible RANGE; a named product has a SPEC. For
assets whose filename/VLM identification names a real product ("Apple
Vision Pro", "AZ vaccine vial"), a web lookup returns measured
dimensions and mass with a source URL — far stronger scale/mass
evidence than a class range, and it disambiguates identities a gray
render can't (vial vs bottle).

Results are cached in workspace/knowledge/product_specs.json so repeat
ingests are deterministic and offline. A lookup is EVIDENCE, not truth:
the spec is recorded with provenance and still passes through the same
audit + visual QA gates as everything else.

Usage:
    python scripts/product_lookup.py "Apple Vision Pro"
    python scripts/product_lookup.py --for-asset <asset_id>

Requires ANTHROPIC_API_KEY (Claude + web search server tool).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CACHE_PATH = REPO / "workspace" / "knowledge" / "product_specs.json"

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "is_known_product": {
            "type": "boolean",
            "description": "True only if this names a specific real-world "
                           "product with published specs"},
        "product_name": {"type": "string"},
        "max_dim_m": {"anyOf": [{"type": "number"}, {"type": "null"}],
                      "description": "Largest physical dimension in meters"},
        "dims_m": {"anyOf": [{"type": "array",
                              "items": {"type": "number"}},
                             {"type": "null"}],
                   "description": "W x D x H in meters when published"},
        "mass_kg": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "source": {"type": "string",
                   "description": "URL or citation for the figures"},
        "notes": {"type": "string"},
    },
    "required": ["is_known_product", "product_name", "max_dim_m", "dims_m",
                 "mass_kg", "source", "notes"],
    "additionalProperties": False,
}


def _norm(query: str) -> str:
    return " ".join(t for t in re.split(r"[\W_]+", query.lower()) if t)


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {"_doc": "Web-sourced product specs used as per-asset priors at "
                    "ingest. Cached for determinism; delete a key to force "
                    "a fresh lookup.", "specs": {}}


def lookup(query: str, refresh: bool = False) -> dict | None:
    """Spec for a named product, or None when it isn't one. Cached."""
    key = _norm(query)
    cache = _load_cache()
    if not refresh and key in cache["specs"]:
        return cache["specs"][key] or None

    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        tools=[{"type": "web_search_20260209", "name": "web_search",
                "max_uses": 4}],
        messages=[{"role": "user", "content": (
            "A 3D asset in a robotics-simulation ingest pipeline is named "
            f"{query!r}. If this names a specific real-world product, look "
            "up its published physical dimensions and weight (the device "
            "itself, not packaging; exclude detachable cables/batteries "
            "unless integral). If it is generic or unidentifiable, say "
            "is_known_product=false rather than guessing.")}],
        output_config={"format": {"type": "json_schema",
                                  "schema": SPEC_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the lookup")
    result = json.loads(
        next(b.text for b in response.content if b.type == "text"))
    spec = None
    if result.get("is_known_product") and (
            result.get("max_dim_m") or result.get("mass_kg")):
        spec = {k: result[k] for k in ("product_name", "max_dim_m", "dims_m",
                                       "mass_kg", "source", "notes")}
        spec["looked_up"] = date.today().isoformat()
    cache["specs"][key] = spec  # negative results cached too
    CACHE_PATH.write_text(json.dumps(cache, indent=1))
    return spec


def enrich_entry(asset_id: str, refresh: bool = False) -> str:
    """Attach a product spec to a queue entry and act on it: rescale the
    derivative to spec dims (>15% off) and author the spec mass. The
    query prefers the VLM's visual identification over the filename."""
    from ingest_asset import (QUEUE_DIR, apply_rigid_physics, build_wrapper,
                              propose_category, refresh_renders, run_report)
    qf = QUEUE_DIR / f"{asset_id}.json"
    entry = json.loads(qf.read_text())
    name = (entry.get("vlm") or {}).get("object_name") or ""
    query = f"{name} ({asset_id})" if name else asset_id
    spec = lookup(query, refresh=refresh)
    if not spec:
        return f"{asset_id}: no product spec ({_norm(query)!r} not a known product)"
    entry["product_spec"] = spec

    cls = entry.get("class_hint") or entry.get("report", {}).get("matched_class")
    changed = []
    if spec.get("max_dim_m"):
        from visual_qa import _measure_dims
        dims = _measure_dims(entry.get("file"))
        if dims:
            measured = max(dims)
            factor = spec["max_dim_m"] / measured
            if abs(factor - 1.0) > 0.15 and not entry.get(
                    "report", {}).get("structure", {}).get("joints"):
                entry.setdefault("original_file", entry["file"])
                entry["file"] = build_wrapper(entry, factor)
                changed.append(f"rescaled x{factor:.4g} to spec "
                               f"{spec['max_dim_m']} m ({spec['source']})")
    if changed or spec.get("mass_kg"):
        entry["report"] = run_report(entry["file"], cls)
        if spec.get("mass_kg") and not entry["report"].get(
                "structure", {}).get("joints"):
            note = apply_rigid_physics(entry)
            if note:
                changed.append(note)
            # spec mass overrides the class-prior midpoint
            from pxr import Usd, UsdPhysics
            stage = Usd.Stage.Open(entry["file"])
            for prim in stage.Traverse():
                if prim.HasAPI(UsdPhysics.MassAPI):
                    UsdPhysics.MassAPI(prim).GetMassAttr().Set(
                        float(spec["mass_kg"]))
                    changed.append(f"mass {spec['mass_kg']} kg from spec")
                    break
            stage.GetRootLayer().Save()
            del stage
        entry["report"] = run_report(entry["file"], cls)
        entry["proposed_category"] = propose_category(entry["report"])
        entry.setdefault("applied_fixes", []).extend(
            f"spec: {c}" for c in changed)
        refresh_renders(entry)
    qf.write_text(json.dumps(entry, indent=1))
    return (f"{asset_id}: spec '{spec['product_name']}' "
            f"max_dim {spec.get('max_dim_m')} m, {spec.get('mass_kg')} kg"
            + (f" — {'; '.join(changed)}" if changed else " — no change needed"))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    refresh = "--refresh" in sys.argv
    if not args:
        print(__doc__)
        return 1
    if "--for-asset" in sys.argv:
        for aid in args:
            print(enrich_entry(aid, refresh=refresh))
        return 0
    for q in args:
        print(json.dumps(lookup(q, refresh=refresh), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
