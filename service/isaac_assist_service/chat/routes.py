from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import logging
from .orchestrator import ChatOrchestrator
from ..governance.audit_log import AuditLogger
from ..governance.models import AuditEntry
from ..knowledge.knowledge_base import KnowledgeBase
from ..retrieval.context_retriever import detect_isaac_version

logger = logging.getLogger(__name__)

router = APIRouter()
orchestrator = ChatOrchestrator()
_audit = AuditLogger()
_kb = KnowledgeBase()

# Define the data contract matching 10_CHAT_UX.md
class ChatMessageRequest(BaseModel):
    session_id: str
    message: str
    attachments: Optional[List[str]] = []
    context: Optional[Dict[str, Any]] = None


class ResetSessionRequest(BaseModel):
    session_id: str = "default_session"


@router.post("/reset")
async def reset_session(req: ResetSessionRequest):
    """
    Clear conversation history for a session so the user can start fresh.
    Resets both in-memory chat context and persisted conversation logs.
    """
    from ..memory import MemoryManager
    try:
        orchestrator.reset_session(req.session_id)
        MemoryManager().clear_session(req.session_id)
        logger.info(f"[reset] Session '{req.session_id}' cleared")
        return {"status": "ok", "message": "Session history cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/message")
async def send_message(req: ChatMessageRequest):
    """
    Primary endpoint for the UI extension. Classifies intent, calls LLM
    with tool schemas, executes tool calls, and returns structured response.
    """
    try:
        result = await orchestrator.handle_message(
            session_id=req.session_id,
            user_message=req.message,
            context=req.context,
            attachments=req.attachments,
        )

        # Build response messages
        response_messages = [
            {
                "role": "assistant",
                "message_type": "text",
                "content": result["reply"],
            }
        ]

        # Add code patches as separate approvable actions
        actions_to_approve = None
        if result.get("code_patches"):
            actions_to_approve = [
                {
                    "type": "code_patch",
                    "code": patch["code"],
                    "description": patch["description"],
                }
                for patch in result["code_patches"]
            ]

        return {
            "intent": result["intent"],
            "response_messages": response_messages,
            "actions_to_approve": actions_to_approve,
            "tool_calls": result.get("tool_calls", []),
            "sources_consulted": [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LogExecutionRequest(BaseModel):
    session_id: str = "default_session"
    code: str
    success: bool
    output: str = ""
    user_message: Optional[str] = None


@router.post("/log_execution")
async def log_execution(req: LogExecutionRequest):
    """
    Called by the extension after a patch is approved and executed.
    - Always writes to the audit log.
    - On failure, auto-adds the error pattern to the knowledge base
      so the LLM avoids the same mistake next time.
    """
    version = detect_isaac_version()

    # ── 1. Audit log (always) ────────────────────────────────────────────
    entry = AuditEntry(
        entry_id=str(uuid.uuid4()),
        timestamp=datetime.utcnow(),
        event_type="patch_executed",
        user_decision="approved",
        metadata={
            "success": req.success,
            "code": req.code[:2000],
            "output": req.output[:2000],
            "user_message": req.user_message or "",
        },
    )
    _audit.log_entry(entry)
    logger.info(f"[audit] Patch {'SUCCESS' if req.success else 'FAIL'}: {req.code[:80]}...")

    # ── Auto-compact periodically ────────────────────────────────────────
    global _log_counter
    _log_counter += 1
    if _log_counter % _COMPACT_EVERY == 0:
        try:
            _kb.compact(version)
        except Exception as ce:
            logger.warning(f"[knowledge] Auto-compaction failed: {ce}")

    # ── 2. Knowledge base learning (always — errors AND successes) ───────
    if not req.success and req.output:
        # Extract the error for the knowledge base (with dedup)
        instruction = (
            f"When asked: '{req.user_message}'\n"
            f"This code was generated but FAILED:\n```python\n{req.code[:1000]}\n```\n"
            f"Error: {req.output[:500]}"
        ) if req.user_message else (
            f"This code FAILED:\n```python\n{req.code[:1000]}\n```\n"
            f"Error: {req.output[:500]}"
        )
        response = (
            "Do NOT generate this pattern again. "
            "The error indicates a runtime incompatibility. "
            "Find an alternative approach or fix the specific issue."
        )
        added = _kb.add_error(version, instruction, response,
                              error_output=req.output)
        if added:
            logger.info(f"[knowledge] Auto-learned NEW error pattern for v{version}")
        else:
            logger.info(f"[knowledge] Skipped duplicate error for v{version}")

    elif req.success and req.user_message:
        # Successful patches become positive examples for the LLM
        instruction = req.user_message
        response = f"```python\n{req.code[:1500]}\n```"
        _kb.add_success(version, instruction, response,
                        code=req.code)
        logger.info(f"[knowledge] Logged successful patch for v{version}")

    return {"status": "logged", "success": req.success}


# ── Compaction ───────────────────────────────────────────────────────────
_log_counter: int = 0
_COMPACT_EVERY: int = 50  # auto-compact after every N logged executions


@router.post("/compact_knowledge")
async def compact_knowledge():
    """
    Manually trigger knowledge base compaction.
    Deduplicates entries, trims old ones, rewrites atomically.
    """
    version = detect_isaac_version()
    result = _kb.compact(version)
    return {"status": "compacted", "version": version, **result}


class ExportSceneRequest(BaseModel):
    session_id: str = "default_session"
    scene_name: str = "exported_scene"


@router.post("/export_scene")
async def export_scene(req: ExportSceneRequest):
    """
    Export the current session's scene as a reusable file package.
    Returns paths to generated scene_setup.py, README.md, ros2_topics.yaml, ros2_launch.py.
    """
    from .tools.tool_executor import execute_tool_call
    try:
        result = await execute_tool_call("export_scene_package", {
            "session_id": req.session_id,
            "scene_name": req.scene_name,
        })
        return result
    except Exception as e:
        logger.error(f"Failed to export scene: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export_scene/download")
async def download_scene_file(filepath: str):
    """
    Download a specific file from a scene export.
    filepath must be under workspace/scene_exports/.
    """
    import os
    from fastapi.responses import FileResponse
    # Security: ensure path is under workspace/scene_exports/
    real_path = os.path.realpath(filepath)
    allowed_dir = os.path.realpath("workspace/scene_exports")
    if not real_path.startswith(allowed_dir):
        raise HTTPException(status_code=403, detail="Access denied: path must be under workspace/scene_exports/")
    if not os.path.exists(real_path):
        raise HTTPException(status_code=404, detail="File not found. Did you export first?")
    return FileResponse(real_path, filename=os.path.basename(real_path))
