"""CRM-A3 — F/T sensor harmonization tests.

Verifies that the extended `add_force_torque_sensor` handler:
  1. Preserves backward-compat (no new kwargs → identical shape to pre-CRM-A3).
  2. Wires noise_std into the generated code when supplied.
  3. Wires publish_topic into the generated code when supplied.
  4. Schema has both new properties as optional (not in required list).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENSOR_PATH = "/World/Robot/FTSensor"
_PARENT_PATH = "/World/Robot/wrist_link"


def _run(coro):
    return asyncio.run(coro)


def _make_fake_exec_sync(output: str = ""):
    """Return an AsyncMock that simulates a successful exec_sync call."""
    async def _impl(code, timeout=10):
        return {"success": True, "output": output}
    return _impl


# ---------------------------------------------------------------------------
# 1. Backward-compat — existing callers see identical shape
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    """Existing calls (no noise_std, no publish_topic) must return an
    identical key-set to the pre-CRM-A3 handler."""

    @pytest.mark.asyncio
    async def test_return_shape_unchanged(self):
        """Handler must always include sensor_path, parent_path, threshold, raw."""
        from service.isaac_assist_service.chat.tools.handlers.sensors import (
            _handle_add_force_torque_sensor,
        )

        with patch(
            "service.isaac_assist_service.chat.tools.kit_tools.exec_sync",
            new=_make_fake_exec_sync(),
        ):
            result = await _handle_add_force_torque_sensor(
                {"sensor_path": _SENSOR_PATH, "parent_path": _PARENT_PATH}
            )

        assert result["sensor_path"] == _SENSOR_PATH
        assert result["parent_path"] == _PARENT_PATH
        assert result["threshold"] == 5.0  # default
        assert "raw" in result

    @pytest.mark.asyncio
    async def test_defaults_noise_zero_topic_none(self):
        """Without the new kwargs, noise_std=0.0 and publish_topic=None are
        returned so callers that inspect these fields won't KeyError."""
        from service.isaac_assist_service.chat.tools.handlers.sensors import (
            _handle_add_force_torque_sensor,
        )

        with patch(
            "service.isaac_assist_service.chat.tools.kit_tools.exec_sync",
            new=_make_fake_exec_sync(),
        ):
            result = await _handle_add_force_torque_sensor(
                {"sensor_path": _SENSOR_PATH, "parent_path": _PARENT_PATH}
            )

        assert result["noise_std"] == 0.0
        assert result["publish_topic"] is None


# ---------------------------------------------------------------------------
# 2. noise_std wiring
# ---------------------------------------------------------------------------

class TestNoiseStd:
    """When noise_std=0.05 is supplied the value must appear in the generated code."""

    @pytest.mark.asyncio
    async def test_noise_std_appears_in_code(self):
        """The generated code must contain the literal noise_std value."""
        from service.isaac_assist_service.chat.tools.handlers.sensors import (
            _handle_add_force_torque_sensor,
        )

        captured_code: list[str] = []

        async def _spy(code, timeout=10):
            captured_code.append(code)
            return {"success": True, "output": ""}

        with patch(
            "service.isaac_assist_service.chat.tools.kit_tools.exec_sync",
            new=_spy,
        ):
            result = await _handle_add_force_torque_sensor(
                {
                    "sensor_path": _SENSOR_PATH,
                    "parent_path": _PARENT_PATH,
                    "noise_std": 0.05,
                }
            )

        assert len(captured_code) == 1, "exec_sync must be called exactly once"
        assert "0.05" in captured_code[0], (
            f"noise_std value 0.05 must appear in generated code; got:\n{captured_code[0]}"
        )
        assert result["noise_std"] == 0.05

    @pytest.mark.asyncio
    async def test_noise_zero_skips_noise_block(self):
        """When noise_std=0.0 (default) no noise block should be injected."""
        from service.isaac_assist_service.chat.tools.handlers.sensors import (
            _handle_add_force_torque_sensor,
        )

        captured_code: list[str] = []

        async def _spy(code, timeout=10):
            captured_code.append(code)
            return {"success": True, "output": ""}

        with patch(
            "service.isaac_assist_service.chat.tools.kit_tools.exec_sync",
            new=_spy,
        ):
            await _handle_add_force_torque_sensor(
                {"sensor_path": _SENSOR_PATH, "parent_path": _PARENT_PATH, "noise_std": 0.0}
            )

        assert "_add_noise" not in captured_code[0], (
            "No noise block should be injected when noise_std=0.0"
        )


