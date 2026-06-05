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
    target_format: str # "unsloth" | "gemini" | "openai" | "alpaca"

@router.post("/export")
async def export_dataset(req: ExportRequest):
    """
    Triggers an export of the version-specific memory bank into a JSONL format
    specifically tailored for Unsloth, Gemini, OpenAI, or Alpaca fine-tuning.
    """
    try:
        format_map = {
            "unsloth": exporter.export_unsloth_format,
            "gemini": exporter.export_gemini_format,
            "openai": exporter.export_openai_format,
            "alpaca": exporter.export_alpaca_format,
        }
        export_fn = format_map.get(req.target_format)
        if not export_fn:
            raise HTTPException(
                status_code=400,
                detail=f"target_format must be one of: {', '.join(format_map.keys())}"
            )
        path = export_fn(req.version)
        return {"status": "success", "file_path": str(path), "download_url": f"/api/v1/finetune/download?filepath={path}"}
    except HTTPException:
        raise
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
