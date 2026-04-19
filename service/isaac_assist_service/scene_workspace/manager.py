"""
SceneWorkspaceManager — per-scene companion file storage.

Maps each USD scene to a workspace folder containing companion files
(URDF, rviz configs, Isaac Lab envs, ROS2 launch files, etc.).

Structure:
    workspace/scenes/
        {scene_slug}/
            manifest.json
            urdf/
            rviz/
            isaaclab/
            launch/
            config/
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import SceneFile, SceneManifest, SceneWorkspaceSummary

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, os.pardir, os.pardir,
    "workspace", "scenes",
)
WORKSPACE_ROOT = os.path.normpath(_WORKSPACE_ROOT)

VALID_CATEGORIES = {"urdf", "rviz", "isaaclab", "launch", "config"}


def _slugify(usd_path: str) -> str:
    """Derive a filesystem-safe slug from a USD scene path."""
    name = os.path.splitext(os.path.basename(usd_path))[0]
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug.lower() or "unnamed_scene"


class SceneWorkspaceManager:
    def __init__(self, root: Optional[str] = None):
        self.root = Path(root or WORKSPACE_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)

    # ── Manifest I/O ────────────────────────────────────────────────

    def _scene_dir(self, slug: str) -> Path:
        # Prevent path traversal
        safe = re.sub(r"[^a-zA-Z0-9_\-]", "", slug)
        return self.root / safe

    def _manifest_path(self, slug: str) -> Path:
        return self._scene_dir(slug) / "manifest.json"

    def _load_manifest(self, slug: str) -> Optional[SceneManifest]:
        mp = self._manifest_path(slug)
        if not mp.exists():
            return None
        data = json.loads(mp.read_text())
        return SceneManifest(**data)

    def _save_manifest(self, manifest: SceneManifest) -> None:
        scene_dir = self._scene_dir(manifest.scene_slug)
        scene_dir.mkdir(parents=True, exist_ok=True)
        mp = self._manifest_path(manifest.scene_slug)
        mp.write_text(manifest.model_dump_json(indent=2))

    # ── Public API ──────────────────────────────────────────────────

    def get_or_create(self, usd_path: str) -> SceneManifest:
        """Get existing manifest for a scene, or create a new one."""
        slug = _slugify(usd_path)
        manifest = self._load_manifest(slug)
        if manifest is not None:
            # Update USD path if it changed (scene moved/renamed)
            if manifest.usd_path != usd_path:
                manifest.usd_path = usd_path
                manifest.updated_at = datetime.now(timezone.utc).isoformat()
                self._save_manifest(manifest)
            return manifest

        now = datetime.now(timezone.utc).isoformat()
        manifest = SceneManifest(
            scene_slug=slug,
            usd_path=usd_path,
            usd_filename=os.path.basename(usd_path),
            created_at=now,
            updated_at=now,
        )
        # Create category subdirs
        scene_dir = self._scene_dir(slug)
        for cat in VALID_CATEGORIES:
            (scene_dir / cat).mkdir(parents=True, exist_ok=True)
        self._save_manifest(manifest)
        logger.info("Created scene workspace: %s -> %s", slug, scene_dir)
        return manifest

    def add_file(
        self,
        usd_path: str,
        category: str,
        filename: str,
        content: str | bytes,
        description: str = "",
        source: str = "generated",
    ) -> SceneFile:
        """Write a companion file into the scene workspace."""
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{category}'. "
                f"Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
            )
        # Sanitize filename
        safe_name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", filename)
        if not safe_name:
            raise ValueError("Invalid filename")

        manifest = self.get_or_create(usd_path)
        scene_dir = self._scene_dir(manifest.scene_slug)
        cat_dir = scene_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        file_path = cat_dir / safe_name

        # Prevent path traversal via filename
        if not file_path.resolve().is_relative_to(scene_dir.resolve()):
            raise ValueError("Invalid filename — path traversal detected")

        if isinstance(content, bytes):
            file_path.write_bytes(content)
        else:
            file_path.write_text(content)

        rel_path = f"{category}/{safe_name}"
        now = datetime.now(timezone.utc).isoformat()
        scene_file = SceneFile(
            category=category,
            filename=safe_name,
            relative_path=rel_path,
            created_at=now,
            description=description,
            source=source,
        )
        manifest.files[rel_path] = scene_file
        manifest.updated_at = now
        self._save_manifest(manifest)
        logger.info("Added %s to scene %s", rel_path, manifest.scene_slug)
        return scene_file

    def get_file_content(
        self, usd_path: str, relative_path: str
    ) -> Optional[str]:
        """Read a companion file by its relative path."""
        slug = _slugify(usd_path)
        scene_dir = self._scene_dir(slug)
        file_path = (scene_dir / relative_path).resolve()
        if not file_path.is_relative_to(scene_dir.resolve()):
            raise ValueError("Path traversal detected")
        if not file_path.exists():
            return None
        return file_path.read_text()

    def list_files(
        self, usd_path: str, category: Optional[str] = None
    ) -> List[SceneFile]:
        """List companion files for a scene, optionally filtered by category."""
        slug = _slugify(usd_path)
        manifest = self._load_manifest(slug)
        if manifest is None:
            return []
        files = list(manifest.files.values())
        if category:
            files = [f for f in files if f.category == category]
        return files

    def get_manifest(self, usd_path: str) -> Optional[SceneManifest]:
        """Get manifest for a scene (None if no workspace exists)."""
        slug = _slugify(usd_path)
        return self._load_manifest(slug)

    def list_scenes(self) -> List[SceneWorkspaceSummary]:
        """List all scene workspaces."""
        summaries = []
        if not self.root.exists():
            return summaries
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            manifest = self._load_manifest(entry.name)
            if manifest is None:
                continue
            categories = sorted({f.category for f in manifest.files.values()})
            summaries.append(SceneWorkspaceSummary(
                scene_slug=manifest.scene_slug,
                usd_path=manifest.usd_path,
                usd_filename=manifest.usd_filename,
                file_count=len(manifest.files),
                categories=categories,
                updated_at=manifest.updated_at,
            ))
        return summaries

    def get_scene_dir(self, usd_path: str) -> Path:
        """Return the absolute path to the scene workspace directory."""
        slug = _slugify(usd_path)
        return self._scene_dir(slug)

    def delete_file(self, usd_path: str, relative_path: str) -> bool:
        """Remove a companion file from the scene workspace."""
        slug = _slugify(usd_path)
        manifest = self._load_manifest(slug)
        if manifest is None:
            return False
        scene_dir = self._scene_dir(slug)
        file_path = (scene_dir / relative_path).resolve()
        if not file_path.is_relative_to(scene_dir.resolve()):
            raise ValueError("Path traversal detected")
        if file_path.exists():
            file_path.unlink()
        if relative_path in manifest.files:
            del manifest.files[relative_path]
            manifest.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_manifest(manifest)
            return True
        return False
