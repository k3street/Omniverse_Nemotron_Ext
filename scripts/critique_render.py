#!/usr/bin/env python3
"""Run the visual judges on a RENDER of authored work — the critic step.

The asset pipeline's judges (Cosmos-Reason2 via vLLM, gemma4 via Ollama,
Claude vision) exist to answer "does this look right?". They were only
ever pointed at ingested assets; this points them at anything we author
— a composed assembly, a scene, a physics result — BEFORE a human is
asked to look at it.

Local judges are used when their servers are up (they share the GPU with
Isaac, so they often are not); Claude vision needs no GPU and always
runs when ANTHROPIC_API_KEY is set.

Usage:
    python scripts/critique_render.py IMAGE [IMAGE ...] --expect "what it should show"
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "what_i_see": {"type": "string",
                       "description": "Literal description of the render"},
        "problems": {"type": "array", "items": {"type": "string"},
                     "description": "Concrete defects, most severe first"},
        "suggested_fixes": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["verdict", "what_i_see", "problems", "suggested_fixes",
                 "confidence"],
    "additionalProperties": False,
}


def _prompt(expect: str) -> str:
    return (
        "You are a critical reviewer of 3D physics-simulation authoring. "
        "These render(s) show work that is about to be shown to an "
        "engineer. It SHOULD show: " + expect + "\n\n"
        "Judge it harshly and concretely: is the geometry assembled where "
        "it belongs, are parts attached at plausible points, is anything "
        "tangled, floating, intersecting, duplicated, or facing the wrong "
        "way? Untextured gray is a renderer limitation, not a defect. "
        "Answer 'fail' if an engineer would call this wrong.")


def critique(images: list[str], expect: str) -> dict:
    import anthropic

    content = []
    for img in images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": base64.standard_b64encode(
                Path(img).read_bytes()).decode()}})
    content.append({"type": "text", "text": _prompt(expect)})
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-opus-5", max_tokens=16000,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the critique")
    out = json.loads(next(b.text for b in resp.content if b.type == "text"))
    out["judge"] = "claude-opus-5"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--expect", required=True)
    args = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — cannot critique")
        return 1
    result = critique(args.images, args.expect)
    print(json.dumps(result, indent=1))
    return 0 if result["verdict"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
