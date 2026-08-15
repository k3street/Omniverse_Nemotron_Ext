"""Version and collision helpers shared by the standalone Newton probes."""
from __future__ import annotations

import re

EXPECTED_NEWTON_VERSION = "1.5.0"
MIN_WARP_VERSION = (1, 16, 0)


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise RuntimeError(f"cannot parse package version {value!r}")
    return tuple(int(part) for part in match.groups())


def require_newton_15():
    """Fail before a probe can overwrite evidence with another runtime."""
    import newton
    import warp as wp

    newton_version = getattr(newton, "__version__", "unknown")
    warp_version = getattr(wp, "__version__", "unknown")
    if newton_version != EXPECTED_NEWTON_VERSION:
        raise RuntimeError(
            f"standalone probes require Newton {EXPECTED_NEWTON_VERSION}; "
            f"found {newton_version}. Install requirements-newton.txt in a "
            "separate environment (do not replace Isaac Lab's Newton pin)."
        )
    if _version_tuple(warp_version) < MIN_WARP_VERSION:
        raise RuntimeError(
            "Newton 1.5 requires Warp >=1.16.0; "
            f"found {warp_version}. Reinstall requirements-newton.txt."
        )
    # Newton 1.5 deprecates DOF-shaped position targets for free/ball/distance
    # joints. Opt into the future layout before any ModelBuilder is created.
    newton.use_coord_layout_targets = True
    return newton, wp


def runtime_metadata() -> dict[str, str]:
    newton, wp = require_newton_15()
    return {
        "newton_version": newton.__version__,
        "warp_version": wp.__version__,
    }


def runtime_label(details: str) -> str:
    versions = runtime_metadata()
    return (f"Newton {versions['newton_version']} / Warp "
            f"{versions['warp_version']} — {details}")


def collision_pipeline(model, **kwargs):
    """Construct the Newton 1.5 pipeline and its reusable contact buffer."""
    newton, _ = require_newton_15()
    pipeline = newton.CollisionPipeline(model, **kwargs)
    return pipeline, pipeline.contacts()
