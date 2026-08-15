#!/usr/bin/env python3
"""Synchronous bridge for usd-validation-nvidia on Linux ARM64.

The upstream CLI uses its asynchronous callback path even for one file. With
the ARM64 ``usd-exchange`` OpenUSD provider that path can remain at 0% while a
worker is blocked. NVIDIA's documented blocking ``ValidationEngine.validate``
API completes normally, so this bridge preserves the official engine and JSON
schema while avoiding that CLI-only deadlock.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROBOTICS_RULES = (
    "StageMetadataChecker",
    "DefaultPrimChecker",
    "LayerSpecChecker",
    "UsdAsciiPerformanceChecker",
    "UnicodeNameChecker",
    "MaterialPathChecker",
    "MaterialOutOfScopeChecker",
    "UsdDanglingMaterialBinding",
    "UsdMaterialBindingApi",
    "RigidBodyChecker",
    "ColliderChecker",
    "PhysicsJointChecker",
    "ArticulationChecker",
    "MassChecker",
)


def _engine(ruleset: str | None = None):
    from usd_validation_nvidia import CategoryRuleRegistry, ValidationEngine

    ruleset = ruleset or os.environ.get(
        "NVIDIA_USD_VALIDATION_RULESET", "robotics"
    ).lower()
    if ruleset == "full":
        return ValidationEngine(processes=0)
    if ruleset != "robotics":
        raise ValueError(
            "NVIDIA_USD_VALIDATION_RULESET must be 'robotics' or 'full'"
        )
    engine = ValidationEngine(
        init_rules=False,
        variants=False,
        instance_prototypes=False,
        processes=0,
    )
    registry = CategoryRuleRegistry()
    for name in ROBOTICS_RULES:
        rule = registry.find_rule(name)
        if rule is None:
            raise RuntimeError(f"usd-validation-nvidia has no rule {name}")
        engine.enable_rule(rule)
    return engine


def self_test() -> int:
    from pxr import Usd, UsdGeom
    from usd_validation_nvidia import __version__

    stage = Usd.Stage.CreateInMemory("nvidia-validator-self-test.usda")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    result = _engine("robotics").validate(stage)
    print(json.dumps({
        "backend": "usd-validation-nvidia",
        "version": __version__,
        "openusd": ".".join(str(value) for value in Usd.GetVersion()[1:]),
        "completed": True,
        "ruleset": "robotics",
        "rule_count": len(ROBOTICS_RULES),
        "issue_count": len(result.issues),
    }))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", nargs="?")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--process", default="0")  # CLI-compatible, forced to zero
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.asset or not args.json_output:
        parser.error("asset and --json-output are required")

    from usd_validation_nvidia.reporting import export_json_file

    result = _engine().validate(args.asset)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    export_json_file(args.json_output, result, metadata=result.context)
    return 1 if result.issues else 0


if __name__ == "__main__":
    sys.exit(main())