# ---------------------------------------------------------------------------
# 3. publish_topic wiring
# ---------------------------------------------------------------------------

class TestPublishTopic:
    """When publish_topic is set the topic string must appear in the generated code."""

    @pytest.mark.asyncio
    async def test_publish_topic_appears_in_code(self):
        """The generated code must contain the publish_topic string."""
        from service.isaac_assist_service.chat.tools.handlers.sensors import (
            _handle_add_force_torque_sensor,
        )

        captured_code: list[str] = []

        async def _spy(code, timeout=10):
            captured_code.append(code)
            return {"success": True, "output": ""}

        topic = "/sensor/ft"
        with patch(
            "service.isaac_assist_service.chat.tools.kit_tools.exec_sync",
            new=_spy,
        ):
            result = await _handle_add_force_torque_sensor(
                {
                    "sensor_path": _SENSOR_PATH,
                    "parent_path": _PARENT_PATH,
                    "publish_topic": topic,
                }
            )

        assert topic in captured_code[0], (
            f"publish_topic '{topic}' must appear in generated code; got:\n{captured_code[0]}"
        )
        assert result["publish_topic"] == topic

    @pytest.mark.asyncio
    async def test_no_publish_block_when_topic_none(self):
        """When publish_topic is None no publish stub should be injected."""
        from service.isaac_assist_service.chat.tools.handlers.sensors import (
            _handle_add_force_torque_sensor,
        )

        captured_code: list[str] = []

        async def _spy(code, timeout=10):
            captured_code.append(code)
            return {"success": True, "output": ""}

        with patch(
            "service.isaac_assist_service.chat.tools.kit_tools.exec_sync",
            new=_spy,
        ):
            await _handle_add_force_torque_sensor(
                {"sensor_path": _SENSOR_PATH, "parent_path": _PARENT_PATH}
            )

        assert "publish_topic" not in captured_code[0] or "ftsensor:publish_topic" not in captured_code[0], (
            "No publish stub should be injected when publish_topic is None"
        )


# ---------------------------------------------------------------------------
# 4. Schema validation — both properties optional, not required
# ---------------------------------------------------------------------------

class TestSchema:
    """Schema for add_force_torque_sensor must contain both new properties
    as optional fields (not listed in required)."""

    def _get_schema(self):
        from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS as TOOL_SCHEMAS
        matches = [
            s["function"]
            for s in TOOL_SCHEMAS
            if s.get("function", {}).get("name") == "add_force_torque_sensor"
        ]
        assert matches, "add_force_torque_sensor must be present in TOOL_SCHEMAS"
        return matches[0]

    def test_noise_std_in_schema_properties(self):
        """noise_std must be present in schema properties."""
        fn = self._get_schema()
        props = fn["parameters"]["properties"]
        assert "noise_std" in props, f"noise_std missing from properties; got: {list(props)}"

    def test_publish_topic_in_schema_properties(self):
        """publish_topic must be present in schema properties."""
        fn = self._get_schema()
        props = fn["parameters"]["properties"]
        assert "publish_topic" in props, f"publish_topic missing from properties; got: {list(props)}"

    def test_new_props_not_in_required(self):
        """noise_std and publish_topic must NOT be in the required list."""
        fn = self._get_schema()
        required = fn["parameters"].get("required", [])
        assert "noise_std" not in required, "noise_std must be optional (not in required)"
        assert "publish_topic" not in required, "publish_topic must be optional (not in required)"

    def test_existing_required_fields_unchanged(self):
        """sensor_path and parent_path must still be required."""
        fn = self._get_schema()
        required = fn["parameters"].get("required", [])
        assert "sensor_path" in required, "sensor_path must remain required"
        assert "parent_path" in required, "parent_path must remain required"
