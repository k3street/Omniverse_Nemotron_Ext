from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from .orchestrator import ChatOrchestrator

router = APIRouter()
orchestrator = ChatOrchestrator()

# Define the data contract matching 10_CHAT_UX.md
class ChatMessageRequest(BaseModel):
    session_id: str
    message: str
    attachments: Optional[List[str]] = []
    context: Optional[Dict[str, Any]] = None

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
