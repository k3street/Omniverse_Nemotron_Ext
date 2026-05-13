"""Phase 70d — bin interior metadata loader.

Loads industrial bin SKU registry from YAML and provides
query methods for drop-target computation (interior dimensions,
payload capacity, orientation).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 70d.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


PHASE_ID = "70d"
PHASE_TITLE = "bin interior metadata"
PHASE_STATUS = "landed"

# Default path relative to the project root (service/ is one level below root)
_DEFAULT_YAML = Path(__file__).parent.parent.parent.parent / "data" / "bin_interior_metadata.yaml"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 70d",
    }


@dataclass
class BinSpec:
    """Specification for a single industrial bin SKU."""

    sku: str
    name: str
    manufacturer: str
    exterior_mm: List[float]        # [width, depth, height]
    interior_mm: List[float]        # [width, depth, height]
    wall_thickness_mm: float
    max_payload_kg: float
    stackable: bool
    opening_orientation: str        # top | side | tilted

    # Convenience accessors ──────────────────────────────────────────────────
    @property
    def interior_w(self) -> float:
        return float(self.interior_mm[0])

    @property
    def interior_d(self) -> float:
        return float(self.interior_mm[1])

    @property
    def interior_h(self) -> float:
        return float(self.interior_mm[2])


class BinMetadataLoader:
    """Load and query industrial bin interior metadata from a YAML registry.

    Parameters
    ----------
    yaml_path:
        Path to the YAML registry file. Defaults to the project-level
        ``data/bin_interior_metadata.yaml``.
    """

    def __init__(self, yaml_path: Optional[Path] = None) -> None:
        self._yaml_path: Path = yaml_path if yaml_path is not None else _DEFAULT_YAML
        self._registry: Optional[Dict[str, BinSpec]] = None

    # ── public API ──────────────────────────────────────────────────────────

    def load(self) -> Dict[str, BinSpec]:
        """Parse the YAML registry and return a mapping of ``sku → BinSpec``.

        The result is cached; subsequent calls return the same dict without
        re-reading the file.
        """
        if self._registry is not None:
            return self._registry

        raw_path = self._yaml_path
        if not raw_path.exists():
            raise FileNotFoundError(
                f"Bin metadata YAML not found at {raw_path}. "
                "Create data/bin_interior_metadata.yaml or supply an explicit path."
            )

        with open(raw_path, "r", encoding="utf-8") as fh:
            raw: List[Dict[str, Any]] = yaml.safe_load(fh)

        registry: Dict[str, BinSpec] = {}
        for entry in raw:
            spec = BinSpec(
                sku=str(entry["sku"]),
                name=str(entry["name"]),
                manufacturer=str(entry["manufacturer"]),
                exterior_mm=[float(v) for v in entry["exterior_mm"]],
                interior_mm=[float(v) for v in entry["interior_mm"]],
                wall_thickness_mm=float(entry["wall_thickness_mm"]),
                max_payload_kg=float(entry["max_payload_kg"]),
                stackable=bool(entry["stackable"]),
                opening_orientation=str(entry["opening_orientation"]),
            )
            registry[spec.sku] = spec

        self._registry = registry
        return registry

    def get(self, sku: str) -> Optional[BinSpec]:
        """Return the ``BinSpec`` for *sku*, or ``None`` if not found."""
        return self.load().get(sku)

    def list_skus(self) -> List[str]:
        """Return a sorted list of all registered SKU strings."""
        return sorted(self.load().keys())

    def find_by_payload(self, min_kg: float) -> List[BinSpec]:
        """Return all bins whose ``max_payload_kg`` is >= *min_kg*.

        Results are sorted by ``max_payload_kg`` ascending.
        """
        results = [
            spec for spec in self.load().values()
            if spec.max_payload_kg >= min_kg
        ]
        return sorted(results, key=lambda s: s.max_payload_kg)

    def find_by_interior_min(
        self, w_mm: float, d_mm: float, h_mm: float
    ) -> List[BinSpec]:
        """Return all bins whose interior dimensions all satisfy the request.

        Each of ``interior_mm[0] >= w_mm``, ``interior_mm[1] >= d_mm``,
        ``interior_mm[2] >= h_mm`` must hold.

        Results are sorted by interior volume (w*d*h) ascending.
        """
        results = [
            spec for spec in self.load().values()
            if (
                spec.interior_w >= w_mm
                and spec.interior_d >= d_mm
                and spec.interior_h >= h_mm
            )
        ]
        return sorted(
            results,
            key=lambda s: s.interior_w * s.interior_d * s.interior_h,
        )
