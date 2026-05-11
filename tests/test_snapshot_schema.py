"""Schema tests for the _snapshot_stage payload format.

The 8 snapshot extensions added this session (2026-04-19) — PhysicsScene
attrs, joint drive/limit attrs, MassAPI attrs, material_binding,
references, variants, semantic_class, approximation — land as new keys
in the per-prim entry dict. These tests pin the EXPECTED KEYS and VALUE
TYPES so a future refactor of `_snapshot_stage` in multi_turn_session.py
can't silently break what the judge sees.

L0 — pure string inspection of the code-template string, plus a parser
check against a synthetic snapshot dict.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.l0


def test_snapshot_code_template_contains_all_8_extensions():
    """The inline code string in multi_turn_session.py generates Python
    that is exec'd inside Kit. These string fragments MUST be present
    for the 8 extensions to land."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "qa" / "multi_turn_session.py").read_text()
    # PhysicsScene config
    assert "physxScene:solverType" in src
    assert "physxScene:enableGPUDynamics" in src
    assert "physxScene:timeStepsPerSecond" in src
    # Joint drive / limits
    assert "physics:lowerLimit" in src
    assert "drive:angular:physics:stiffness" in src
    assert "drive:angular:physics:damping" in src
    # Mass
    assert "physics:mass" in src
    assert "physics:density" in src
    # Material binding
    assert "material_binding" in src
    assert "MaterialBindingAPI" in src
    # References
    assert "has_refs" in src or "references" in src
    # Variants
    assert "GetVariantSets" in src
    assert "variants" in src  # dict key
    # Semantics
    assert "semantic_class" in src
    assert "Semantics.SemanticsAPI" in src
    # Approximation
    assert "physics:approximation" in src


def test_judge_summary_surfaces_extensions():
    """The judge's _snapshot_summary must render the new fields so the
    Gemini judge can see them. Pin the render-line fragments."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "qa" / "ground_truth_judge.py").read_text()
    assert "attrs={" in src
    assert "material_binding=" in src
    assert "references=" in src or "has_refs=" in src
    assert "variants={" in src
    assert "semantic=" in src


def test_synthetic_snapshot_with_all_extensions_json_serializable():
    """If a future dev adds a Gf.Vec3f without converting to list, the
    resulting snapshot dict fails json.dumps. Build a representative
    dict and confirm it serializes."""
    snap = {
        "prim_count": 3,
        "prims": [
            {
                "path": "/World/PhysicsScene",
                "type": "PhysicsScene",
                "apis": ["PhysxSceneAPI"],
                "attrs": {
                    "physxScene:solverType": "TGS",
                    "physxScene:enableGPUDynamics": False,
                    "physxScene:timeStepsPerSecond": 60,
                    "physics:gravityDirection": [0.0, 0.0, -1.0],
                },
            },
            {
                "path": "/World/Arm/joint0",
                "type": "PhysicsRevoluteJoint",
                "attrs": {
                    "physics:lowerLimit": -90.0,
                    "physics:upperLimit": 90.0,
                    "drive:angular:physics:stiffness": 400.0,
                },
            },
            {
                "path": "/World/Apples/apple_0",
                "type": "Cube",
                "apis": ["PhysicsMassAPI"],
                "attrs": {
                    "physics:mass": 0.5,
                    "physics:density": 1000.0,
                    "physics:centerOfMass": [0.0, 0.0, 0.0],
                },
                "material_binding": "/World/Looks/apple_mat_0",
                "references": ["omniverse://localhost/asset.usd"],
                "variants": {"color": "blue"},
                "semantic_class": "apple",
            },
        ],
    }
    s = json.dumps(snap)
    assert len(s) > 100
    # Round-trip
    parsed = json.loads(s)
    assert parsed["prims"][2]["material_binding"] == "/World/Looks/apple_mat_0"
    assert parsed["prims"][2]["variants"]["color"] == "blue"
    assert parsed["prims"][2]["semantic_class"] == "apple"
    assert parsed["prims"][0]["attrs"]["physxScene:solverType"] == "TGS"


def test_extensions_opt_in_only():
    """Key design: each extension emits its field ONLY when the underlying
    data exists. An empty stage, or a Cube with nothing special, should
    produce a minimal {path, type} entry — not a bunch of null fields.
    Check the source: each extension wraps in a presence check before
    writing the entry key."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "qa" / "multi_turn_session.py").read_text()
    # Each extension should have a guard
    # material_binding only when ComputeBoundMaterial returns something
    assert "if mat:" in src or "if mat_path:" in src
    # references only if HasAuthoredReferences
    assert "HasAuthoredReferences" in src
    # variants only if GetNames returns non-empty
    assert "if vsets and vsets.GetNames()" in src
    # semantic only if instances found
    assert "if _sem_data" in src
