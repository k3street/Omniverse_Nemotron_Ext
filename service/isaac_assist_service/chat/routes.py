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
    Primary endpoint for the UI extension. It parses the intent,
    reaches out to the configured LLM, and logs it to memory.
    """
    try:
        response_text = await orchestrator.handle_message(
            session_id=req.session_id,
            user_message=req.message
        )
        # Wrap response into the structured contract
        return {
            "intent": "general", # Intent parsing stubbed
            "response_messages": [
                {
                    "role": "assistant",
                    "message_type": "text",
                    "content": response_text
                }
            ],
            "actions_to_approve": None,
            "sources_consulted": []
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
