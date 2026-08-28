"""Generic world-space constraint realization helpers."""
from __future__ import annotations

import math


def governed_vertical_target(
    *,
    nominal_target_z: float,
    controlled_frame_z: float,
    subject_z: float,
    reference_z: float,
    minimum_clearance_m: float,
) -> float:
    """Raise a nominal target enough to preserve a subject/reference clearance.

    The function only reasons about world-frame scalar positions. It has no
    knowledge of the mechanism used to realize the returned target.
    """
    values = (
        nominal_target_z,
        controlled_frame_z,
        subject_z,
        reference_z,
        minimum_clearance_m,
    )
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("vertical-clearance inputs must be finite")
    if minimum_clearance_m < 0.0:
        raise ValueError("minimum_clearance_m must be non-negative")
    required_subject_delta = reference_z + minimum_clearance_m - subject_z
    required_controlled_target = controlled_frame_z + required_subject_delta
    return max(float(nominal_target_z), float(required_controlled_target))
