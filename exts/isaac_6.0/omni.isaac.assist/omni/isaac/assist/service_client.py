import asyncio
import logging
import json

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)

class AssistServiceClient:
    """
    Connects the Extension UI to the external Python LiveKit/LLM orchestrator service.
    Defaults to localhost:8000.
    """
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session_id = "default_session"

    async def reset_session(self) -> dict:
        """Clear conversation history and open a new empty stage."""
        if not HAS_AIOHTTP:
            return {"status": "skipped"}
        url = f"{self.base_url}/api/v1/chat/reset"
        payload = {"session_id": self.session_id}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"error": f"Failed (HTTP {response.status})"}
        except Exception as e:
            return {"error": str(e)}

    async def send_message(self, text: str) -> dict:
        """ Sends a chat message to the orchestration service """
        if not HAS_AIOHTTP:
            logger.warning("aiohttp not installed in the Isaac Sim python environment. Mocking response.")
            await asyncio.sleep(1)
            return {"response_messages": [{"role": "assistant", "content": f"Mock echo: {text}"}]}
            
        url = f"{self.base_url}/api/v1/chat/message"
        payload = {
            "session_id": self.session_id,
            "message": text,
            "context": {}
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"Service returned status: {response.status}")
                        return {"error": "Failed to communicate with service"}
        except Exception as e:
            logger.error(f"Error communicating with Assist Service: {e}")
            return {"error": str(e)}
