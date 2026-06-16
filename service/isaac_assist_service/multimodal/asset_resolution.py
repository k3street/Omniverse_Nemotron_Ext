"""Deterministic asset resolution for reviewed LayoutSpec objects.

The floor-plan UI lets users correct ``object_class`` before build.  This
module turns that reviewed class into the USD reference the instantiator should
materialise, while preserving explicit per-object overrides when present.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, List, Optional

from .object_palette import get_class

_ASSET_ROOTS_ENV = "ISAAC_ASSIST_ASSET_ROOTS"
_LEGACY_ASSET_ROOT_ENV = "ASSETS_ROOT_PATH"
_DEFAULT_ASSET_ROOT = Path("/home/kimate/Desktop/assets")

# Base used to turn palette-relative "Isaac/..." references into a
# Kit-resolvable URL. This must be a full Isaac *assets root* (equivalent to
# omni.isaac.core's get_assets_root_path()), i.e. ending in ".../Assets/Isaac/<ver>",
# NOT a bare Nucleus server root. The default targets the production S3 bucket;
# override with ISAAC_ASSIST_ASSET_BASE for a local Nucleus mount.
_ASSET_BASE_ENV = "ISAAC_ASSIST_ASSET_BASE"
_DEFAULT_ISAAC_ASSET_BASE = (
    "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1"
)

_LOCAL_ASSET_OVERRIDES = {
    "franka_panda": [
        "Lightwheel_OpenSource/Locomotion/Grass/E/InteractiveAsset/omron_franka.usd",
    ],
    "bowl": [
        "SimReady_Furniture_Misc_01_NVD/Assets/simready_content/common_assets/props/serving_bowl/serving_bowl.usd",
        "Residential_NVD/Assets/ArchVis/Residential/Kitchen/Kitchenware/Serving/serving_bowl.usd",
        "Residential_NVD/Assets/ArchVis/Residential/Kitchen/Kitchenware/StorageAndOrganization/fruit_bowl.usd",
        "Residential_NVD/Assets/ArchVis/Residential/Decor/Kitchenware/Brass_Bowl_Small.usd",
    ],
    "plate": [
        "SimReady_Furniture_Misc_01_NVD/Assets/simready_content/common_assets/props/plate_small/plate_small.usd",
        "Residential_NVD/Assets/ArchVis/Residential/Kitchen/Kitchenware/Dinnerware/plate_small.usd",
    ],
    "fruit": [
        "SimReady_Furniture_Misc_01_NVD/Assets/simready_content/common_assets/props/orange_02/orange_02.usd",
        "Residential_NVD/Assets/ArchVis/Residential/Food/Fruit/Apple.usd",
        "Lightwheel_OpenSource/Locomotion/KitchenRoom/Kitchen_Other/Kitchen_Orange002.usd",
    ],
    "apple": [
        "Residential_NVD/Assets/ArchVis/Residential/Food/Fruit/Apple.usd",
    ],
    "orange": [
        "SimReady_Furniture_Misc_01_NVD/Assets/simready_content/common_assets/props/orange_02/orange_02.usd",
        "SimReady_Furniture_Misc_01_NVD/Assets/simready_content/common_assets/props/orange_01/orange_01.usd",
        "Residential_NVD/Assets/ArchVis/Residential/Decor/Tchotchkes/Orange_02.usd",
    ],
    "bin": [
        "SimReady_Containers_Shipping_02_NVD/Assets/simready_content/common_assets/props/box_a01/box_a01.usd",
        "SimReady_Containers_Shipping_02_NVD/Assets/simready_content/common_assets/props/standardwoodcrate_a22/standardwoodcrate_a22.usd",
    ],
    "bin_large": [
        "SimReady_Containers_Shipping_02_NVD/Assets/simready_content/common_assets/props/standardwoodcrate_a22/standardwoodcrate_a22.usd",
        "SimReady_Containers_Shipping_02_NVD/Assets/simready_content/common_assets/props/heavydutywoodcrate_a03/heavydutywoodcrate_a03.usd",
    ],
    "conveyor": [
        "Warehouse_NVD/Assets/DigitalTwin/Assets/Warehouse/Equipment/Conveyors/ConveyorBelt_A/ConveyorBelt_A12_PR_NVD_01.usd",
        "Warehouse_NVD/Assets/DigitalTwin/Assets/Warehouse/Equipment/Conveyors/ConveyorBelt_A/ConveyorBelt_A01_PR_NVD_01.usd",
    ],
    "conveyor_short": [
        "Warehouse_NVD/Assets/DigitalTwin/Assets/Warehouse/Equipment/Conveyors/ConveyorBelt_A/ConveyorBelt_A12_PR_NVD_01.usd",
        "Warehouse_NVD/Assets/DigitalTwin/Assets/Warehouse/Equipment/Conveyors/ConveyorBelt_A/ConveyorBelt_A01_PR_NVD_01.usd",
    ],
    "conveyor_long": [
        "Warehouse_NVD/Assets/DigitalTwin/Assets/Warehouse/Equipment/Conveyors/ConveyorBelt_A/ConveyorBelt_A31_PR_NVD_01.usd",
        "Warehouse_NVD/Assets/DigitalTwin/Assets/Warehouse/Equipment/Conveyors/ConveyorBelt_A/ConveyorBelt_A12_PR_NVD_01.usd",
    ],
    "cube": [
        "Lightwheel_oz5iukPxYq_KitchenRoom/omniverse-content-production.s3.us-west-2.amazonaws.com/Assets/Extensions/Samples/Paint/cube.usd",
        "Residential_NVD/Assets/ArchVis/Residential/Entertainment/Games/RubixCube.usd",
    ],
    "cube_small": [
        "Lightwheel_oz5iukPxYq_KitchenRoom/omniverse-content-production.s3.us-west-2.amazonaws.com/Assets/Extensions/Samples/Paint/cube.usd",
        "SimReady_Containers_Shipping_02_NVD/Assets/simready_content/common_assets/props/box_a01/box_a01.usd",
    ],
    "cube_medium": [
        "Lightwheel_oz5iukPxYq_KitchenRoom/omniverse-content-production.s3.us-west-2.amazonaws.com/Assets/Extensions/Samples/Paint/cube.usd",
        "SimReady_Containers_Shipping_02_NVD/Assets/simready_content/common_assets/props/box_a01/box_a01.usd",
    ],
    "cube_large": [
        "SimReady_Containers_Shipping_02_NVD/Assets/simready_content/common_assets/props/box_a01/box_a01.usd",
        "Lightwheel_oz5iukPxYq_KitchenRoom/omniverse-content-production.s3.us-west-2.amazonaws.com/Assets/Extensions/Samples/Paint/cube.usd",
    ],
    "table_small": ["Collected_table/table.usd"],
    "table_medium": ["Collected_table/table.usd"],
    "table_large": ["Collected_table/table.usd"],
}

_CATALOG_QUERIES = {
    "franka_panda": ("franka", "panda", "robot arm", "omron_franka"),
    "bowl": ("serving_bowl", "bowl", "fruit_bowl"),
    "plate": ("plate_small", "plate", "dinnerware"),
    "fruit": ("orange", "apple", "fruit"),
    "apple": ("apple", "fruit"),
    "orange": ("orange", "fruit"),
    "bin": ("box", "crate", "container"),
    "bin_large": ("crate", "container", "box"),
    "conveyor": ("conveyorbelt", "conveyor"),
    "conveyor_short": ("conveyorbelt", "conveyor"),
    "conveyor_long": ("conveyorbelt", "conveyor"),
    "cube": ("cube", "box"),
    "cube_small": ("cube", "box"),
    "cube_medium": ("cube", "box"),
    "cube_large": ("box", "cube"),
    "table_small": ("table",),
    "table_medium": ("table",),
    "table_large": ("table",),
}


@dataclass(frozen=True)
class AssetResolution:
    object_id: str
    object_class: str
    usd_ref: str
    source: str
    label: str = ""
    confidence: Optional[float] = None
    needs_review: bool = False


@dataclass(frozen=True)
class LocalAssetOption:
    label: str
    usd_ref: str
    source: str
    category: str = ""
    relative_path: str = ""
    tags: tuple[str, ...] = ()
    score: int = 0


def _obj_get(obj: Any, attr: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _metadata(obj: Any) -> dict:
    value = _obj_get(obj, "metadata", {}) or {}
    return value if isinstance(value, dict) else {}


def _configured_asset_roots() -> tuple[Path, ...]:
    raw = os.getenv(_ASSET_ROOTS_ENV, "")
    roots = [Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip()]
    if not roots:
        legacy = os.getenv(_LEGACY_ASSET_ROOT_ENV, "")
        roots = [Path(item).expanduser() for item in legacy.split(os.pathsep) if item.strip()]
    if not roots:
        roots = [_DEFAULT_ASSET_ROOT]
    return tuple(root for root in roots if root.exists())


def _existing_override_for_class(object_class: str) -> Optional[str]:
    candidates = _LOCAL_ASSET_OVERRIDES.get(object_class, ())
    for root in _configured_asset_roots():
        for rel_path in candidates:
            candidate = root / rel_path
            if candidate.exists():
                return str(candidate)
    return None


@lru_cache(maxsize=8)
def _load_asset_catalog(root_key: str) -> tuple[dict, ...]:
    roots = [Path(item) for item in root_key.split(os.pathsep) if item]
    assets: list[dict] = []
    for root in roots:
        for catalog_path in root.rglob("asset_catalog.json"):
            try:
                data = json.loads(catalog_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for item in data.get("assets", []):
                if isinstance(item, dict) and item.get("usd_path"):
                    assets.append(item)
    return tuple(assets)


def _catalog_key() -> str:
    return os.pathsep.join(str(root) for root in _configured_asset_roots())


def _score_catalog_item(item: dict, query: str) -> int:
    query = query.lower()
    name = str(item.get("name") or "").lower()
    category = str(item.get("category") or "").lower()
    tags = " ".join(str(tag).lower() for tag in item.get("tags") or [])
    path = str(item.get("usd_path") or "").lower()

    score = 0
    if query == name:
        score += 100
    if query in name:
        score += 60
    if query == category:
        score += 50
    if query in tags:
        score += 30
    if query in path:
        score += 10
    if "/.thumbs/" in path or path.endswith(".usda"):
        score -= 50
    return score


def _catalog_asset_for_class(object_class: str) -> Optional[str]:
    queries = _CATALOG_QUERIES.get(object_class, ())
    if not queries:
        return None

    best: tuple[int, str] | None = None
    for item in _load_asset_catalog(_catalog_key()):
        usd_path = str(item.get("usd_path") or "")
        if not usd_path or not Path(usd_path).exists():
            continue
        score = max(_score_catalog_item(item, query) for query in queries)
        if score <= 0:
            continue
        candidate = (score, usd_path)
        if best is None or candidate > best:
            best = candidate
    return best[1] if best else None


def _normalize_usd_ref(usd_ref: str) -> str:
    """Return a Kit-resolvable USD reference for palette-relative Isaac paths.

    Palette refs are stored relative to the Isaac assets root (e.g.
    "Isaac/Robots/.../franka.usd"). A bare relative ref does not resolve on a
    fresh stage, so prepend the configured assets base. Absolute refs
    (omniverse://, http(s)://, file://, leading slash) are passed through.
    """
    if not usd_ref or "://" in usd_ref or usd_ref.startswith("/"):
        return usd_ref
    if usd_ref.startswith("Isaac/"):
        base = os.getenv(_ASSET_BASE_ENV, _DEFAULT_ISAAC_ASSET_BASE).rstrip("/")
        return f"{base}/{usd_ref}"
    return usd_ref


def _asset_label_from_path(path: Path) -> str:
    name = path.stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(part.capitalize() for part in name.split()) or path.name


@lru_cache(maxsize=8)
def _load_local_asset_files(root_key: str) -> tuple[dict, ...]:
    roots = [Path(item) for item in root_key.split(os.pathsep) if item]
    assets: list[dict] = []
    seen: set[str] = set()

    for item in _load_asset_catalog(root_key):
        usd_path = str(item.get("usd_path") or "")
        if not usd_path or usd_path in seen or not Path(usd_path).exists():
            continue
        seen.add(usd_path)
        assets.append({
            "name": str(item.get("name") or _asset_label_from_path(Path(usd_path))),
            "usd_path": usd_path,
            "relative_path": str(item.get("relative_path") or ""),
            "category": str(item.get("category") or ""),
            "tags": tuple(str(tag) for tag in (item.get("tags") or [])),
            "source": "asset_catalog",
        })

    for root in roots:
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".usd", ".usda", ".usdc"}:
                continue
            usd_path = str(path)
            if usd_path in seen:
                continue
            seen.add(usd_path)
            try:
                relative_path = str(path.relative_to(root))
            except ValueError:
                relative_path = path.name
            assets.append({
                "name": _asset_label_from_path(path),
                "usd_path": usd_path,
                "relative_path": relative_path,
                "category": "",
                "tags": tuple(part.lower() for part in path.parts[-5:-1]),
                "source": "local_file",
            })
    return tuple(assets)


def _score_local_asset(item: dict, query: str) -> int:
    query = (query or "").strip().lower()
    if not query:
        return 1
    terms = [term for term in query.replace("_", " ").replace("-", " ").split() if term]
    if not terms:
        return 1

    name = str(item.get("name") or "").lower().replace("_", " ").replace("-", " ")
    category = str(item.get("category") or "").lower()
    tags = " ".join(str(tag).lower().replace("_", " ").replace("-", " ") for tag in item.get("tags") or [])
    path = str(item.get("usd_path") or "").lower().replace("_", " ").replace("-", " ")
    haystacks = (name, category, tags, path)

    score = 0
    for term in terms:
        if term == name:
            score += 100
        elif term in name:
            score += 70
        if term == category:
            score += 45
        elif category and term in category:
            score += 30
        if term in tags:
            score += 25
        if term in path:
            score += 8
        if not any(term in value for value in haystacks):
            return 0
    if str(item.get("source")) == "asset_catalog":
        score += 5
    path_text = str(item.get("usd_path") or "").lower()
    if "/.thumbs/" in path_text or "/texture/" in path_text:
        score -= 50
    return score


def list_local_asset_options(query: str = "", limit: int = 80) -> List[LocalAssetOption]:
    """Return searchable local USD assets for UI review/selection."""

    limit = max(1, min(int(limit), 250))
    scored: list[LocalAssetOption] = []
    for item in _load_local_asset_files(_catalog_key()):
        usd_ref = str(item.get("usd_path") or "")
        score = _score_local_asset(item, query)
        if score <= 0:
            continue
        scored.append(LocalAssetOption(
            label=str(item.get("name") or _asset_label_from_path(Path(usd_ref))),
            usd_ref=usd_ref,
            source=str(item.get("source") or "local_file"),
            category=str(item.get("category") or ""),
            relative_path=str(item.get("relative_path") or ""),
            tags=tuple(str(tag) for tag in (item.get("tags") or ())),
            score=score,
        ))
    scored.sort(key=lambda item: (-item.score, item.label.lower(), item.usd_ref))
    return scored[:limit]


def local_asset_options_payload(query: str = "", limit: int = 80) -> dict:
    """Return a JSON-safe payload describing local selectable USD assets."""

    options = list_local_asset_options(query=query, limit=limit)
    return {
        "roots": [str(root) for root in _configured_asset_roots()],
        "query": query,
        "count": len(options),
        "options": [asdict(option) for option in options],
    }


def resolve_object_asset(obj: Any) -> Optional[AssetResolution]:
    """Resolve one LayoutSpec object to a USD reference, if known."""

    object_class = str(_obj_get(obj, "object_class", "") or _obj_get(obj, "class", "") or "")
    if not object_class:
        return None

    metadata = _metadata(obj)
    explicit = (
        _obj_get(obj, "asset_path")
        or _obj_get(obj, "asset_ref")
        or metadata.get("asset_path")
        or metadata.get("asset_ref")
        or metadata.get("reviewed_asset_ref")
    )
    palette_entry = get_class(object_class)
    palette_ref = palette_entry.usd_ref if palette_entry else ""
    local_ref = "" if explicit else (_existing_override_for_class(object_class) or "")
    catalog_ref = "" if explicit or local_ref else (_catalog_asset_for_class(object_class) or "")
    usd_ref = _normalize_usd_ref(str(explicit or local_ref or catalog_ref or palette_ref or ""))
    if not usd_ref:
        return None

    confidence = metadata.get("cosmos_confidence")
    if not isinstance(confidence, (int, float)):
        confidence = None
    label = str(metadata.get("cosmos_label") or "")
    if explicit:
        source = "explicit"
    elif local_ref:
        source = "local_assets"
    elif catalog_ref:
        source = "asset_catalog"
    else:
        source = "palette"
    needs_review = bool(
        metadata.get("requires_asset_review")
        or (confidence is not None and confidence < 0.7)
        or object_class == "obstacle_box"
    )
    return AssetResolution(
        object_id=str(_obj_get(obj, "id", "")),
        object_class=object_class,
        usd_ref=usd_ref,
        source=source,
        label=label,
        confidence=float(confidence) if confidence is not None else None,
        needs_review=needs_review,
    )


def resolve_layout_assets(objects: Iterable[Any]) -> List[AssetResolution]:
    """Resolve all known object assets in a LayoutSpec object collection."""

    resolved: List[AssetResolution] = []
    for obj in objects:
        item = resolve_object_asset(obj)
        if item is not None:
            resolved.append(item)
    return resolved
