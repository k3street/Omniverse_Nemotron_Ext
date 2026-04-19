from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime


class SceneFile(BaseModel):
    """A companion file associated with a USD scene."""
    category: str  # "urdf", "rviz", "isaaclab", "launch", "config"
    filename: str
    relative_path: str  # path within the scene workspace folder
    created_at: str
    description: str = ""
    source: str = "generated"  # "generated", "imported", "user"


class SceneManifest(BaseModel):
    """Tracks all companion files for a single USD scene."""
    scene_slug: str
    usd_path: str  # absolute path to the USD scene file
    usd_filename: str
    created_at: str
    updated_at: str
    files: Dict[str, SceneFile] = {}  # relative_path -> SceneFile


class SceneWorkspaceSummary(BaseModel):
    """Lightweight summary returned by list operations."""
    scene_slug: str
    usd_path: str
    usd_filename: str
    file_count: int
    categories: List[str]
    updated_at: str


class AddFileRequest(BaseModel):
    """Request to add/update a companion file."""
    usd_path: str
    category: str
    filename: str
    content: str
    description: str = ""
    source: str = "generated"


class GenerateURDFRequest(BaseModel):
    """Request to generate URDF from the current scene's articulation."""
    robot_prim_path: str
    output_filename: Optional[str] = None


class GenerateRvizRequest(BaseModel):
    """Request to generate rviz config from active ROS2 topics."""
    fixed_frame: str = "world"
    output_filename: Optional[str] = None
