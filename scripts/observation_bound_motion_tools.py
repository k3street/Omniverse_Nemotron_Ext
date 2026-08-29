"""Observation-bound, runtime-configurable execution tools.

Motion executors register their own model-facing tools and configuration
schemas at runtime.  A selected model or human may invoke one executor, hold,
or abort using exactly one fresh observation token.  This protocol does not
know about tasks, embodiments, joints, objects, phases, or controller types.

Actuator executors use the same fresh-token and fail-closed rules, but own a
runtime-defined command schema instead of a world-space target.

Operation schedulers choose among runtime-advertised next operations using the
same single-use observation token. Candidate descriptions are supplied by the
runtime, so the scheduling contract does not encode a task, phase sequence,
embodiment, or executor implementation.
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
SCHEDULER_CONTROL_TOOL_NAMES = frozenset(
    {"observe_again", "complete_task", "abort_task"}
)
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_OPERATION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


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


@dataclass(frozen=True)
class OperationCandidate:
    """One runtime-proposed next operation, independent of its implementation."""

    operation_id: str
    kind: str
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not _OPERATION_ID.fullmatch(
            self.operation_id
        ):
            raise MotionToolValidationError("operation_id has an invalid format")
        _required_text(self.kind, "kind")
        _required_text(self.description, "description")

    def to_dict(self) -> dict[str, str]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "description": self.description,
        }


def _normalize_operation_candidates(
    candidates: Sequence[OperationCandidate],
) -> tuple[OperationCandidate, ...]:
    normalized = tuple(candidates)
    if not normalized:
        raise MotionToolValidationError("at least one operation candidate is required")
    if any(not isinstance(item, OperationCandidate) for item in normalized):
        raise MotionToolValidationError(
            "candidates must contain OperationCandidate values"
        )
    operation_ids = [item.operation_id for item in normalized]
    if len(operation_ids) != len(set(operation_ids)):
        raise MotionToolValidationError("operation candidate ids must be unique")
    return normalized


def operation_scheduler_tool_schemas(
    observation_id: str,
    candidates: Sequence[OperationCandidate],
) -> list[dict[str, Any]]:
    """Advertise current runtime operations plus observe/complete/abort controls."""
    observation_id = _required_text(observation_id, "observation_id")
    normalized = _normalize_operation_candidates(candidates)
    common = _common_properties(observation_id)
    dispatch_properties = dict(common)
    dispatch_properties["operation_id"] = {
        "type": "string",
        "enum": [item.operation_id for item in normalized],
        "description": "One operation advertised for this fresh observation.",
    }
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "dispatch_operation",
                "description": (
                    "Dispatch exactly one of the runtime-advertised next operations."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": dispatch_properties,
                    "required": [
                        "observation_id",
                        "operation_id",
                        "confidence",
                        "reason",
                    ],
                },
            },
        }
    ]
    for name, description in (
        (
            "observe_again",
            "Preserve current commands and require a new observation before scheduling.",
        ),
        (
            "complete_task",
            "Declare that the human instruction is already physically complete.",
        ),
        ("abort_task", "Abort because no advertised operation is currently safe."),
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
class ScheduledOperationOutcome:
    tool_name: str
    observation_id: str
    action: str
    confidence: float
    reason: str
    operation_id: str | None
    operation_kind: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "observation_id": self.observation_id,
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind,
        }


class ObservationBoundOperationGate:
    """Consume one scheduling call for one fresh observation."""

    def __init__(
        self,
        *,
        observation_id: str,
        candidates: Sequence[OperationCandidate],
    ):
        self.observation_id = _required_text(observation_id, "observation_id")
        normalized = _normalize_operation_candidates(candidates)
        self._by_id = {item.operation_id: item for item in normalized}
        self._consumed = False

    def dispatch(self, call: Mapping[str, Any]) -> ScheduledOperationOutcome:
        if self._consumed:
            raise MotionToolValidationError(
                "this observation has already authorized one scheduler call"
            )
        self._consumed = True
        name, arguments = _tool_name_and_arguments(call)
        if name != "dispatch_operation" and name not in SCHEDULER_CONTROL_TOOL_NAMES:
            raise MotionToolValidationError(f"unregistered scheduler tool {name!r}")
        allowed = {"observation_id", "confidence", "reason"}
        if name == "dispatch_operation":
            allowed.add("operation_id")
        unknown = set(arguments) - allowed
        missing = {"observation_id", "confidence", "reason"} - set(arguments)
        if name == "dispatch_operation" and "operation_id" not in arguments:
            missing.add("operation_id")
        if unknown:
            raise MotionToolValidationError(
                f"tool arguments contain unknown fields: {sorted(unknown)}"
            )
        if missing:
            raise MotionToolValidationError(
                f"tool arguments are missing fields: {sorted(missing)}"
            )
        if arguments["observation_id"] != self.observation_id:
            raise MotionToolValidationError(
                "scheduler tool call uses a stale observation"
            )
        confidence = arguments["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise MotionToolValidationError("confidence must be a number in [0, 1]")
        reason = _required_text(arguments["reason"], "reason")
        candidate = None
        if name == "dispatch_operation":
            operation_id = _required_text(arguments["operation_id"], "operation_id")
            candidate = self._by_id.get(operation_id)
            if candidate is None:
                raise MotionToolValidationError(
                    f"operation {operation_id!r} was not advertised"
                )
            action = "dispatch"
        else:
            action = {
                "observe_again": "observe",
                "complete_task": "complete",
                "abort_task": "abort",
            }[name]
        return ScheduledOperationOutcome(
            tool_name=name,
            observation_id=self.observation_id,
            action=action,
            confidence=confidence,
            reason=" ".join(reason.split()),
            operation_id=None if candidate is None else candidate.operation_id,
            operation_kind=None if candidate is None else candidate.kind,
        )


def motion_report_yields_to_scheduler(motion_report: Mapping[str, Any]) -> bool:
    """Return whether an actual model hold should yield to fresh scheduling."""
    if not isinstance(motion_report, Mapping):
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


@dataclass(frozen=True)
class ActuatorFeedbackEventPolicy:
    """Configurable trigger for returning changed physical state to a governor."""

    minimum_position_change: float
    minimum_force_change_n: float

    def __post_init__(self) -> None:
        for name, value, allow_zero in (
            ("minimum_position_change", self.minimum_position_change, False),
            ("minimum_force_change_n", self.minimum_force_change_n, True),
        ):
            value = float(value)
            if not math.isfinite(value) or value < 0.0 or (
                not allow_zero and value == 0.0
            ):
                qualifier = "non-negative" if allow_zero else "positive"
                raise MotionToolValidationError(f"{name} must be finite and {qualifier}")


@dataclass(frozen=True)
class ActuatorFeedbackEvent:
    triggered: bool
    actuator_position_changed: bool
    tactile_changed: bool
    actuator_position_change: float
    tactile_force_change_n: float
    touch_changed: bool
    policy: ActuatorFeedbackEventPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "actuator_position_changed": self.actuator_position_changed,
            "tactile_changed": self.tactile_changed,
            "actuator_position_change": self.actuator_position_change,
            "tactile_force_change_n": self.tactile_force_change_n,
            "touch_changed": self.touch_changed,
            "policy": {
                "minimum_position_change": self.policy.minimum_position_change,
                "minimum_force_change_n": self.policy.minimum_force_change_n,
            },
        }


def assess_actuator_feedback_event(
    *,
    position_before: float,
    position_after: float,
    force_before_n: float,
    force_after_n: float,
    touch_before: bool,
    touch_after: bool,
    policy: ActuatorFeedbackEventPolicy,
) -> ActuatorFeedbackEvent:
    """Fuse actuator displacement and tactile change into one re-observe event."""
    if not isinstance(policy, ActuatorFeedbackEventPolicy):
        raise MotionToolValidationError(
            "policy must be an ActuatorFeedbackEventPolicy"
        )
    numeric: dict[str, float] = {}
    for name, value in (
        ("position_before", position_before),
        ("position_after", position_after),
        ("force_before_n", force_before_n),
        ("force_after_n", force_after_n),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{name} must be a finite number")
        value = float(value)
        if not math.isfinite(value):
            raise MotionToolValidationError(f"{name} must be a finite number")
        numeric[name] = value
    if not isinstance(touch_before, bool) or not isinstance(touch_after, bool):
        raise MotionToolValidationError("touch values must be boolean")
    position_change = abs(numeric["position_after"] - numeric["position_before"])
    force_change = abs(numeric["force_after_n"] - numeric["force_before_n"])
    touch_changed = touch_before != touch_after
    position_changed = position_change >= policy.minimum_position_change
    tactile_changed = touch_changed or force_change >= policy.minimum_force_change_n
    return ActuatorFeedbackEvent(
        triggered=position_changed and tactile_changed,
        actuator_position_changed=position_changed,
        tactile_changed=tactile_changed,
        actuator_position_change=position_change,
        tactile_force_change_n=force_change,
        touch_changed=touch_changed,
        policy=policy,
    )


@dataclass(frozen=True)
class MotionLeaseConditions:
    """Runtime-evaluable invariants attached to a longer-lived motion call."""

    require_contact: bool = False
    minimum_contact_force_n: float = 0.0
    maximum_tracked_pose_error_m: float | None = None
    minimum_observed_clearance_m: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.require_contact, bool):
            raise MotionToolValidationError("require_contact must be boolean")
        force = float(self.minimum_contact_force_n)
        if not math.isfinite(force) or force < 0.0:
            raise MotionToolValidationError(
                "minimum_contact_force_n must be finite and non-negative"
            )
        for name, value in (
            ("maximum_tracked_pose_error_m", self.maximum_tracked_pose_error_m),
            ("minimum_observed_clearance_m", self.minimum_observed_clearance_m),
        ):
            if value is None:
                continue
            value = float(value)
            if not math.isfinite(value) or value < 0.0:
                raise MotionToolValidationError(
                    f"{name} must be finite and non-negative when supplied"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_contact": self.require_contact,
            "minimum_contact_force_n": self.minimum_contact_force_n,
            "maximum_tracked_pose_error_m": self.maximum_tracked_pose_error_m,
            "minimum_observed_clearance_m": self.minimum_observed_clearance_m,
        }


@dataclass(frozen=True)
class MotionLeaseAssessment:
    valid: bool
    invalidation_reasons: tuple[str, ...]
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "invalidation_reasons": list(self.invalidation_reasons),
            "evidence": _copy_json(self.evidence, "evidence"),
        }


def assess_motion_lease(
    conditions: MotionLeaseConditions,
    *,
    contact_available: bool,
    touch: bool | None,
    contact_force_n: float | None,
    tracked_pose_error_m: float | None,
    observed_clearance_m: float | None,
) -> MotionLeaseAssessment:
    """Evaluate advertised lease conditions without task or executor knowledge."""
    if not isinstance(conditions, MotionLeaseConditions):
        raise MotionToolValidationError("conditions must be MotionLeaseConditions")
    if not isinstance(contact_available, bool):
        raise MotionToolValidationError("contact_available must be boolean")
    if touch is not None and not isinstance(touch, bool):
        raise MotionToolValidationError("touch must be boolean or null")

    numeric: dict[str, float | None] = {}
    for name, value in (
        ("contact_force_n", contact_force_n),
        ("tracked_pose_error_m", tracked_pose_error_m),
        ("observed_clearance_m", observed_clearance_m),
    ):
        if value is None:
            numeric[name] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MotionToolValidationError(f"{name} must be finite or null")
        value = float(value)
        if not math.isfinite(value):
            raise MotionToolValidationError(f"{name} must be finite or null")
        numeric[name] = value

    reasons: list[str] = []
    if conditions.require_contact:
        if not contact_available:
            reasons.append("contact_observation_unavailable")
        elif touch is not True:
            reasons.append("contact_lost")
        elif numeric["contact_force_n"] is None:
            reasons.append("contact_force_unavailable")
        elif numeric["contact_force_n"] < conditions.minimum_contact_force_n:
            reasons.append("contact_force_below_lease_minimum")
    if conditions.maximum_tracked_pose_error_m is not None:
        tracked_error = numeric["tracked_pose_error_m"]
        if tracked_error is None:
            reasons.append("tracked_pose_unavailable")
        elif tracked_error > conditions.maximum_tracked_pose_error_m:
            reasons.append("tracked_pose_error_exceeded")
    if conditions.minimum_observed_clearance_m is not None:
        clearance = numeric["observed_clearance_m"]
        if clearance is None:
            reasons.append("observed_clearance_unavailable")
        elif clearance < conditions.minimum_observed_clearance_m:
            reasons.append("observed_clearance_below_lease_minimum")
    evidence = {
        "contact_available": contact_available,
        "touch": touch,
        **numeric,
        "conditions": conditions.to_dict(),
    }
    return MotionLeaseAssessment(
        valid=not reasons,
        invalidation_reasons=tuple(reasons),
        evidence=evidence,
    )


def motion_lease_source_errors(
    conditions: MotionLeaseConditions,
    *,
    contact_available: bool,
    tracked_pose_available: bool,
    observed_clearance_available: bool,
) -> tuple[str, ...]:
    """Reject lease conditions whose required observation source is unavailable."""
    if not isinstance(conditions, MotionLeaseConditions):
        raise MotionToolValidationError("conditions must be MotionLeaseConditions")
    for name, value in (
        ("contact_available", contact_available),
        ("tracked_pose_available", tracked_pose_available),
        ("observed_clearance_available", observed_clearance_available),
    ):
        if not isinstance(value, bool):
            raise MotionToolValidationError(f"{name} must be boolean")
    errors: list[str] = []
    if conditions.require_contact and not contact_available:
        errors.append("contact source is unavailable")
    if (
        conditions.maximum_tracked_pose_error_m is not None
        and not tracked_pose_available
    ):
        errors.append("tracked-pose source is unavailable")
    if (
        conditions.minimum_observed_clearance_m is not None
        and not observed_clearance_available
    ):
        errors.append("observed-clearance source is unavailable")
    return tuple(errors)
