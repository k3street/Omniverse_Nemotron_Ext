"""Phase 31b — Industrial bridges full surface: Modbus, MQTT-Sparkplug, OPC-UA.

Implements the SPEC/STATE-MACHINE layer for industrial PLC connectivity:

* ``BridgeProtocol`` — Literal type for supported protocol identifiers.
* ``BridgeState`` — Literal type for the full bridge lifecycle states.
* ``RegisterMapEntry`` — dataclass describing one named register in the device map.
* ``BridgeConfig`` — dataclass holding all configuration needed to open a bridge.
* ``IndustrialBridge`` — state-machine class with dry-run connect/disconnect,
  typed register read/write, batch operations, and health_check.
* ``BRIDGE_PRESETS`` — four factory-floor preset configurations.
* ``expected_state_transitions`` — happy-path state list per protocol.

Live PLC connections are intentionally left as ``NotImplementedError`` scaffolds;
all real logic operates in dry_run mode so the full surface is testable without
hardware.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 31b.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------
PHASE_ID = "31b"
PHASE_TITLE = "Industrial bridges full surface: Modbus, MQTT-Sparkplug, OPC-UA"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 31b",
        "protocols": ["modbus_tcp", "modbus_rtu", "opc_ua", "mqtt_sparkplug"],
    }


# ---------------------------------------------------------------------------
# Core Literal types
# ---------------------------------------------------------------------------
BridgeProtocol = Literal["modbus_tcp", "modbus_rtu", "opc_ua", "mqtt_sparkplug"]

BridgeState = Literal[
    "disconnected",
    "connecting",
    "handshaking",
    "ready",
    "polling",
    "error",
    "closed",
]


# ---------------------------------------------------------------------------
# RegisterMapEntry
# ---------------------------------------------------------------------------
@dataclass
class RegisterMapEntry:
    """One named register in the device register map.

    Attributes
    ----------
    name:
        Unique human-readable identifier (e.g. ``"conveyor_speed"``).
    address:
        Device register address (≥ 0).
    data_type:
        One of ``"bool"``, ``"int16"``, ``"int32"``, ``"float32"``.
    read_only:
        If *True* write_register raises ``ValueError``.
    scale:
        Multiplier applied when converting raw device units to SI/engineering
        units.  Default ``1.0`` (no scaling).
    description:
        Optional free-text description of the register.
    """

    name: str
    address: int
    data_type: Literal["bool", "int16", "int32", "float32"]
    read_only: bool = False
    scale: float = 1.0
    description: str = ""

    def __post_init__(self) -> None:
        if self.address < 0:
            raise ValueError(f"Register address must be >= 0, got {self.address!r}")


# ---------------------------------------------------------------------------
# BridgeConfig
# ---------------------------------------------------------------------------
@dataclass
class BridgeConfig:
    """Configuration for one industrial bridge connection.

    Attributes
    ----------
    protocol:
        Transport protocol identifier.
    host:
        IP address or hostname of the remote device / broker.
    port:
        TCP/UDP port number.
    unit_id:
        Modbus unit (slave) identifier; also used as OPC-UA namespace index.
    poll_interval_ms:
        How often to refresh register values (milliseconds).
    register_map:
        Ordered list of registers exposed by this bridge.
    """

    protocol: BridgeProtocol
    host: str
    port: int
    unit_id: int = 1
    poll_interval_ms: int = 100
    register_map: List[RegisterMapEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# IndustrialBridge
# ---------------------------------------------------------------------------
class IndustrialBridge:
    """Lifecycle state machine for an industrial connectivity bridge.

    The bridge begins in ``"disconnected"`` state.  Calling :meth:`connect`
    drives it through ``connecting`` → ``handshaking`` → ``ready``.
    :meth:`disconnect` moves it to ``"closed"`` from any active state.

    All operations on register values use an internal ``_dry_run_state`` dict
    so that the full API surface is exercisable without PLC hardware.

    Parameters
    ----------
    config:
        Bridge configuration.
    dry_run:
        When *True* (default) the state machine performs simulated transitions
        without opening a real network connection.  When *False* :meth:`connect`
        raises ``NotImplementedError`` as a scaffold placeholder.
    """

    # ------------------------------------------------------------------ #
    # Legal state-machine transitions                                      #
    # ------------------------------------------------------------------ #
    LEGAL_TRANSITIONS: Dict[BridgeState, set] = {
        "disconnected": {"connecting"},
        "connecting": {"handshaking", "error"},
        "handshaking": {"ready", "error"},
        "ready": {"polling", "error", "closed"},
        "polling": {"ready", "error", "closed"},
        "error": {"connecting", "closed"},
        "closed": set(),
    }

    def __init__(self, config: BridgeConfig, dry_run: bool = True) -> None:
        self._config = config
        self._dry_run = dry_run
        self._state: BridgeState = "disconnected"
        self._dry_run_state: Dict[str, Any] = {}
        self._last_poll_ts: Optional[float] = None
        self._error_count: int = 0

    # ------------------------------------------------------------------ #
    # State property                                                       #
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> BridgeState:
        """Current lifecycle state of this bridge."""
        return self._state

    # ------------------------------------------------------------------ #
    # Transition guard                                                     #
    # ------------------------------------------------------------------ #
    def _transition(self, target: BridgeState) -> None:
        """Move to *target* state or raise ``ValueError`` if illegal."""
        allowed = self.LEGAL_TRANSITIONS.get(self._state, set())
        if target not in allowed:
            raise ValueError(
                f"Illegal transition: {self._state!r} → {target!r}. "
                f"Legal targets from {self._state!r}: {sorted(allowed)}"
            )
        self._state = target

    # ------------------------------------------------------------------ #
    # Connect / disconnect                                                 #
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        """Open the bridge connection.

        In dry-run mode the state machine traverses the full happy path:
        ``disconnected → connecting → handshaking → ready``.

        Raises
        ------
        NotImplementedError
            When ``dry_run=False`` — live PLC connection is not yet
            implemented; this is the scaffold placeholder.
        ValueError
            When the current state is not ``"disconnected"`` (illegal
            transition).
        """
        if not self._dry_run:
            raise NotImplementedError(
                "Live PLC connection is not implemented. "
                "Use dry_run=True for simulation."
            )
        # Walk through the full handshake sequence.
        self._transition("connecting")
        self._transition("handshaking")
        self._transition("ready")
        self._last_poll_ts = time.monotonic()

    def disconnect(self) -> None:
        """Close the bridge and move to ``"closed"`` state.

        Safe to call from ``ready``, ``polling``, or ``error``.

        Raises
        ------
        ValueError
            If the bridge is already ``"closed"`` or in a state that
            does not allow a direct jump to ``"closed"``.
        """
        self._transition("closed")

    # ------------------------------------------------------------------ #
    # Register I/O                                                         #
    # ------------------------------------------------------------------ #
    def _lookup(self, name: str) -> RegisterMapEntry:
        for entry in self._config.register_map:
            if entry.name == name:
                return entry
        raise KeyError(f"Register {name!r} not found in register map.")

    def write_register(self, name: str, value: Any) -> None:
        """Write *value* to the named register.

        Parameters
        ----------
        name:
            Register name as declared in :attr:`BridgeConfig.register_map`.
        value:
            Numeric or boolean value to write.

        Raises
        ------
        KeyError
            Register name not found in the map.
        ValueError
            Register is declared ``read_only=True``.
        """
        entry = self._lookup(name)
        if entry.read_only:
            raise ValueError(
                f"Register {name!r} is read-only (address={entry.address})."
            )
        self._dry_run_state[name] = value

    def read_register(self, name: str) -> Any:
        """Read the current value of the named register.

        In dry-run mode returns the value set by the most recent
        :meth:`write_register` call, or raises ``KeyError`` if no
        value has been written yet.

        Parameters
        ----------
        name:
            Register name as declared in :attr:`BridgeConfig.register_map`.

        Raises
        ------
        KeyError
            Register name not found in the map or has never been written.
        """
        # Validate name exists in map (raises KeyError if not).
        self._lookup(name)
        if name not in self._dry_run_state:
            raise KeyError(
                f"Register {name!r} has no stored value. "
                "Write a value first or read from live PLC."
            )
        return self._dry_run_state[name]

    def batch_write(self, values: Dict[str, Any]) -> None:
        """Write multiple registers atomically (dry-run: sequentially).

        Parameters
        ----------
        values:
            Mapping of ``{register_name: value}``.  All names must exist in the
            register map and none may be read-only; the entire batch is
            validated before any write is committed.

        Raises
        ------
        KeyError
            One or more register names not found.
        ValueError
            One or more registers are read-only.
        """
        # Validate all entries before writing any.
        for name, _val in values.items():
            entry = self._lookup(name)
            if entry.read_only:
                raise ValueError(
                    f"Register {name!r} is read-only; batch_write rejected."
                )
        # Commit.
        for name, val in values.items():
            self._dry_run_state[name] = val

    def batch_read(self, names: List[str]) -> Dict[str, Any]:
        """Read multiple registers and return them as a dict.

        Parameters
        ----------
        names:
            List of register names to read.

        Returns
        -------
        dict
            ``{name: value}`` for each requested register.

        Raises
        ------
        KeyError
            Any name not found in the map or not yet written.
        """
        return {name: self.read_register(name) for name in names}

    # ------------------------------------------------------------------ #
    # Health check                                                         #
    # ------------------------------------------------------------------ #
    def health_check(self) -> Dict[str, Any]:
        """Return a snapshot of bridge health metrics.

        Returns
        -------
        dict with keys:
            ``state``, ``protocol``, ``register_count``,
            ``last_poll_ts``, ``error_count``
        """
        return {
            "state": self._state,
            "protocol": self._config.protocol,
            "register_count": len(self._config.register_map),
            "last_poll_ts": self._last_poll_ts,
            "error_count": self._error_count,
        }


# ---------------------------------------------------------------------------
# Factory-floor presets
# ---------------------------------------------------------------------------
BRIDGE_PRESETS: Dict[str, BridgeConfig] = {
    "modbus_factory_floor": BridgeConfig(
        protocol="modbus_tcp",
        host="192.168.1.10",
        port=502,
        unit_id=1,
        poll_interval_ms=100,
        register_map=[
            RegisterMapEntry(
                name="conveyor_speed",
                address=0,
                data_type="float32",
                description="Conveyor belt speed in m/s",
            ),
            RegisterMapEntry(
                name="emergency_stop",
                address=1,
                data_type="bool",
                description="E-stop relay output",
            ),
            RegisterMapEntry(
                name="part_count",
                address=2,
                data_type="int32",
                read_only=True,
                description="Accumulated part counter (read-only sensor)",
            ),
        ],
    ),
    "mqtt_sparkplug_default": BridgeConfig(
        protocol="mqtt_sparkplug",
        host="broker.factory.local",
        port=1883,
        unit_id=0,
        poll_interval_ms=200,
        register_map=[
            RegisterMapEntry(
                name="robot_state",
                address=10,
                data_type="int16",
                description="Robot operational state (0=idle, 1=running, 2=fault)",
            ),
            RegisterMapEntry(
                name="gripper_force",
                address=11,
                data_type="float32",
                description="Gripper force feedback in Newtons",
            ),
            RegisterMapEntry(
                name="cycle_complete",
                address=12,
                data_type="bool",
                description="Pulse high for 100 ms on cycle completion",
            ),
        ],
    ),
    "opc_ua_machine_a": BridgeConfig(
        protocol="opc_ua",
        host="10.0.0.50",
        port=4840,
        unit_id=2,
        poll_interval_ms=50,
        register_map=[
            RegisterMapEntry(
                name="spindle_rpm",
                address=100,
                data_type="float32",
                description="CNC spindle speed in RPM",
            ),
            RegisterMapEntry(
                name="axis_x_pos",
                address=101,
                data_type="float32",
                description="X-axis position in mm",
            ),
            RegisterMapEntry(
                name="axis_y_pos",
                address=102,
                data_type="float32",
                description="Y-axis position in mm",
            ),
            RegisterMapEntry(
                name="tool_change_request",
                address=103,
                data_type="bool",
                description="PLC requests automatic tool change",
            ),
        ],
    ),
    "modbus_rtu_legacy": BridgeConfig(
        protocol="modbus_rtu",
        host="/dev/ttyUSB0",
        port=0,  # RTU: baud rate encoded separately; port=0 is a placeholder
        unit_id=5,
        poll_interval_ms=500,
        register_map=[
            RegisterMapEntry(
                name="pump_flow_rate",
                address=200,
                data_type="int16",
                scale=0.1,
                description="Pump flow rate in 0.1 L/min increments",
            ),
            RegisterMapEntry(
                name="tank_level",
                address=201,
                data_type="int16",
                read_only=True,
                scale=0.01,
                description="Tank fill level 0.0–1.0 (read-only ultrasonic sensor)",
            ),
        ],
    ),
}


# ---------------------------------------------------------------------------
# expected_state_transitions
# ---------------------------------------------------------------------------
def expected_state_transitions(protocol: BridgeProtocol) -> List[BridgeState]:
    """Return the ordered list of states for a successful connect + disconnect.

    The happy path is identical for all four protocols at this abstraction
    level; protocol-specific divergences (e.g. MQTT CONNECT/CONNACK vs
    Modbus TCP SYN/ACK) are not visible in the state-machine layer.

    Returns
    -------
    list[BridgeState]
        At least 4 states: ``disconnected``, ``connecting``,
        ``handshaking``, ``ready``.
    """
    base: List[BridgeState] = [
        "disconnected",
        "connecting",
        "handshaking",
        "ready",
    ]
    # For polling-capable protocols we include the polling state.
    if protocol in ("modbus_tcp", "modbus_rtu", "opc_ua"):
        base.append("polling")
    base.append("closed")
    return base
