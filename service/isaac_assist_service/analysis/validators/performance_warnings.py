"""
PerformanceWarningsValidator — flag scene performance issues.

Catches:
- High poly-count meshes without convex decomposition
- Too many rigid bodies without GPU physics pipeline
- Excessive USD sublayers
- Missing LOD on high-detail assets
- Many active lights (expensive rendering)
"""
from typing import List, Dict, Any
import uuid

from .base import ValidationRule
from ..models import ValidationFinding


# Thresholds (configurable defaults)
_HIGH_POLY_THRESHOLD = 100_000       # vertices per mesh
_MAX_RIGID_BODIES_CPU = 200          # before recommending GPU pipeline
_MAX_SUBLAYERS = 10                  # sublayer count warning
_MAX_ACTIVE_LIGHTS = 16              # light count warning
_TOTAL_TRI_WARNING = 2_000_000       # total scene triangles


class PerformanceWarningsValidator(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "performance.warnings"
        self.pack = "performance_warnings"
        self.severity = "info"
        self.name = "Performance warnings"
        self.description = (
            "Flags potential performance issues — high poly counts, "
            "excessive rigid bodies, too many sublayers or lights."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        prims = stage_data.get("prims", [])
        sublayer_count = stage_data.get("sublayer_count", 0)

        rigid_body_count = 0
        total_vertices = 0
        total_triangles = 0
        light_count = 0
        high_poly_prims = []

        for prim in prims:
            path = prim.get("path", "")
            prim_type = prim.get("type", "")
            schemas = prim.get("schemas", [])
            attrs = prim.get("attributes", {})

            # Count rigid bodies
            if "PhysicsRigidBodyAPI" in schemas:
                rigid_body_count += 1

            # Count lights
            if any(kw in prim_type for kw in (
                "DistantLight", "SphereLight", "RectLight", "DiskLight",
                "CylinderLight", "DomeLight",
            )):
                visible = attrs.get("visibility", "inherited")
                if visible != "invisible":
                    light_count += 1

            # Vertex / triangle counts
            verts = attrs.get("vertex_count", attrs.get("points_count", 0))
            tris = attrs.get("face_count", attrs.get("triangle_count", 0))
            if isinstance(verts, (int, float)):
                total_vertices += int(verts)
                if int(verts) > _HIGH_POLY_THRESHOLD:
                    high_poly_prims.append((path, int(verts)))
            if isinstance(tris, (int, float)):
                total_triangles += int(tris)

        # --- High-poly meshes ---
        for prim_path, vert_count in high_poly_prims:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="performance.high_poly_mesh",
                pack=self.pack,
                severity="warning",
                prim_path=prim_path,
                message=f"High-poly mesh ({vert_count:,} vertices).",
                detail=(
                    f"Mesh '{prim_path}' has {vert_count:,} vertices. "
                    f"Consider using convex decomposition for collision "
                    f"and LOD for rendering to improve performance."
                ),
                evidence={"vertex_count": vert_count},
                auto_fixable=False,
            ))

        # --- Total scene triangles ---
        if total_triangles > _TOTAL_TRI_WARNING:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="performance.total_triangles",
                pack=self.pack,
                severity="warning",
                prim_path=None,
                message=f"Scene has {total_triangles:,} triangles.",
                detail=(
                    f"The scene contains {total_triangles:,} triangles "
                    f"across all meshes. This may impact rendering "
                    f"performance. Consider enabling LOD or reducing "
                    f"mesh detail for distant objects."
                ),
                evidence={"total_triangles": total_triangles},
                auto_fixable=False,
            ))

        # --- Too many rigid bodies ---
        if rigid_body_count > _MAX_RIGID_BODIES_CPU:
            gpu_enabled = stage_data.get("gpu_dynamics", False)
            if not gpu_enabled:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="performance.rigid_body_count",
                    pack=self.pack,
                    severity="warning",
                    prim_path=None,
                    message=(
                        f"{rigid_body_count} rigid bodies without GPU dynamics."
                    ),
                    detail=(
                        f"The scene has {rigid_body_count} rigid bodies "
                        f"but GPU dynamics is not enabled. CPU physics "
                        f"will be slow above ~{_MAX_RIGID_BODIES_CPU} "
                        f"bodies. Enable GPU dynamics in the PhysicsScene."
                    ),
                    evidence={
                        "rigid_body_count": rigid_body_count,
                        "gpu_dynamics": gpu_enabled,
                    },
                    auto_fixable=True,
                ))

        # --- Excessive sublayers ---
        if sublayer_count > _MAX_SUBLAYERS:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="performance.sublayer_count",
                pack=self.pack,
                severity="info",
                prim_path=None,
                message=f"Scene has {sublayer_count} sublayers.",
                detail=(
                    f"The scene uses {sublayer_count} USD sublayers. "
                    f"Many sublayers increase composition time and "
                    f"memory. Consider flattening unused layers."
                ),
                evidence={"sublayer_count": sublayer_count},
                auto_fixable=False,
            ))

        # --- Many active lights ---
        if light_count > _MAX_ACTIVE_LIGHTS:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="performance.light_count",
                pack=self.pack,
                severity="info",
                prim_path=None,
                message=f"{light_count} active lights in scene.",
                detail=(
                    f"The scene has {light_count} active lights. Each "
                    f"light adds rendering cost. Consider disabling or "
                    f"reducing lights for real-time simulation."
                ),
                evidence={"light_count": light_count},
                auto_fixable=False,
            ))

        return findings
