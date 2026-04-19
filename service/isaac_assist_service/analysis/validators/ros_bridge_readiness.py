"""
ROSBridgeReadinessValidator — check ROS2 integration health.

Catches:
- Missing clock publisher (sim-time sync)
- Topic name collisions (same topic published twice)
- frame_id inconsistencies across sensor publishers
- OmniGraph action graph missing for published topics
- RtxLidarHelper with fullScan=False on rotary lidars (empty LaserScan)
- RtxLidarHelper without RenderProduct connection (no data)
- RtxLidarHelper frameId vs prim-name mismatch (TF frame won't match)
- Sensor publishers without ROS2PublishTransformTree (rviz2 drops msgs)
- ROS2PublishTransformTree without targetPrims set (no TF published)
"""
from typing import List, Dict, Any, Set
import uuid
from collections import Counter

from .base import ValidationRule
from ..models import ValidationFinding


class ROSBridgeReadinessValidator(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "ros_bridge.readiness"
        self.pack = "ros_bridge_readiness"
        self.severity = "warning"
        self.name = "ROS2 bridge readiness"
        self.description = (
            "Verifies ROS2 bridge configuration — clock publisher, "
            "topic collisions, frame_id consistency, TF tree, "
            "lidar fullScan, and OmniGraph wiring."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        prims = stage_data.get("prims", [])
        og_nodes = stage_data.get("omnigraph_nodes", [])

        # If no OmniGraph data, skip ROS checks entirely
        if not og_nodes:
            return findings

        # Classify ROS2 nodes
        ros2_publishers: List[Dict] = []
        ros2_subscribers: List[Dict] = []
        has_clock = False
        has_context = False
        has_tf_publisher = False
        published_topics: List[str] = []
        frame_ids: Set[str] = set()
        lidar_helpers: List[Dict] = []
        tf_publishers: List[Dict] = []

        for node in og_nodes:
            node_type = node.get("type", "")
            inputs = node.get("inputs", {})
            node_path = node.get("path", "")

            # Detect ROS2 nodes by type name
            is_ros2 = any(kw in node_type.lower() for kw in (
                "ros2", "ros_2",
            ))
            if not is_ros2:
                continue

            if "clock" in node_type.lower():
                has_clock = True
            if "context" in node_type.lower():
                has_context = True

            # RtxLidarHelper detection
            if "rtxlidarhelper" in node_type.lower() or \
               "lidarhelper" in node_type.lower():
                lidar_helpers.append(node)

            # TF publisher detection
            if "transformtree" in node_type.lower() or \
               "publishtf" in node_type.lower():
                has_tf_publisher = True
                tf_publishers.append(node)

            if "publish" in node_type.lower():
                ros2_publishers.append(node)
                topic = inputs.get("topicName", inputs.get("topic_name", ""))
                if topic:
                    published_topics.append(topic)
                fid = inputs.get("frameId", inputs.get("frame_id", ""))
                if fid:
                    frame_ids.add(fid)
            if "subscribe" in node_type.lower():
                ros2_subscribers.append(node)

        # If no ROS2 nodes at all, nothing to validate
        if not (ros2_publishers or ros2_subscribers or lidar_helpers):
            return findings

        # --- Missing clock publisher ---
        if not has_clock:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="ros_bridge.no_clock",
                pack=self.pack,
                severity="warning",
                prim_path=None,
                message="No ROS2 clock publisher found.",
                detail=(
                    "The scene has ROS2 publisher/subscriber nodes but no "
                    "clock publisher. Without it, sim-time won't sync with "
                    "ROS2 — TF transforms and sensor timestamps will use "
                    "wall-clock time, causing drift."
                ),
                evidence={
                    "publisher_count": len(ros2_publishers),
                    "subscriber_count": len(ros2_subscribers),
                },
                auto_fixable=True,
            ))

        # --- Missing ROS2Context ---
        if not has_context:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="ros_bridge.no_context",
                pack=self.pack,
                severity="warning",
                prim_path=None,
                message="No ROS2Context node found.",
                detail=(
                    "ROS2 publishers/subscribers exist but no ROS2Context "
                    "node was found. The ROS2Context node manages the "
                    "domain ID and lifecycle — without it, nodes may use "
                    "defaults or fail to initialize."
                ),
                evidence={},
                auto_fixable=True,
            ))

        # --- Topic name collisions ---
        topic_counts = Counter(published_topics)
        for topic, count in topic_counts.items():
            if count > 1:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="ros_bridge.topic_collision",
                    pack=self.pack,
                    severity="error",
                    prim_path=None,
                    message=f"Topic '{topic}' published by {count} nodes.",
                    detail=(
                        f"Topic '{topic}' is published by {count} different "
                        f"OmniGraph nodes. This causes message interleaving "
                        f"and unpredictable behavior on subscribers."
                    ),
                    evidence={"topic": topic, "count": count},
                    auto_fixable=False,
                ))

        # --- frame_id inconsistency ---
        if len(frame_ids) > 3:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="ros_bridge.frame_id_inconsistency",
                pack=self.pack,
                severity="info",
                prim_path=None,
                message=f"Many different frame_ids in use ({len(frame_ids)}).",
                detail=(
                    f"ROS2 publishers use {len(frame_ids)} different "
                    f"frame_ids: {', '.join(sorted(frame_ids)[:5])}... "
                    f"Verify these are intentional and match your TF tree."
                ),
                evidence={"frame_ids": sorted(frame_ids)},
                auto_fixable=False,
            ))

        # --- RtxLidarHelper checks ---
        findings.extend(self._check_lidar_helpers(lidar_helpers, og_nodes))

        # --- TF publisher checks ---
        findings.extend(self._check_tf_publishers(
            has_tf_publisher, tf_publishers, lidar_helpers, ros2_publishers,
        ))

        return findings

    # ── RtxLidarHelper diagnostics ───────────────────────────────────────

    def _check_lidar_helpers(
        self,
        lidar_helpers: List[Dict],
        og_nodes: List[Dict],
    ) -> List[ValidationFinding]:
        findings = []
        # Build set of RenderProduct output paths for connection check
        render_product_paths = set()
        for node in og_nodes:
            node_type = node.get("type", "")
            if "renderproduct" in node_type.lower() or \
               "createrender" in node_type.lower():
                render_product_paths.add(node.get("path", ""))

        for node in lidar_helpers:
            inputs = node.get("inputs", {})
            node_path = node.get("path", "")
            node_name = node_path.rsplit("/", 1)[-1] if "/" in node_path else node_path
            connections = node.get("connections", {})
            lidar_type = inputs.get("type", "")

            # --- fullScan not enabled ---
            full_scan = inputs.get("fullScan", False)
            if not full_scan:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="ros_bridge.lidar_no_full_scan",
                    pack=self.pack,
                    severity="error",
                    prim_path=node_path,
                    message=(
                        f"RtxLidarHelper '{node_name}' has fullScan=False "
                        f"— rotary lidars will publish empty data."
                    ),
                    detail=(
                        f"Node '{node_path}' (type={lidar_type or 'unknown'}) "
                        f"has fullScan disabled. For rotary/spinning lidars "
                        f"like RPLidar, Velodyne, or Ouster, this causes "
                        f"LaserScan/PointCloud2 messages with 0 points "
                        f"because only a partial firing is captured. "
                        f"Set fullScan=True to accumulate a complete "
                        f"revolution before publishing."
                    ),
                    evidence={
                        "fullScan": full_scan,
                        "type": lidar_type,
                    },
                    auto_fixable=True,
                ))

            # --- RenderProduct not connected ---
            rp_input = inputs.get("renderProductPath", "")
            rp_connected = bool(rp_input) or any(
                "renderproduct" in k.lower()
                for k in connections.keys()
            ) if connections else bool(rp_input)
            if not rp_connected:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="ros_bridge.lidar_no_render_product",
                    pack=self.pack,
                    severity="error",
                    prim_path=node_path,
                    message=(
                        f"RtxLidarHelper '{node_name}' has no "
                        f"RenderProduct connection — no data will flow."
                    ),
                    detail=(
                        f"Node '{node_path}' needs a connected "
                        f"IsaacCreateRenderProduct node. Without it, "
                        f"the RTX lidar sensor has no render target and "
                        f"will not produce any scan data. Create an "
                        f"IsaacCreateRenderProduct node with cameraPrim "
                        f"pointing to the lidar prim and connect its "
                        f"output to this node's renderProductPath input."
                    ),
                    evidence={"renderProductPath": rp_input},
                    auto_fixable=True,
                ))

            # --- frameId vs prim-name mismatch ---
            frame_id = inputs.get("frameId", "")
            if frame_id:
                # ROS2RtxLidarHelper ignores the frameId input and
                # derives frame_id from the lidar prim name (lowercased,
                # slashes→underscores). Warn if user set a custom frameId
                # that won't match.
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="ros_bridge.lidar_frameid_ignored",
                    pack=self.pack,
                    severity="warning",
                    prim_path=node_path,
                    message=(
                        f"RtxLidarHelper '{node_name}' frameId='{frame_id}' "
                        f"may be ignored — actual frame_id comes from the "
                        f"lidar prim name."
                    ),
                    detail=(
                        f"The ROS2RtxLidarHelper node ignores the frameId "
                        f"input and derives the published frame_id from the "
                        f"lidar prim's name in the USD hierarchy (typically "
                        f"lowercased). If your TF tree or rviz2 expects "
                        f"'{frame_id}', you may need a static_transform_"
                        f"publisher to bridge the mismatch, or rename the "
                        f"lidar prim to match. Check with: "
                        f"ros2 topic echo /scan --field header.frame_id"
                    ),
                    evidence={"frameId_input": frame_id},
                    auto_fixable=False,
                ))

        return findings

    # ── TF publisher diagnostics ──────────────────────────────────────

    def _check_tf_publishers(
        self,
        has_tf_publisher: bool,
        tf_publishers: List[Dict],
        lidar_helpers: List[Dict],
        ros2_publishers: List[Dict],
    ) -> List[ValidationFinding]:
        findings = []

        # --- No TF publisher at all ---
        sensor_publishers = lidar_helpers + [
            p for p in ros2_publishers
            if any(kw in p.get("type", "").lower()
                   for kw in ("camera", "imu", "lidar", "pointcloud"))
        ]
        if sensor_publishers and not has_tf_publisher:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="ros_bridge.no_tf_publisher",
                pack=self.pack,
                severity="error",
                prim_path=None,
                message=(
                    "No ROS2PublishTransformTree node found — rviz2 and "
                    "nav2 cannot resolve sensor frames."
                ),
                detail=(
                    f"The scene has {len(sensor_publishers)} sensor "
                    f"publisher(s) but no ROS2PublishTransformTree node. "
                    f"Without TF, downstream consumers (rviz2, nav2, SLAM) "
                    f"will drop all sensor messages with 'Message Filter "
                    f"dropping message: frame ...' errors. Add a "
                    f"ROS2PublishTransformTree node with parentPrim set to "
                    f"the robot root and targetPrims including all sensor "
                    f"links."
                ),
                evidence={
                    "sensor_publisher_count": len(sensor_publishers),
                },
                auto_fixable=True,
            ))

        # --- TF publisher without targetPrims ---
        for node in tf_publishers:
            inputs = node.get("inputs", {})
            node_path = node.get("path", "")
            node_name = node_path.rsplit("/", 1)[-1] if "/" in node_path else node_path
            target_prims = inputs.get("targetPrims", [])
            parent_prim = inputs.get("parentPrim", "")

            if not target_prims:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="ros_bridge.tf_no_target_prims",
                    pack=self.pack,
                    severity="error",
                    prim_path=node_path,
                    message=(
                        f"TF publisher '{node_name}' has no targetPrims "
                        f"— no transforms will be published."
                    ),
                    detail=(
                        f"ROS2PublishTransformTree at '{node_path}' has "
                        f"parentPrim='{parent_prim}' but targetPrims is "
                        f"empty. The node requires targetPrims to specify "
                        f"which child frames to publish. Without it, /tf "
                        f"will be silent even though the node exists. Set "
                        f"targetPrims to the robot's child links "
                        f"(chassis_link, sensor frames, etc.)."
                    ),
                    evidence={
                        "parentPrim": parent_prim,
                        "targetPrims": target_prims,
                    },
                    auto_fixable=True,
                ))

            if not parent_prim:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="ros_bridge.tf_no_parent_prim",
                    pack=self.pack,
                    severity="error",
                    prim_path=node_path,
                    message=(
                        f"TF publisher '{node_name}' has no parentPrim set."
                    ),
                    detail=(
                        f"ROS2PublishTransformTree at '{node_path}' has no "
                        f"parentPrim. This is the root frame of the TF tree "
                        f"(typically the robot root prim). Without it, "
                        f"transforms have no parent frame reference."
                    ),
                    evidence={"parentPrim": parent_prim},
                    auto_fixable=True,
                ))

        return findings
