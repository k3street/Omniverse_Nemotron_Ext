"""Semantic scene-role bindings for model-governed manipulation runs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


_TRAILING_INSTANCE = re.compile(r"_\d+$")


def humanize_asset_name(asset_name: str) -> str:
    """Turn an instance key such as ``bagel_06`` into a model-facing label."""
    normalized = _TRAILING_INSTANCE.sub("", asset_name.strip())
    return normalized.replace("_", " ").strip()


@dataclass(frozen=True)
class ManipulationSceneRoles:
    """Bind semantic task roles to assets without leaking them into tool schemas."""

    movable_object_asset: str
    movable_object_label: str
    target_receptacle_asset: str
    target_receptacle_label: str

    @classmethod
    def create(
        cls,
        *,
        movable_object_asset: str,
        movable_object_label: str | None = None,
        target_receptacle_asset: str,
        target_receptacle_label: str | None = None,
    ) -> "ManipulationSceneRoles":
        object_asset = movable_object_asset.strip()
        target_asset = target_receptacle_asset.strip()
        if not object_asset or not target_asset:
            raise ValueError("scene-role asset names must be non-empty")
        if object_asset == target_asset:
            raise ValueError("movable object and target receptacle must differ")
        object_label = (movable_object_label or humanize_asset_name(object_asset)).strip()
        target_label = (
            target_receptacle_label or humanize_asset_name(target_asset)
        ).strip()
        if not object_label or not target_label:
            raise ValueError("scene-role labels must be non-empty")
        return cls(
            movable_object_asset=object_asset,
            movable_object_label=object_label,
            target_receptacle_asset=target_asset,
            target_receptacle_label=target_label,
        )

    def validate_scene(self, scene: Mapping[str, Any]) -> None:
        missing = []
        for asset in (self.movable_object_asset, self.target_receptacle_asset):
            try:
                scene[asset]
            except (KeyError, TypeError, IndexError):
                missing.append(asset)
        if missing:
            raise KeyError(f"scene-role assets are unavailable: {missing}")

    def default_instruction(self) -> str:
        return (
            f"Pick up the {self.movable_object_label} and put it on the "
            f"{self.target_receptacle_label}"
        )

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {
            "movable_object": {
                "asset": self.movable_object_asset,
                "label": self.movable_object_label,
            },
            "target_receptacle": {
                "asset": self.target_receptacle_asset,
                "label": self.target_receptacle_label,
            },
        }
