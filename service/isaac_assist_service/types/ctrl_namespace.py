"""Phase 11c — Controller ctrl:* attribute namespace.

Unifies controller state across USD attr namespaces — cuRobo, builtin
pick-place, spline, constraint-pull, etc. — so probe instruments
(probe_ctrl_telemetry, Phase 8d baseline-comparator) can read every
controller's state identically.

Canonical attrs (every controller writes all of them):
  ctrl:adapter      — string controller-type token
  ctrl:phase        — string controller-specific phase token
  ctrl:tick         — int monotonic step counter
  ctrl:status       — "ok" | "stalled" | "fault"
  ctrl:last_error   — Optional[str], most recent error
  ctrl:profile      — Optional[str], reserved for Phase 56c scenario profile
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict


# Known controller adapter tokens. Extensible — list grows as new
# controllers land. Use the AdapterToken Literal in this module for
# the *required* set; future tokens land via either expanding the
# Literal here or using the looser ControllerAttrSet.adapter: str
# (the schema allows any string for forward-compat).
AdapterToken = Literal["curobo", "builtin_pp", "spline", "constraint_pull"]
StatusToken = Literal["ok", "stalled", "fault"]


class ControllerAttrSet(BaseModel):
    """Pydantic-validated canonical ctrl:* attribute set.

    Use `to_usd_attrs() -> dict[str, ...]` to materialise a dict
    suitable for passing into USD-attribute-write helpers; the
    helper inverts the names by prefixing each with `ctrl:`.
    """
    model_config = ConfigDict(frozen=True)

    adapter: str  # Loose — accepts unknown tokens for forward-compat;
                   # validation against AdapterToken happens via
                   # validate_strict() if the caller wants the tight check.
    phase: str
    tick: int = 0
    status: StatusToken = "ok"
    last_error: Optional[str] = None
    profile: Optional[str] = None

    def to_usd_attrs(self) -> dict[str, object]:
        """Return a {attr_name: value} dict ready for USD-attribute write.

        Attribute names use the unified `ctrl:*` prefix.
        """
        out: dict[str, object] = {
            "ctrl:adapter": self.adapter,
            "ctrl:phase": self.phase,
            "ctrl:tick": int(self.tick),
            "ctrl:status": str(self.status),
        }
        if self.last_error is not None:
            out["ctrl:last_error"] = self.last_error
        if self.profile is not None:
            out["ctrl:profile"] = self.profile
        return out

    @classmethod
    def from_usd_attrs(cls, attrs: dict[str, object]) -> "ControllerAttrSet":
        """Inverse of to_usd_attrs. Accepts either `ctrl:adapter` keys
        or unprefixed `adapter` keys (for callers that already stripped
        the namespace). Missing optional fields default to None / 'ok' / 0.
        """
        def _g(short: str):
            return attrs.get(f"ctrl:{short}", attrs.get(short))
        adapter = _g("adapter")
        if adapter is None:
            raise ValueError("ControllerAttrSet.from_usd_attrs: missing 'ctrl:adapter'")
        return cls(
            adapter=str(adapter),
            phase=str(_g("phase") or ""),
            tick=int(_g("tick") or 0),
            status=str(_g("status") or "ok"),  # type: ignore[arg-type]
            last_error=_g("last_error") if _g("last_error") is not None else None,
            profile=_g("profile") if _g("profile") is not None else None,
        )

    def validate_strict_adapter(self) -> None:
        """Raise ValueError if adapter is not one of the known AdapterToken values."""
        known = {"curobo", "builtin_pp", "spline", "constraint_pull"}
        if self.adapter not in known:
            raise ValueError(
                f"ControllerAttrSet.adapter={self.adapter!r} is not in the "
                f"known set {sorted(known)}. Loose strings allowed at the "
                f"type level but validate_strict_adapter() rejects unknowns."
            )
