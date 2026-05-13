"""Phase 81 — Multi-rate physics: high-rate vision + low-rate planning.

Provides `MultiRateConfig`, a dataclass that describes the update-rate budget
for each subsystem in an Isaac Sim simulation loop:

- physics  : 240 Hz  (PhysX integration)
- vision   :  30 Hz  (camera / perception pipeline)
- planning :  10 Hz  (motion planner)
- control  : 120 Hz  (joint controller)
- logging  :   1 Hz  (telemetry / diagnostics)

The config also accepts user-defined ``RateChannel`` extras.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 81.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


PHASE_ID = 81
PHASE_TITLE = "Multi-rate physics: high-rate vision + low-rate planning"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 81",
    }


# ---------------------------------------------------------------------------
# RateChannel
# ---------------------------------------------------------------------------

@dataclass
class RateChannel:
    """A named update-rate channel with an optional priority hint.

    Parameters
    ----------
    name:
        Unique identifier, e.g. ``"vision"`` or ``"planning"``.
    hz:
        Update frequency in Hertz.  Must be > 0.
    priority:
        Higher value = higher scheduling priority (default 0).
    description:
        Human-readable note about the channel's role.
    """

    name: str
    hz: float
    priority: int = 0
    description: str = ""

    def __post_init__(self) -> None:
        if self.hz <= 0:
            raise ValueError(f"RateChannel '{self.name}': hz must be > 0, got {self.hz}")


# ---------------------------------------------------------------------------
# MultiRateConfig
# ---------------------------------------------------------------------------

@dataclass
class MultiRateConfig:
    """Simulation multi-rate configuration.

    Attributes
    ----------
    physics_hz:
        PhysX integration rate (Hz).  All other rates must be <= this value
        for a physically consistent simulation (though the class does not
        *enforce* this for planning/vision — only control_hz > physics_hz
        triggers a warning).
    vision_hz:
        Camera / perception pipeline rate (Hz).
    planning_hz:
        Motion-planner invocation rate (Hz).
    control_hz:
        Joint-controller rate (Hz).
    logging_hz:
        Telemetry / diagnostic logging rate (Hz).
    extras:
        Additional user-defined ``RateChannel`` objects, keyed by channel name.
    """

    physics_hz: float = 240.0
    vision_hz: float = 30.0
    planning_hz: float = 10.0
    control_hz: float = 120.0
    logging_hz: float = 1.0
    extras: Dict[str, "RateChannel"] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Built-in channel descriptors
    # ------------------------------------------------------------------

    _BUILTIN_DESCRIPTIONS: Dict[str, str] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_BUILTIN_DESCRIPTIONS",
            {
                "physics": "PhysX integration step",
                "vision": "Camera / perception pipeline",
                "planning": "Motion planner invocation",
                "control": "Joint controller update",
                "logging": "Telemetry / diagnostic logging",
            },
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def as_channels(self) -> List[RateChannel]:
        """Return all rate channels — 5 builtins + any extras.

        Built-in priority assignment (higher = more time-critical):
            physics  : 4
            control  : 3
            vision   : 2
            planning : 1
            logging  : 0
        """
        builtins: List[RateChannel] = [
            RateChannel(
                name="physics",
                hz=self.physics_hz,
                priority=4,
                description=self._BUILTIN_DESCRIPTIONS["physics"],
            ),
            RateChannel(
                name="control",
                hz=self.control_hz,
                priority=3,
                description=self._BUILTIN_DESCRIPTIONS["control"],
            ),
            RateChannel(
                name="vision",
                hz=self.vision_hz,
                priority=2,
                description=self._BUILTIN_DESCRIPTIONS["vision"],
            ),
            RateChannel(
                name="planning",
                hz=self.planning_hz,
                priority=1,
                description=self._BUILTIN_DESCRIPTIONS["planning"],
            ),
            RateChannel(
                name="logging",
                hz=self.logging_hz,
                priority=0,
                description=self._BUILTIN_DESCRIPTIONS["logging"],
            ),
        ]
        extra_channels = list(self.extras.values())
        return builtins + extra_channels

    def ratio(self, slow_channel: str, fast_channel: str) -> float:
        """Return ``slow_hz / fast_hz``.

        Use this to express how many ticks of *slow_channel* fit inside one
        tick of *fast_channel* — or equivalently the fractional rate of the
        slower channel relative to the faster one.

        Parameters
        ----------
        slow_channel:
            Name of the slower channel (numerator).
        fast_channel:
            Name of the faster channel (denominator).

        Raises
        ------
        KeyError:
            If either channel name is not found.
        ZeroDivisionError:
            If the fast channel has hz == 0 (cannot happen for validated
            channels, but guard is explicit).
        """
        slow_hz = self._lookup_hz(slow_channel)
        fast_hz = self._lookup_hz(fast_channel)
        return slow_hz / fast_hz

    def subdivisions_per_physics_tick(self, channel_name: str) -> float:
        """Return ``physics_hz / channel_hz``.

        Answers the question: "How many physics integration steps elapse
        between consecutive updates of *channel_name*?"

        A value > 1 means the channel is updated less frequently than the
        physics step (typical for vision or planning).
        A value < 1 would mean the channel is updated more often than
        physics (suspicious — also caught by ``validate()`` for control).

        Parameters
        ----------
        channel_name:
            Name of the target channel.

        Raises
        ------
        KeyError:
            If the channel name is not found.
        ZeroDivisionError:
            If channel_hz == 0.
        """
        channel_hz = self._lookup_hz(channel_name)
        return self.physics_hz / channel_hz

    def validate(self) -> List[str]:
        """Sanity-check the configuration.

        Returns a (possibly empty) list of human-readable warning strings.
        Currently checks:

        - ``control_hz > physics_hz``: the controller would be asked to run
          faster than the physics integrator can advance state — suspicious.
        - ``vision_hz > physics_hz``: more camera frames than physics ticks —
          frames would be duplicates.
        - ``planning_hz > physics_hz``: planner faster than physics — unusual.
        """
        warnings: List[str] = []
        if self.control_hz > self.physics_hz:
            warnings.append(
                f"control_hz ({self.control_hz} Hz) > physics_hz ({self.physics_hz} Hz): "
                "controller updates faster than physics integrator — state will repeat."
            )
        if self.vision_hz > self.physics_hz:
            warnings.append(
                f"vision_hz ({self.vision_hz} Hz) > physics_hz ({self.physics_hz} Hz): "
                "camera would capture duplicate frames."
            )
        if self.planning_hz > self.physics_hz:
            warnings.append(
                f"planning_hz ({self.planning_hz} Hz) > physics_hz ({self.physics_hz} Hz): "
                "planner running faster than physics — unusual."
            )
        return warnings

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lookup_hz(self, name: str) -> float:
        """Return the Hz value for a builtin or extra channel by name."""
        _builtin_map: Dict[str, float] = {
            "physics": self.physics_hz,
            "vision": self.vision_hz,
            "planning": self.planning_hz,
            "control": self.control_hz,
            "logging": self.logging_hz,
        }
        if name in _builtin_map:
            return _builtin_map[name]
        if name in self.extras:
            return self.extras[name].hz
        raise KeyError(
            f"Unknown rate channel '{name}'. "
            f"Known channels: {list(_builtin_map) + list(self.extras)}"
        )


# ---------------------------------------------------------------------------
# Module-level default
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = MultiRateConfig()
