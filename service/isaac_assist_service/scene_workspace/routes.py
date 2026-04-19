"""
REST routes for Scene Workspace management.

GET  /api/v1/scenes                          — list all scene workspaces
GET  /api/v1/scenes/{slug}                   — get manifest for a scene
GET  /api/v1/scenes/{slug}/files             — list files (optional ?category=)
GET  /api/v1/scenes/{slug}/files/{path:path} — read a companion file
POST /api/v1/scenes/{slug}/files             — add/update a companion file
DELETE /api/v1/scenes/{slug}/files/{path:path} — remove a companion file
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .manager import SceneWorkspaceManager
from .models import AddFileRequest, SceneManifest, SceneWorkspaceSummary, SceneFile

logger = logging.getLogger(__name__)
router = APIRouter()

_manager = SceneWorkspaceManager()


class AddFileBody(BaseModel):
    usd_path: str
    category: str
    filename: str
    content: str
    description: str = ""
    source: str = "generated"


class InitSceneBody(BaseModel):
    usd_path: str


# ── List all scene workspaces ─────────────────────────────────────────

@router.get("/", response_model=list[SceneWorkspaceSummary])
async def list_scenes():
    """Return summaries of all scene workspaces."""
    return _manager.list_scenes()


# ── Init / get manifest ──────────────────────────────────────────────

@router.post("/init", response_model=SceneManifest)
async def init_scene(body: InitSceneBody):
    """Initialize a scene workspace (idempotent)."""
    return _manager.get_or_create(body.usd_path)


@router.get("/{slug}", response_model=SceneManifest)
async def get_scene(slug: str):
    """Get manifest for a specific scene workspace."""
    # We need to find the manifest by slug directly
    manifest = _manager._load_manifest(slug)
    if manifest is None:
        raise HTTPException(404, f"No scene workspace '{slug}'")
    return manifest


# ── File operations ──────────────────────────────────────────────────

@router.get("/{slug}/files", response_model=list[SceneFile])
async def list_files(slug: str, category: Optional[str] = Query(None)):
    """List companion files, optionally filtered by category."""
    manifest = _manager._load_manifest(slug)
    if manifest is None:
        raise HTTPException(404, f"No scene workspace '{slug}'")
    files = list(manifest.files.values())
    if category:
        files = [f for f in files if f.category == category]
    return files


@router.get("/{slug}/files/{file_path:path}")
async def read_file(slug: str, file_path: str):
    """Read the content of a companion file."""
    manifest = _manager._load_manifest(slug)
    if manifest is None:
        raise HTTPException(404, f"No scene workspace '{slug}'")
    try:
        content = _manager.get_file_content(manifest.usd_path, file_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if content is None:
        raise HTTPException(404, f"File not found: {file_path}")
    return {"file_path": file_path, "content": content}


@router.post("/{slug}/files", response_model=SceneFile)
async def add_file(slug: str, body: AddFileBody):
    """Add or update a companion file in the scene workspace."""
    manifest = _manager._load_manifest(slug)
    if manifest is None:
        raise HTTPException(404, f"No scene workspace '{slug}'")
    try:
        return _manager.add_file(
            usd_path=manifest.usd_path,
            category=body.category,
            filename=body.filename,
            content=body.content,
            description=body.description,
            source=body.source,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{slug}/files/{file_path:path}")
async def delete_file(slug: str, file_path: str):
    """Remove a companion file from the scene workspace."""
    manifest = _manager._load_manifest(slug)
    if manifest is None:
        raise HTTPException(404, f"No scene workspace '{slug}'")
    try:
        removed = _manager.delete_file(manifest.usd_path, file_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not removed:
        raise HTTPException(404, f"File not found: {file_path}")
    return {"removed": file_path}
