"""ros2_control bridge for the assist extension (CRM-A1).

Thin Python bridge connecting Isaac Sim's in-Kit world to the upstream
ros2_control admittance / impedance controllers. The bridge handles
two directions:

  Isaac → ROS:  republishes F/T sensor readings on the topic the
                ros2_control admittance_controller subscribes to.
  ROS → Isaac:  subscribes to ros2_control feedback topics
                (controller state, current pose, status) so Isaac-side
                tool handlers can read compliance state without
                duplicating ros2_control's bookkeeping.

## Architecture: Option A (external graph hop)

Per the Compliance & Force-Feedback spec Section 13 (Open Question 1),
this implementation uses Option A: a standard ROS2 hop via the
existing isaacsim.ros2.bridge extension. Each Isaac F/T sample
crosses the ros2_bridge boundary, gets published on a real DDS topic,
and ros2_control's admittance_controller subscribes to it.

Trade-off accepted: ~10 ms extra latency vs Option B (an in-Kit
direct port that bypasses DDS entirely). The win of Option A is
reuse of upstream ros2_controllers maintenance — no need to vendor
admittance maths in-tree. If the 500 Hz step budget (Section 10) is
ever breached on real hardware, Option B can replace this file
without touching the tool handlers.

## Soft-failure on missing rclpy

The bridge is loaded by the extension at startup. To keep the
extension importable in environments without rclpy installed (CI,
ext-folder discovery on stripped Isaac builds), every rclpy import
is guarded; if rclpy is absent the bridge stays in a stub state and
its public methods return ``{"available": False, ...}`` rather than
raising.

Per `docs/specs/2026-05-11-contact-rich-manipulation-spec.md` Section
18.1 task CRM-A1.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# rclpy guard
# ---------------------------------------------------------------------------

try:
    import rclpy  # type: ignore[import-not-found]
    from rclpy.node import Node  # type: ignore[import-not-found]
    from rclpy.qos import QoSProfile, ReliabilityPolicy  # type: ignore[import-not-found]

    _RCLPY_AVAILABLE = True
except Exception as _exc:  # pragma: no cover — exercised only in stripped envs
    rclpy = None  # type: ignore[assignment]
    Node = object  # type: ignore[assignment,misc]
    QoSProfile = None  # type: ignore[assignment]
    ReliabilityPolicy = None  # type: ignore[assignment]
    _RCLPY_AVAILABLE = False
    _RCLPY_IMPORT_ERROR = str(_exc)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class FTSensorPublisher:
    """One Isaac F/T sensor wired to a ROS2 publisher."""

    prim_path: str
    publish_topic: str
    handle: Any = None  # rclpy.Publisher when active; None in stub mode


@dataclass
class ComplianceStateSubscriber:
    """One ros2_control feedback subscription."""

    controller_name: str
    topic: str
    callback: Callable[[Dict[str, Any]], None]
    handle: Any = None
    last_message: Optional[Dict[str, Any]] = None


@dataclass
class BridgeHealth:
    """Snapshot of bridge state for `health_check()` callers."""

    available: bool
    node_started: bool
    ft_publishers: int
    state_subscribers: int
    rclpy_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class Ros2ControlBridge:
    """Single-process bridge wiring Isaac F/T to ros2_control topics.

    Lifecycle:
      1. ``__init__`` validates rclpy presence; stays in stub if missing.
      2. ``start()`` spins up the rclpy node (idempotent).
      3. ``attach_ft_sensor()`` and ``subscribe_compliance_state()``
         register publishers / subscribers; safe to call before
         ``start()``.
      4. ``health_check()`` returns a typed snapshot for tools.
      5. ``stop()`` tears down the node + publishers.
    """

    DEFAULT_NODE_NAME: str = "isaac_assist_ros2_control_bridge"
    DEFAULT_QOS_DEPTH: int = 10

    def __init__(
        self,
        node_name: str = DEFAULT_NODE_NAME,
        domain_id: int = 0,
    ) -> None:
        self._node_name: str = node_name
        self._domain_id: int = int(domain_id)
        self._node: Optional[Node] = None
        self._ft_publishers: Dict[str, FTSensorPublisher] = {}
        self._state_subscribers: Dict[str, ComplianceStateSubscriber] = {}
        self._started: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> Dict[str, Any]:
        """Spin up the rclpy node. Idempotent — second call is a no-op.

        Returns:
            Dict with:
              * ``success`` (bool) — Section 19 honesty key. True when the
                node is up after this call (whether newly created or
                already-started). False on rclpy missing or creation error.
              * ``available`` (bool) — rclpy importable in this process.
              * ``started`` (bool) — node currently running.
              * ``reason`` (str, optional) — populated on failure or no-op.
        """
        if not _RCLPY_AVAILABLE:
            return {
                "success": False,
                "available": False,
                "started": False,
                "reason": "rclpy not importable",
            }
        if self._started:
            return {
                "success": True,
                "available": True,
                "started": True,
                "reason": "already started",
            }
        try:
            if not rclpy.ok():
                rclpy.init(args=None)
            self._node = rclpy.create_node(self._node_name)
            self._started = True
            logger.info(
                "[ros2_control_bridge] node started: name=%s domain_id=%d",
                self._node_name,
                self._domain_id,
            )
            return {"success": True, "available": True, "started": True}
        except Exception as exc:
            logger.exception("[ros2_control_bridge] start() failed")
            return {
                "success": False,
                "available": True,
                "started": False,
                "reason": f"rclpy node creation failed: {exc}",
            }

    def stop(self) -> Dict[str, Any]:
        """Destroy publishers, subscribers, then the node. Idempotent.

        Returns:
            Dict with:
              * ``success`` (bool) — Section 19 honesty key. True when
                the bridge is in a stopped state after this call.
              * ``available`` (bool) — rclpy importable.
              * ``stopped`` (bool) — node currently torn down.
              * ``reason`` (str, optional) — populated on no-op or failure.
        """
        if not self._started:
            return {
                "success": True,
                "available": _RCLPY_AVAILABLE,
                "stopped": True,
                "reason": "not started",
            }
        try:
            for pub in self._ft_publishers.values():
                if pub.handle is not None and self._node is not None:
                    try:
                        self._node.destroy_publisher(pub.handle)
                    except Exception:
                        pass
                    pub.handle = None
            for sub in self._state_subscribers.values():
                if sub.handle is not None and self._node is not None:
                    try:
                        self._node.destroy_subscription(sub.handle)
                    except Exception:
                        pass
                    sub.handle = None
            if self._node is not None:
                try:
                    self._node.destroy_node()
                except Exception:
                    pass
            self._node = None
            self._started = False
            return {"success": True, "available": True, "stopped": True}
        except Exception as exc:
            logger.exception("[ros2_control_bridge] stop() failed")
            return {
                "success": False,
                "available": True,
                "stopped": False,
                "reason": f"shutdown failed: {exc}",
            }

    # ------------------------------------------------------------------
    # F/T sensor → ROS publisher
    # ------------------------------------------------------------------

    def attach_ft_sensor(self, prim_path: str, publish_topic: str) -> Dict[str, Any]:
        """Register an F/T sensor Isaac→ROS bridge.

        Stores the prim_path → topic mapping so tool handlers can call
        ``publish_ft_reading(prim_path, wrench)`` from the simulation
        tick. If rclpy is available and ``start()`` has been called,
        the underlying ROS publisher is created immediately.

        Args:
            prim_path: USD path of the F/T sensor prim (e.g.
                ``/World/Franka/wrist/ForceTorqueSensor``).
            publish_topic: ROS topic name where wrench should be
                published (default expected by ros2_control's
                admittance_controller is ``~/force_torque_sensor_broadcaster``).

        Returns:
            ``{"available": bool, "registered": bool, "topic": str, ...}``.
        """
        if not prim_path or not publish_topic:
            return {
                "available": _RCLPY_AVAILABLE,
                "registered": False,
                "reason": "prim_path and publish_topic must be non-empty",
            }

        pub = self._ft_publishers.get(prim_path)
        if pub is None:
            pub = FTSensorPublisher(prim_path=prim_path, publish_topic=publish_topic)
            self._ft_publishers[prim_path] = pub
        else:
            pub.publish_topic = publish_topic

        if _RCLPY_AVAILABLE and self._started and self._node is not None:
            self._ensure_ft_publisher_handle(pub)

        return {
            "available": _RCLPY_AVAILABLE,
            "registered": True,
            "prim_path": prim_path,
            "topic": publish_topic,
            "live": pub.handle is not None,
        }

    def _ensure_ft_publisher_handle(self, pub: FTSensorPublisher) -> None:
        """Lazily create the rclpy publisher when the node is up."""
        if pub.handle is not None or self._node is None:
            return
        try:
            from geometry_msgs.msg import WrenchStamped  # type: ignore[import-not-found]
        except Exception:
            logger.warning(
                "[ros2_control_bridge] geometry_msgs not importable; "
                "publisher for %s stays in stub mode",
                pub.prim_path,
            )
            return
        qos = QoSProfile(depth=self.DEFAULT_QOS_DEPTH) if QoSProfile else self.DEFAULT_QOS_DEPTH
        try:
            pub.handle = self._node.create_publisher(WrenchStamped, pub.publish_topic, qos)
        except Exception as exc:
            logger.warning(
                "[ros2_control_bridge] could not create publisher for %s: %s",
                pub.publish_topic,
                exc,
            )

    def detach_ft_sensor(self, prim_path: str) -> Dict[str, Any]:
        """Remove an F/T sensor registration. Destroys the underlying
        publisher if one was active. Idempotent.
        """
        pub = self._ft_publishers.pop(prim_path, None)
        if pub is None:
            return {"available": _RCLPY_AVAILABLE, "removed": False, "reason": "not registered"}
        if pub.handle is not None and self._node is not None:
            try:
                self._node.destroy_publisher(pub.handle)
            except Exception:
                pass
        return {"available": _RCLPY_AVAILABLE, "removed": True, "prim_path": prim_path}

    # ------------------------------------------------------------------
    # ros2_control state subscriber
    # ------------------------------------------------------------------

    def subscribe_compliance_state(
        self,
        controller_name: str,
        topic: str,
        callback: Callable[[Dict[str, Any]], None],
    ) -> Dict[str, Any]:
        """Subscribe to a ros2_control controller's state topic.

        The callback receives a small dict — not the raw ROS message —
        with whatever fields the bridge knows how to extract for that
        controller. For admittance_controller, the extracted dict has
        keys like ``{"current_pose": [...], "current_wrench": [...],
        "is_engaged": bool}``.

        Args:
            controller_name: ros2_control controller identifier
                (e.g. ``admittance_controller``).
            topic: full topic path
                (e.g. ``/admittance_controller/state``).
            callback: invoked on every received message.

        Returns:
            ``{"available": bool, "subscribed": bool, ...}``.
        """
        if not controller_name or not topic:
            return {
                "available": _RCLPY_AVAILABLE,
                "subscribed": False,
                "reason": "controller_name and topic must be non-empty",
            }

        sub = ComplianceStateSubscriber(
            controller_name=controller_name,
            topic=topic,
            callback=callback,
        )
        self._state_subscribers[controller_name] = sub

        if _RCLPY_AVAILABLE and self._started and self._node is not None:
            self._ensure_subscriber_handle(sub)

        return {
            "available": _RCLPY_AVAILABLE,
            "subscribed": True,
            "controller": controller_name,
            "topic": topic,
            "live": sub.handle is not None,
        }

    def _ensure_subscriber_handle(self, sub: ComplianceStateSubscriber) -> None:
        """Lazily create the rclpy subscription when the node is up."""
        if sub.handle is not None or self._node is None:
            return
        try:
            from std_msgs.msg import String  # type: ignore[import-not-found]
        except Exception:
            logger.warning(
                "[ros2_control_bridge] std_msgs not importable; subscriber for %s stays in stub mode",
                sub.controller_name,
            )
            return

        def _on_message(msg: Any) -> None:
            extracted = self._extract_state_fields(sub.controller_name, msg)
            sub.last_message = extracted
            try:
                sub.callback(extracted)
            except Exception:
                logger.exception(
                    "[ros2_control_bridge] callback raised on topic %s",
                    sub.topic,
                )

        qos = QoSProfile(depth=self.DEFAULT_QOS_DEPTH) if QoSProfile else self.DEFAULT_QOS_DEPTH
        try:
            sub.handle = self._node.create_subscription(String, sub.topic, _on_message, qos)
        except Exception as exc:
            logger.warning(
                "[ros2_control_bridge] could not create subscription for %s: %s",
                sub.topic,
                exc,
            )

    @staticmethod
    def _extract_state_fields(controller_name: str, msg: Any) -> Dict[str, Any]:
        """Pull the fields a tool handler is likely to read.

        This is a per-controller extractor. Today only the
        ``admittance_controller`` shape is mapped; everything else
        falls back to a generic ``{"raw_data": str(msg)}``.
        """
        if controller_name == "admittance_controller":
            return {
                "controller": controller_name,
                "current_pose": getattr(msg, "current_pose", None),
                "current_wrench": getattr(msg, "current_wrench", None),
                "is_engaged": getattr(msg, "is_engaged", None),
            }
        return {"controller": controller_name, "raw_data": str(msg)}

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def health_check(self) -> BridgeHealth:
        """Snapshot bridge state for tool handlers."""
        return BridgeHealth(
            available=_RCLPY_AVAILABLE,
            node_started=self._started,
            ft_publishers=len(self._ft_publishers),
            state_subscribers=len(self._state_subscribers),
            rclpy_error=None if _RCLPY_AVAILABLE else _RCLPY_IMPORT_ERROR,
        )

    @property
    def started(self) -> bool:
        return self._started

    @property
    def node(self) -> Optional[Node]:
        return self._node


# ---------------------------------------------------------------------------
# Module-level singleton (managed by extension)
# ---------------------------------------------------------------------------

_BRIDGE_SINGLETON: Optional[Ros2ControlBridge] = None


def get_bridge() -> Ros2ControlBridge:
    """Return the process-wide bridge instance, creating it if absent."""
    global _BRIDGE_SINGLETON
    if _BRIDGE_SINGLETON is None:
        _BRIDGE_SINGLETON = Ros2ControlBridge()
    return _BRIDGE_SINGLETON


def reset_bridge_for_testing() -> None:
    """Test helper — drops the singleton so the next call rebuilds it."""
    global _BRIDGE_SINGLETON
    if _BRIDGE_SINGLETON is not None:
        try:
            _BRIDGE_SINGLETON.stop()
        except Exception:
            pass
    _BRIDGE_SINGLETON = None


# Public exports
__all__ = [
    "FTSensorPublisher",
    "ComplianceStateSubscriber",
    "BridgeHealth",
    "Ros2ControlBridge",
    "get_bridge",
    "reset_bridge_for_testing",
]
