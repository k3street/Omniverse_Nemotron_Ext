"""Run RoboLab playback without its optional OpenCV preview window.

Isaac Sim remains fully graphical.  RoboLab already renders and records the
camera observations inside Isaac Sim; this shim only disables the redundant
``cv2.imshow`` window, which is unavailable when OpenCV's headless wheel wins
the shared ``cv2`` package namespace.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

import cv2


def main() -> None:
    root = Path(
        os.environ.get("ROBOLAB_ROOT", "/home/kimate/Documents/Github/RoboLab")
    ).expanduser().resolve()
    entrypoint = root / "examples" / "run_recorded.py"
    if not entrypoint.is_file():
        raise FileNotFoundError(f"RoboLab replay entrypoint not found: {entrypoint}")

    cv2.imshow = lambda *_args, **_kwargs: None
    cv2.waitKey = lambda *_args, **_kwargs: -1
    cv2.destroyAllWindows = lambda: None
    sys.path.insert(0, str(entrypoint.parent))
    sys.argv[0] = str(entrypoint)
    runpy.run_path(str(entrypoint), run_name="__main__")


if __name__ == "__main__":
    main()
