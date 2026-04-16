"""
SensorCompletenessValidator — verify sensor wiring is complete.

Catches:
- Camera prims without RenderProduct (can't produce images)
- LiDAR without OmniGraph tick pipeline
- IMU/ContactSensor not attached to a physics-enabled body
- Sensor prims with no downstream consumer
"""
from typing import List, Dict, Any
import uuid

from .base import ValidationRule
from ..models import ValidationFinding


class SensorCompletenessValidator(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "sensor.completeness"
        self.pack = "sensor_completeness"
        self.severity = "warning"
        self.name = "Sensor wiring completeness"
        self.description = (
            "Checks that sensors have the required downstream wiring — "
            "cameras need RenderProducts, LiDAR needs OmniGraph ticks, "
            "and physics sensors must be on physics-enabled bodies."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        prims = stage_data.get("prims", [])
        prim_map = {p.get("path", ""): p for p in prims}
        og_nodes = stage_data.get("omnigraph_nodes", [])

        # Collect render product targets for quick lookup
        render_product_cameras = set()
        for p in prims:
            if p.get("type") == "RenderProduct":
                cam_path = p.get("attributes", {}).get("cameraPrim", "")
                if cam_path:
                    render_product_cameras.add(cam_path)

        # Collect OmniGraph node types for LiDAR tick detection
        og_node_types = set()
        for node in og_nodes:
            og_node_types.add(node.get("type", ""))

        for prim in prims:
            path = prim.get("path", "")
            prim_type = prim.get("type", "")
            schemas = prim.get("schemas", [])
            attrs = prim.get("attributes", {})

            # --- Camera without RenderProduct ---
            if prim_type == "Camera":
                if path not in render_product_cameras:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="sensor.camera_no_render_product",
                        pack=self.pack,
                        severity="warning",
                        prim_path=path,
                        message="Camera has no RenderProduct — it can't produce images.",
                        detail=(
                            f"Camera '{path}' exists but no RenderProduct "
                            f"references it. Without a RenderProduct, the "
                            f"camera cannot render frames for Replicator, "
                            f"ROS2 publishers, or viewport display."
                        ),
                        evidence={"prim_type": prim_type},
                        auto_fixable=True,
                    ))

            # --- LiDAR/RTX sensor without OmniGraph ---
            if any(kw in prim_type.lower() for kw in ("lidar", "radar")) or \
               any("LidarSensor" in s or "RtxSensor" in s for s in schemas):
                # Check if there's any IsaacSensor read node for this prim
                has_og_reader = any(
                    path in str(node.get("inputs", {}))
                    for node in og_nodes
                )
                if not has_og_reader and og_nodes:  # only warn if we have OG data
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="sensor.lidar_no_omnigraph",
                        pack=self.pack,
                        severity="info",
                        prim_path=path,
                        message="LiDAR/RTX sensor has no OmniGraph reader node.",
                        detail=(
                            f"Sensor '{path}' appears to be a LiDAR or RTX "
                            f"sensor but no OmniGraph node reads from it. "
                            f"Data won't flow to ROS2 or other consumers."
                        ),
                        evidence={"prim_type": prim_type, "schemas": schemas},
                        auto_fixable=False,
                    ))

            # --- IMU / ContactSensor not on physics body ---
            is_physics_sensor = any(
                kw in prim_type for kw in ("IMUSensor", "ContactSensor")
            ) or any(
                kw in s for s in schemas
                for kw in ("IsaacImuSensor", "IsaacContactSensor")
            )
            if is_physics_sensor:
                # Walk up to parent and check for RigidBodyAPI
                parent_path = "/".join(path.split("/")[:-1])
                parent = prim_map.get(parent_path, {})
                parent_schemas = parent.get("schemas", [])
                # Check parent and grandparent
                gp_path = "/".join(parent_path.split("/")[:-1])
                gp = prim_map.get(gp_path, {})
                gp_schemas = gp.get("schemas", [])

                has_physics = (
                    "PhysicsRigidBodyAPI" in parent_schemas
                    or "PhysicsRigidBodyAPI" in gp_schemas
                    or "PhysicsArticulationRootAPI" in parent_schemas
                    or "PhysicsArticulationRootAPI" in gp_schemas
                )
                if not has_physics:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="sensor.physics_sensor_no_body",
                        pack=self.pack,
                        severity="warning",
                        prim_path=path,
                        message="Physics sensor not attached to a physics body.",
                        detail=(
                            f"Sensor '{path}' is a physics-based sensor "
                            f"(IMU/Contact) but its parent '{parent_path}' "
                            f"has no RigidBody or ArticulationRoot. The "
                            f"sensor won't receive any physics data."
                        ),
                        evidence={
                            "sensor_type": prim_type,
                            "parent_schemas": parent_schemas,
                        },
                        auto_fixable=False,
                    ))

        return findings
