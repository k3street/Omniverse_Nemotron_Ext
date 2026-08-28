#!/usr/bin/env python3
"""Patch an upstream DROID checkout to retain Polymetis external torque.

DROID already serializes the dictionary returned by FrankaRobot.get_robot_state
into raw trajectory HDF5.  Polymetis populates ``motor_torques_external`` from
libfranka, but upstream's dictionary currently omits that protobuf field.  This
installer performs one narrow, idempotent source edit and refuses unknown
source layouts.
"""
from __future__ import annotations

import argparse
from pathlib import Path


ANCHOR = '            "motor_torques_measured": list(robot_state.motor_torques_measured),\n'
INSERTION = (
    ANCHOR
    + '            "motor_torques_external": list(robot_state.motor_torques_external),\n'
)
PROTO_FIELD = "motor_torques_external"


def target_file(droid_root: Path) -> Path:
    return droid_root.expanduser().resolve() / "droid" / "franka" / "robot.py"


def inspect_checkout(droid_root: Path) -> dict[str, object]:
    target = target_file(droid_root)
    if not target.is_file():
        raise FileNotFoundError(f"DROID Franka collector not found: {target}")
    source = target.read_text(encoding="utf-8")
    already_patched = INSERTION in source
    compatible = already_patched or source.count(ANCHOR) == 1
    proto_candidates = (
        droid_root / "fairo" / "polymetis" / "polymetis" / "proto" / "polymetis.proto",
        droid_root / "fairo" / "polymetis" / "polymetis" / "polymetis.proto",
    )
    existing_proto = next((path for path in proto_candidates if path.is_file()), None)
    proto_ready = (
        None
        if existing_proto is None
        else PROTO_FIELD in existing_proto.read_text(encoding="utf-8", errors="replace")
    )
    return {
        "target": target,
        "already_patched": already_patched,
        "compatible": compatible,
        "protobuf_checked": existing_proto is not None,
        "protobuf_ready": proto_ready,
        "protobuf_path": existing_proto,
    }


def patch_checkout(droid_root: Path, *, check_only: bool = False) -> dict[str, object]:
    status = inspect_checkout(droid_root)
    if not status["compatible"]:
        raise RuntimeError(
            "DROID collector layout is not recognized; refusing a broad or ambiguous edit"
        )
    if status["protobuf_ready"] is False:
        raise RuntimeError(
            f"Pinned Polymetis protobuf lacks {PROTO_FIELD}: {status['protobuf_path']}"
        )
    if check_only or status["already_patched"]:
        return {**status, "changed": False}
    target = status["target"]
    assert isinstance(target, Path)
    source = target.read_text(encoding="utf-8")
    patched = source.replace(ANCHOR, INSERTION, 1)
    target.write_text(patched, encoding="utf-8")
    verified = inspect_checkout(droid_root)
    if not verified["already_patched"]:
        raise RuntimeError("DROID external-torque patch did not verify")
    return {**verified, "changed": True}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retain motor_torques_external in future raw DROID episodes."
    )
    parser.add_argument("--droid-root", required=True, type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate compatibility without editing the checkout.",
    )
    args = parser.parse_args()
    result = patch_checkout(args.droid_root, check_only=args.check)
    print(
        f"target={result['target']} compatible={result['compatible']} "
        f"already_patched={result['already_patched']} changed={result['changed']} "
        f"protobuf_ready={result['protobuf_ready']}"
    )


if __name__ == "__main__":
    main()
