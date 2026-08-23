"""Fail when intentionally shared 5.1/6.0 extension files drift."""

from __future__ import annotations

import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "config" / "extension_shared_files.txt"
ROOT_51 = REPO / "exts" / "isaac_5.1" / "omni.isaac.assist" / "omni" / "isaac" / "assist"
ROOT_60 = REPO / "exts" / "isaac_6.0" / "omni.isaac.assist" / "omni" / "isaac" / "assist"


def shared_paths() -> list[str]:
    return [
        line.strip()
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def parity_errors() -> list[str]:
    errors: list[str] = []
    for relative in shared_paths():
        old, current = ROOT_51 / relative, ROOT_60 / relative
        if not old.is_file() or not current.is_file():
            errors.append(f"missing shared file: {relative}")
        elif old.read_bytes() != current.read_bytes():
            errors.append(f"shared extension drift: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    errors = parity_errors()
    if errors:
        print("\n".join(errors))
        return 1
    print(f"extension parity ok: {len(shared_paths())} shared files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
