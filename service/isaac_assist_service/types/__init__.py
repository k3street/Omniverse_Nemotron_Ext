"""
Shared typed primitives for IA (Phase 8c).

Re-exports the seven primitives — `Vec3`, `Pose6D`, `Bbox3`,
`Distribution`, `GradedScale`, `Source` — so consumers can write:

    from service.isaac_assist_service.types import Pose6D, Distribution

The package has **ZERO internal dependencies** on any other module
under `service.isaac_assist_service.*`. This is enforced by the
import-purity smoke test in `tests/test_shared_types.py` and gates the
risk-mitigation contract from spec Phase 8c (which warned about import
cycles when `multimodal/`, `diagnose/`, and `governance/` start
depending on `types/`).

The only allowed external dependencies are:
- Python stdlib
- `pydantic` (Pydantic v2)
- `numpy`
"""
from service.isaac_assist_service.types.provenance import Source
from service.isaac_assist_service.types.spatial import Bbox3, Pose6D, Vec3
from service.isaac_assist_service.types.uncertainty import (
    Distribution,
    GradedScale,
)

__all__ = [
    "Bbox3",
    "Distribution",
    "GradedScale",
    "Pose6D",
    "Source",
    "Vec3",
]
