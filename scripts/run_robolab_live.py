"""Run RoboLab's GR00T evaluator without its optional OpenCV preview window.

Isaac Sim remains graphical.  This disables only the redundant ``cv2.imshow``
preview, which is unavailable in RoboLab's headless OpenCV installation.
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
    entrypoint = root / "policies" / "gr00t" / "run.py"
    if not entrypoint.is_file():
        raise FileNotFoundError(f"RoboLab GR00T entrypoint not found: {entrypoint}")

    cv2.imshow = lambda *_args, **_kwargs: None
    cv2.waitKey = lambda *_args, **_kwargs: -1
    cv2.destroyAllWindows = lambda: None
    sys.path.insert(0, str(root))
    sys.argv[0] = str(entrypoint)
    runpy.run_path(str(entrypoint), run_name="__main__")


if __name__ == "__main__":
    main()
