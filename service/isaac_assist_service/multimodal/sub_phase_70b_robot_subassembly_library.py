"""Phase 70b — typed robot subassembly registry and compatibility lookup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

PHASE_ID = "70b"
PHASE_TITLE = "robot subassembly library"
PHASE_STATUS = "landed"

SubassemblyKind = Literal["arm", "gripper", "hand", "tool", "sensor", "mobile_base"]


@dataclass(frozen=True)
class RobotSubassembly:
    subassembly_id: str
    kind: SubassemblyKind
    asset_uri: str
    mount_interface: str
    mass_kg: float
    supported_runtimes: Tuple[str, ...] = ("5.1", "6.0")
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subassembly_id.strip():
            raise ValueError("subassembly_id is required")
        if not self.asset_uri.strip():
            raise ValueError("asset_uri is required")
        if self.mass_kg <= 0:
            raise ValueError("mass_kg must be positive")


class RobotSubassemblyLibrary:
    def __init__(self, entries: Iterable[RobotSubassembly] = ()) -> None:
        self._entries: Dict[str, RobotSubassembly] = {}
        for entry in entries:
            self.register(entry)

    def register(self, entry: RobotSubassembly, replace: bool = False) -> None:
        if entry.subassembly_id in self._entries and not replace:
            raise ValueError(f"duplicate subassembly_id: {entry.subassembly_id}")
        self._entries[entry.subassembly_id] = entry

    def get(self, subassembly_id: str) -> Optional[RobotSubassembly]:
        return self._entries.get(subassembly_id)

    def query(
        self,
        *,
        kind: Optional[SubassemblyKind] = None,
        mount_interface: Optional[str] = None,
        runtime: Optional[str] = None,
    ) -> List[RobotSubassembly]:
        return sorted(
            (
                entry for entry in self._entries.values()
                if (kind is None or entry.kind == kind)
                and (mount_interface is None or entry.mount_interface == mount_interface)
                and (runtime is None or runtime in entry.supported_runtimes)
            ),
            key=lambda entry: entry.subassembly_id,
        )

    def compatible(self, parent_id: str, child_id: str, runtime: str) -> bool:
        parent, child = self.get(parent_id), self.get(child_id)
        if parent is None or child is None:
            return False
        return (
            parent.mount_interface == child.mount_interface
            and runtime in parent.supported_runtimes
            and runtime in child.supported_runtimes
        )


def get_phase_metadata() -> Dict[str, Any]:
    return {"phase": PHASE_ID, "title": PHASE_TITLE, "status": PHASE_STATUS,
            "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 70b"}
