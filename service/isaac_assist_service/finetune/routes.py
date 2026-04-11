import os
import logging
from typing import Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse

from service.isaac_assist_service.knowledge.knowledge_base import KnowledgeBase
from .exporters import FinetuneExporter

logger = logging.getLogger(__name__)

router = APIRouter()
kb = KnowledgeBase()
exporter = FinetuneExporter(kb)

class ExportRequest(BaseModel):
    version: str
    target_format: str # "unsloth" | "gemini"

@router.post("/export")
async def export_dataset(req: ExportRequest):
    """
    Triggers an export of the version-specific memory bank into a JSONL format
    specifically tailored for either Unsloth (ShareGPT Qwen/Gemma) or Gemini Vertex AI.
    """
    try:
        if req.target_format == "unsloth":
            path = exporter.export_unsloth_format(req.version)
        elif req.target_format == "gemini":
            path = exporter.export_gemini_format(req.version)
        else:
            raise HTTPException(status_code=400, detail="target_format must be 'unsloth' or 'gemini'")
        
        return {"status": "success", "file_path": str(path), "download_url": f"/api/v1/finetune/download?filepath={path}"}
    except Exception as e:
        logger.error(f"Failed to export dataset: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/download")
async def download_dataset(filepath: str):
    """
    Returns the explicitly requested compiled training dataset for manual upload
    to cloud orchestration platforms or local notebooks.
    """
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Requested export file not found out on disk. Did you export first?")
    
    return FileResponse(filepath, media_type="application/jsonl", filename=os.path.basename(filepath))
