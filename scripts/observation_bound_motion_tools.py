"""Observation-bound, runtime-configurable execution tools.

Motion executors register their own model-facing tools and configuration
schemas at runtime.  A selected model or human may invoke one executor, hold,
or abort using exactly one fresh observation token.  This protocol does not
know about tasks, embodiments, joints, objects, phases, or controller types.

Actuator executors use the same fresh-token and fail-closed rules, but own a
runtime-defined command schema instead of a world-space target.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Mapping, Sequence


CONTROL_TOOL_NAMES = frozenset({"hold_motion", "abort_motion"})
ACTUATOR_CONTROL_TOOL_NAMES = frozenset(
    {"hold_actuation", "abort_actuation"}
)
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class MotionToolValidationError(ValueError):
    """Raised when a motion tool call is stale, malformed, or unsafe."""


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MotionToolValidationError(f"{path} must be a non-empty string")
    return value.strip()


def _finite_vector(value: Any, path: str) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MotionToolValidationError(f"{path} must be an XYZ array")
    if len(value) != 3:
        raise MotionToolValidationError(f"{path} must contain exactly three numbers")
    result: list[float] = []
    for index, component in enumerate(value):
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise MotionToolValidationError(f"{path}[{index}] must be a number")
        component = float(component)
        if not math.isfinite(component):
            raise MotionToolValidationError(f"{path}[{index}] must be finite")
        result.append(component)
    return result[0], result[1], result[2]


def _copy_json(value: Any, path: str = "value") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MotionToolValidationError(f"{path} must contain finite values")
        return value
    if isinstance(value, list):
        return [_copy_json(item, f"{path}[]") for item in value]
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise MotionToolValidationError(f"{path} keys must be strings")
            copied[key] = _copy_json(item, f"{path}.{key}")
        return copied
    raise MotionToolValidationError(f"{path} must be JSON-compatible")


def _validate_config_value(value: Any, schema: Mapping[str, Any], path: str) -> Any:
    """Validate the small JSON-Schema subset used by executor configurations."""
    expected = schema.get("type")
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{path} must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise MotionToolValidationError(f"{path} must be finite")
        if "minimum" in schema and value < float(schema["minimum"]):
            raise MotionToolValidationError(f"{path} is below its minimum")
        if "maximum" in schema and value > float(schema["maximum"]):
            raise MotionToolValidationError(f"{path} exceeds its maximum")
        return value
    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise MotionToolValidationError(f"{path} must be an integer")
        if "minimum" in schema and value < int(schema["minimum"]):
            raise MotionToolValidationError(f"{path} is below its minimum")
        if "maximum" in schema and value > int(schema["maximum"]):
            raise MotionToolValidationError(f"{path} exceeds its maximum")
        return value
    if expected == "boolean":
        if not isinstance(value, bool):
            raise MotionToolValidationError(f"{path} must be a boolean")
        return value
    if expected == "string":
        if not isinstance(value, str):
            raise MotionToolValidationError(f"{path} must be a string")
        if "enum" in schema and value not in schema["enum"]:
            raise MotionToolValidationError(f"{path} is not an allowed value")
        return value
    raise MotionToolValidationError(
        f"{path} uses unsupported executor-config schema type {expected!r}"
    )


@dataclass(frozen=True)
class MotionExecutorSpec:
    """One runtime-advertised executor and its model-configurable settings."""

    executor_id: str
    tool_name: str
    description: str
    configuration_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        _required_text(self.executor_id, "executor_id")
        if not isinstance(self.tool_name, str) or not _TOOL_NAME.fullmatch(
            self.tool_name
        ):
            raise MotionToolValidationError("tool_name has an invalid format")
        if self.tool_name in CONTROL_TOOL_NAMES:
            raise MotionToolValidationError("executor tool collides with a control tool")
        _required_text(self.description, "description")
        schema = self.configuration_schema
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise MotionToolValidationError(
                "configuration_schema must describe an object"
            )
        _copy_json(schema, "configuration_schema")

    def tool_schema(self, observation_id: str) -> dict[str, Any]:
        properties = _common_properties(observation_id)
        properties.update(
            {
                "translation_delta_m": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": (
                        "Optional XYZ correction of the current world-space target, "
                        "in meters in the frame supplied by the runtime."
                    ),
                },
                "executor_config": _copy_json(
                    self.configuration_schema, "configuration_schema"
                ),
            }
        )
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": ["observation_id", "confidence", "reason"],
                },
            },
        }

    def validate_configuration(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise MotionToolValidationError("executor_config must be an object")
        schema = self.configuration_schema
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        unknown = set(value) - set(properties)
        missing = required - set(value)
        if unknown and schema.get("additionalProperties", True) is False:
            raise MotionToolValidationError(
                f"executor_config contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"executor_config is missing fields: {sorted(missing)}"
            )
        validated = {
            key: _validate_config_value(item, properties[key], f"executor_config.{key}")
            if key in properties
            else _copy_json(item, f"executor_config.{key}")
            for key, item in value.items()
        }
        return validated


class MotionExecutorRegistry:
    """Runtime registry used to discover, advertise, and resolve executors."""

    def __init__(self) -> None:
        self._by_tool_name: dict[str, MotionExecutorSpec] = {}
        self._executor_ids: set[str] = set()

    def register(self, spec: MotionExecutorSpec) -> None:
        if not isinstance(spec, MotionExecutorSpec):
            raise MotionToolValidationError("executor registration requires a spec")
        if spec.tool_name in self._by_tool_name:
            raise MotionToolValidationError(
                f"executor tool {spec.tool_name!r} is already registered"
            )
        if spec.executor_id in self._executor_ids:
            raise MotionToolValidationError(
                f"executor id {spec.executor_id!r} is already registered"
            )
        self._by_tool_name[spec.tool_name] = spec
        self._executor_ids.add(spec.executor_id)

    def resolve(self, tool_name: str) -> MotionExecutorSpec | None:
        return self._by_tool_name.get(tool_name)

    def specs(self) -> tuple[MotionExecutorSpec, ...]:
        return tuple(self._by_tool_name[name] for name in sorted(self._by_tool_name))


def _common_properties(observation_id: str) -> dict[str, Any]:
    return {
        "observation_id": {
            "type": "string",
            "const": observation_id,
            "description": "The fresh observation token supplied for this decision.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "reason": {
            "type": "string",
            "minLength": 1,
            "description": "Concise reasoning grounded in the fresh observation.",
        },
    }


def motion_tool_schemas(
    observation_id: str, registry: MotionExecutorRegistry
) -> list[dict[str, Any]]:
    """Advertise currently registered executors plus hold/abort controls."""
    observation_id = _required_text(observation_id, "observation_id")
    if not isinstance(registry, MotionExecutorRegistry):
        raise MotionToolValidationError("registry must be a MotionExecutorRegistry")
    schemas = [spec.tool_schema(observation_id) for spec in registry.specs()]
    common = _common_properties(observation_id)
    for name, description in (
        (
            "hold_motion",
            "Hold position and require a new observation before further movement.",
        ),
        ("abort_motion", "Abort the current motion because it is unsafe."),
    ):
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": common,
                        "required": ["observation_id", "confidence", "reason"],
                    },
                },
            }
        )
    return schemas


def _tool_name_and_arguments(call: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(call, Mapping):
        raise MotionToolValidationError("tool call must be an object")
    function = call.get("function")
    if isinstance(function, Mapping):
        name = function.get("name")
        arguments = function.get("arguments", {})
    else:
        name = call.get("name")
        arguments = call.get("arguments", {})
    name = _required_text(name, "tool name")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as error:
            raise MotionToolValidationError("tool arguments must be valid JSON") from error
    if not isinstance(arguments, Mapping):
        raise MotionToolValidationError("tool arguments must be an object")
    return name, arguments


@dataclass(frozen=True)
class MotionToolOutcome:
    tool_name: str
    observation_id: str
    action: str
    confidence: float
    reason: str
    executor_id: str | None
    executor_config: Mapping[str, Any]
    target_before_m: tuple[float, float, float]
    target_after_m: tuple[float, float, float]
    requested_translation_delta_m: tuple[float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "observation_id": self.observation_id,
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "executor_id": self.executor_id,
            "executor_config": _copy_json(self.executor_config, "executor_config"),
            "target_before_m": list(self.target_before_m),
            "target_after_m": list(self.target_after_m),
            "requested_translation_delta_m": list(
                self.requested_translation_delta_m
            ),
        }


class ObservationBoundMotionGate:
    """Validate and consume one model-issued call for one fresh observation."""

    def __init__(
        self,
        *,
        observation_id: str,
        current_target_m: Sequence[float],
        maximum_correction_m: float,
        registry: MotionExecutorRegistry,
    ):
        self.observation_id = _required_text(observation_id, "observation_id")
        maximum_correction_m = float(maximum_correction_m)
        if not math.isfinite(maximum_correction_m) or maximum_correction_m <= 0.0:
            raise MotionToolValidationError("maximum_correction_m must be positive")
        if not isinstance(registry, MotionExecutorRegistry):
            raise MotionToolValidationError("registry must be a MotionExecutorRegistry")
        self.current_target_m = _finite_vector(current_target_m, "current_target_m")
        self.maximum_correction_m = maximum_correction_m
        self.registry = registry
        self._consumed = False

    def dispatch(self, call: Mapping[str, Any]) -> MotionToolOutcome:
        if self._consumed:
            raise MotionToolValidationError(
                "this observation has already authorized one tool call"
            )
        self._consumed = True
        name, arguments = _tool_name_and_arguments(call)
        spec = self.registry.resolve(name)
        if spec is None and name not in CONTROL_TOOL_NAMES:
            raise MotionToolValidationError(f"unregistered motion tool {name!r}")
        allowed = {"observation_id", "confidence", "reason"}
        if spec is not None:
            allowed.update({"translation_delta_m", "executor_config"})
        unknown = set(arguments) - allowed
        missing = {"observation_id", "confidence", "reason"} - set(arguments)
        if unknown:
            raise MotionToolValidationError(
                f"tool arguments contain unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"tool arguments are missing fields: {sorted(missing)}"
            )
        if arguments["observation_id"] != self.observation_id:
            raise MotionToolValidationError("motion tool call uses a stale observation")
        confidence = arguments["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        reason = _required_text(arguments["reason"], "reason")

        delta = (0.0, 0.0, 0.0)
        executor_id: str | None = None
        executor_config: Mapping[str, Any] = {}
        if spec is not None:
            action = "execute"
            executor_id = spec.executor_id
            executor_config = spec.validate_configuration(
                arguments.get("executor_config")
            )
            if "translation_delta_m" in arguments:
                delta = _finite_vector(
                    arguments["translation_delta_m"], "translation_delta_m"
                )
                magnitude = math.sqrt(
                    sum(component * component for component in delta)
                )
                if magnitude > self.maximum_correction_m + 1.0e-9:
                    raise MotionToolValidationError(
                        f"requested correction {magnitude:.4f} m exceeds the "
                        f"{self.maximum_correction_m:.4f} m safety limit"
                    )
        else:
            action = "hold" if name == "hold_motion" else "abort"
        target_after = tuple(
            before + change for before, change in zip(self.current_target_m, delta)
        )
        return MotionToolOutcome(
            tool_name=name,
            observation_id=self.observation_id,
            action=action,
            confidence=confidence,
            reason=" ".join(reason.split()),
            executor_id=executor_id,
            executor_config=executor_config,
            target_before_m=self.current_target_m,
            target_after_m=target_after,
            requested_translation_delta_m=delta,
        )


@dataclass(frozen=True)
class ActuatorExecutorSpec:
    """One runtime-advertised actuator and its configurable command surface."""

    executor_id: str
    tool_name: str
    description: str
    command_schema: Mapping[str, Any]
    configuration_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        _required_text(self.executor_id, "executor_id")
        if not isinstance(self.tool_name, str) or not _TOOL_NAME.fullmatch(
            self.tool_name
        ):
            raise MotionToolValidationError("tool_name has an invalid format")
        if self.tool_name in CONTROL_TOOL_NAMES | ACTUATOR_CONTROL_TOOL_NAMES:
            raise MotionToolValidationError("executor tool collides with a control tool")
        _required_text(self.description, "description")
        for path, schema in (
            ("command_schema", self.command_schema),
            ("configuration_schema", self.configuration_schema),
        ):
            if not isinstance(schema, Mapping) or schema.get("type") != "object":
                raise MotionToolValidationError(f"{path} must describe an object")
            _copy_json(schema, path)

    @staticmethod
    def _validate_object(
        value: Any,
        schema: Mapping[str, Any],
        path: str,
        *,
        optional: bool,
    ) -> dict[str, Any]:
        if value is None and optional:
            return {}
        if not isinstance(value, Mapping):
            raise MotionToolValidationError(f"{path} must be an object")
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        unknown = set(value) - set(properties)
        missing = required - set(value)
        if unknown and schema.get("additionalProperties", True) is False:
            raise MotionToolValidationError(
                f"{path} contains unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"{path} is missing fields: {sorted(missing)}"
            )
        return {
            key: _validate_config_value(item, properties[key], f"{path}.{key}")
            if key in properties
            else _copy_json(item, f"{path}.{key}")
            for key, item in value.items()
        }

    def validate_command(self, value: Any) -> dict[str, Any]:
        return self._validate_object(
            value, self.command_schema, "command", optional=False
        )

    def validate_configuration(self, value: Any) -> dict[str, Any]:
        return self._validate_object(
            value,
            self.configuration_schema,
            "executor_config",
            optional=True,
        )

    def tool_schema(self, observation_id: str) -> dict[str, Any]:
        properties = _common_properties(observation_id)
        properties.update(
            {
                "command": _copy_json(self.command_schema, "command_schema"),
                "executor_config": _copy_json(
                    self.configuration_schema, "configuration_schema"
                ),
            }
        )
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": [
                        "observation_id",
                        "confidence",
                        "reason",
                        "command",
                    ],
                },
            },
        }


class ActuatorExecutorRegistry:
    """Runtime discovery and resolution for actuator executors."""

    def __init__(self) -> None:
        self._by_tool_name: dict[str, ActuatorExecutorSpec] = {}
        self._executor_ids: set[str] = set()

    def register(self, spec: ActuatorExecutorSpec) -> None:
        if not isinstance(spec, ActuatorExecutorSpec):
            raise MotionToolValidationError("executor registration requires a spec")
        if spec.tool_name in self._by_tool_name:
            raise MotionToolValidationError(
                f"executor tool {spec.tool_name!r} is already registered"
            )
        if spec.executor_id in self._executor_ids:
            raise MotionToolValidationError(
                f"executor id {spec.executor_id!r} is already registered"
            )
        self._by_tool_name[spec.tool_name] = spec
        self._executor_ids.add(spec.executor_id)

    def resolve(self, tool_name: str) -> ActuatorExecutorSpec | None:
        return self._by_tool_name.get(tool_name)

    def specs(self) -> tuple[ActuatorExecutorSpec, ...]:
        return tuple(self._by_tool_name[name] for name in sorted(self._by_tool_name))


def actuator_tool_schemas(
    observation_id: str, registry: ActuatorExecutorRegistry
) -> list[dict[str, Any]]:
    """Advertise runtime actuator executors plus hold/abort controls."""
    observation_id = _required_text(observation_id, "observation_id")
    if not isinstance(registry, ActuatorExecutorRegistry):
        raise MotionToolValidationError("registry must be an ActuatorExecutorRegistry")
    schemas = [spec.tool_schema(observation_id) for spec in registry.specs()]
    common = _common_properties(observation_id)
    for name, description in (
        (
            "hold_actuation",
            "Maintain the current actuator command and require a new observation.",
        ),
        (
            "abort_actuation",
            "Abort the actuator transition because it is unsafe.",
        ),
    ):
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": common,
                        "required": ["observation_id", "confidence", "reason"],
                    },
                },
            }
        )
    return schemas


@dataclass(frozen=True)
class ActuatorToolOutcome:
    tool_name: str
    observation_id: str
    action: str
    confidence: float
    reason: str
    executor_id: str | None
    command: Mapping[str, Any]
    executor_config: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "observation_id": self.observation_id,
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "executor_id": self.executor_id,
            "command": _copy_json(self.command, "command"),
            "executor_config": _copy_json(
                self.executor_config, "executor_config"
            ),
        }


class ObservationBoundActuatorGate:
    """Validate and consume one actuator call for one fresh observation."""

    def __init__(
        self,
        *,
        observation_id: str,
        registry: ActuatorExecutorRegistry,
    ):
        self.observation_id = _required_text(observation_id, "observation_id")
        if not isinstance(registry, ActuatorExecutorRegistry):
            raise MotionToolValidationError(
                "registry must be an ActuatorExecutorRegistry"
            )
        self.registry = registry
        self._consumed = False

    def dispatch(self, call: Mapping[str, Any]) -> ActuatorToolOutcome:
        if self._consumed:
            raise MotionToolValidationError(
                "this observation has already authorized one tool call"
            )
        self._consumed = True
        name, arguments = _tool_name_and_arguments(call)
        spec = self.registry.resolve(name)
        if spec is None and name not in ACTUATOR_CONTROL_TOOL_NAMES:
            raise MotionToolValidationError(f"unregistered actuator tool {name!r}")
        allowed = {"observation_id", "confidence", "reason"}
        if spec is not None:
            allowed.update({"command", "executor_config"})
        unknown = set(arguments) - allowed
        missing = {"observation_id", "confidence", "reason"} - set(arguments)
        if spec is not None and "command" not in arguments:
            missing.add("command")
        if unknown:
            raise MotionToolValidationError(
                f"tool arguments contain unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"tool arguments are missing fields: {sorted(missing)}"
            )
        if arguments["observation_id"] != self.observation_id:
            raise MotionToolValidationError("actuator tool call uses a stale observation")
        confidence = arguments["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        reason = _required_text(arguments["reason"], "reason")
        executor_id: str | None = None
        command: Mapping[str, Any] = {}
        executor_config: Mapping[str, Any] = {}
        if spec is not None:
            action = "execute"
            executor_id = spec.executor_id
            command = spec.validate_command(arguments["command"])
            executor_config = spec.validate_configuration(
                arguments.get("executor_config")
            )
        else:
            action = "hold" if name == "hold_actuation" else "abort"
        return ActuatorToolOutcome(
            tool_name=name,
            observation_id=self.observation_id,
            action=action,
            confidence=confidence,
            reason=" ".join(reason.split()),
            executor_id=executor_id,
            command=command,
            executor_config=executor_config,
        )


def motion_report_yields_to_actuator(
    motion_report: Mapping[str, Any],
    *,
    actuator_transition_pending: bool,
) -> bool:
    """Return whether a model hold is a safe capability handoff, not failure."""
    if not actuator_transition_pending or not isinstance(motion_report, Mapping):
        return False
    recovery = motion_report.get("recovery_request")
    if not isinstance(recovery, Mapping):
        return False
    if recovery.get("reason") != "model_requested_hold":
        return False
    decision = recovery.get("coach_decision")
    if not isinstance(decision, Mapping):
        return False
    tool = decision.get("motion_tool")
    return isinstance(tool, Mapping) and tool.get("action") == "hold"
