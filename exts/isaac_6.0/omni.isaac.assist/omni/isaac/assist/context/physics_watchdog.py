"""Fail-fast rigid-body transform watchdog for live Isaac Sim stages."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import carb


class PhysicsWatchdog:
    """Pause the timeline before invalid articulation transforms flood Kit."""

    def __init__(self) -> None:
        self._subscription = None
        self._enabled = False
        self._root_path = ""
        self._max_translation = 100.0
        self._max_frame_displacement = 0.1
        self._previous_positions: Dict[str, tuple[float, float, float]] = {}
        self._stage_identifier = ""
        self._tripped = False
        self._trip_reason = ""
        self._trip_paths = []
        self._trip_time = None
        self._body_count = 0

    def start(self) -> None:
        if self._subscription is not None:
            return
        import omni.kit.app

        self._subscription = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(
                self._on_update, name="IsaacAssist-PhysicsWatchdog"
            )
        )
        carb.log_warn("[IsaacAssist] Physics watchdog registered (disabled)")

    def stop(self) -> None:
        self._subscription = None
        self._enabled = False
        self._previous_positions.clear()

    def configure(
        self,
        root_path: str,
        max_translation: float = 100.0,
        max_frame_displacement: float = 0.1,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        if not root_path or not root_path.startswith("/"):
            raise ValueError("root_path must be an absolute USD prim path")
        if not math.isfinite(max_translation) or max_translation <= 0:
            raise ValueError("max_translation must be finite and positive")
        if not math.isfinite(max_frame_displacement) or max_frame_displacement <= 0:
            raise ValueError("max_frame_displacement must be finite and positive")
        self._root_path = root_path
        self._max_translation = float(max_translation)
        self._max_frame_displacement = float(max_frame_displacement)
        self._enabled = bool(enabled)
        self.reset()
        carb.log_warn(
            "[IsaacAssist] Physics watchdog configured: "
            f"root={root_path} max_translation={max_translation} "
            f"max_frame_displacement={max_frame_displacement} enabled={enabled}"
        )
        return self.state()

    def enable(self) -> Dict[str, Any]:
        if not self._root_path:
            raise RuntimeError("Configure a root_path before enabling the watchdog")
        self._enabled = True
        self.reset()
        return self.state()

    def disable(self) -> Dict[str, Any]:
        self._enabled = False
        self._previous_positions.clear()
        return self.state()

    def reset(self) -> Dict[str, Any]:
        self._previous_positions.clear()
        self._stage_identifier = ""
        self._tripped = False
        self._trip_reason = ""
        self._trip_paths = []
        self._trip_time = None
        self._body_count = 0
        return self.state()

    def state(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "root_path": self._root_path,
            "max_translation": self._max_translation,
            "max_frame_displacement": self._max_frame_displacement,
            "tracked_body_count": self._body_count,
            "tripped": self._tripped,
            "trip_reason": self._trip_reason,
            "trip_paths": list(self._trip_paths),
            "trip_time": self._trip_time,
        }

    def _trip(self, reason: str, paths) -> None:
        import omni.timeline

        self._tripped = True
        self._trip_reason = reason
        self._trip_paths = list(paths)[:20]
        timeline = omni.timeline.get_timeline_interface()
        self._trip_time = float(timeline.get_current_time())
        timeline.pause()
        carb.log_error(
            "[IsaacAssist] PHYSICS WATCHDOG PAUSED TIMELINE: "
            f"{reason}; paths={self._trip_paths}"
        )

    def _on_update(self, _event) -> None:
        if not self._enabled or self._tripped:
            return
        try:
            import omni.timeline
            import omni.usd
            from pxr import Usd, UsdGeom, UsdPhysics

            timeline = omni.timeline.get_timeline_interface()
            if not timeline.is_playing():
                return
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return
            stage_identifier = stage.GetRootLayer().identifier
            if stage_identifier != self._stage_identifier:
                self._stage_identifier = stage_identifier
                self._previous_positions.clear()

            root = stage.GetPrimAtPath(self._root_path)
            if not root.IsValid():
                self._trip("configured root prim disappeared", [self._root_path])
                return

            cache = UsdGeom.XformCache(Usd.TimeCode.Default())
            positions: Dict[str, tuple[float, float, float]] = {}
            invalid = []
            jumps = []
            for prim in Usd.PrimRange(root):
                if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    continue
                path = str(prim.GetPath())
                matrix = cache.GetLocalToWorldTransform(prim)
                translation = tuple(
                    float(value)
                    for value in matrix.ExtractTranslation()
                )
                positions[path] = translation
                if (
                    not all(
                        math.isfinite(float(matrix[row][column]))
                        for row in range(4)
                        for column in range(4)
                    )
                    or max(abs(value) for value in translation) > self._max_translation
                ):
                    invalid.append(path)
                    continue
                previous = self._previous_positions.get(path)
                if previous is not None:
                    displacement = math.sqrt(
                        sum((translation[index] - previous[index]) ** 2 for index in range(3))
                    )
                    if displacement > self._max_frame_displacement:
                        jumps.append(path)

            self._body_count = len(positions)
            self._previous_positions = positions
            if invalid:
                self._trip("non-finite or out-of-envelope rigid-body transform", invalid)
            elif jumps:
                self._trip("implausible one-frame rigid-body displacement", jumps)
        except Exception as exc:
            carb.log_warn(f"[IsaacAssist] Physics watchdog check failed: {exc}")


_WATCHDOG: Optional[PhysicsWatchdog] = None


def get_physics_watchdog() -> PhysicsWatchdog:
    global _WATCHDOG
    if _WATCHDOG is None:
        _WATCHDOG = PhysicsWatchdog()
    return _WATCHDOG
