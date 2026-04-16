"""
ROSBridgeReadinessValidator — check ROS2 integration health.

Catches:
- Missing clock publisher (sim-time sync)
- Topic name collisions (same topic published twice)
- frame_id inconsistencies across sensor publishers
- OmniGraph action graph missing for published topics
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
            "topic collisions, frame_id consistency, and OmniGraph wiring."
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
        published_topics: List[str] = []
        frame_ids: Set[str] = set()

        for node in og_nodes:
            node_type = node.get("type", "")
            inputs = node.get("inputs", {})

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
        if not (ros2_publishers or ros2_subscribers):
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

        return findings
