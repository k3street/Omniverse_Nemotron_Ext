import asyncio
import logging
import json

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

logger = logging.getLogger(__name__)


class _UsdSafeEncoder(json.JSONEncoder):
    """Fallback encoder that converts leftover USD/Gf types to primitives."""
    def default(self, obj):
        if hasattr(obj, '__float__'):
            try:
                return float(obj)
            except Exception:
                pass
        if hasattr(obj, '__int__'):
            try:
                return int(obj)
            except Exception:
                pass
        if hasattr(obj, '__iter__'):
            try:
                return [self.default(x) if not isinstance(x, (bool, int, float, str, list, dict, type(None))) else x for x in obj]
            except Exception:
                pass
        return str(obj)

class AssistServiceClient:
    """
    Connects the Extension UI to the external Python LiveKit/LLM orchestrator service.
    Defaults to localhost:8000.
    """
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session_id = "default_session"
        self._json_serialize = lambda obj: json.dumps(obj, cls=_UsdSafeEncoder)

    async def send_message(self, text: str, context: dict = None) -> dict:
        """ Sends a chat message to the orchestration service """
        if not HAS_AIOHTTP:
            logger.warning("aiohttp not installed in the Isaac Sim python environment. Mocking response.")
            await asyncio.sleep(1)
            return {"response_messages": [{"role": "assistant", "content": f"Mock echo: {text}"}]}
            
        url = f"{self.base_url}/api/v1/chat/message"
        payload = {
            "session_id": self.session_id,
            "message": text,
            "context": context or {}
        }
        
        try:
            async with aiohttp.ClientSession(json_serialize=self._json_serialize) as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"Service returned status: {response.status}")
                        return {"error": "Failed to communicate with service"}
        except Exception as e:
            logger.error(f"Error communicating with Assist Service: {e}")
            return {"error": str(e)}

    async def generate_plan(self, user_query: str, findings: list = None,
                            scope: str = "scene", mode: str = "auto") -> dict:
        """ Submits findings/intent to the Swarm Plan Generator endpoint. """
        url = f"{self.base_url}/api/v1/plans/generate"
        payload = {
            "req": {
                "finding_ids": [f.get("finding_id", f) if isinstance(f, dict) else f
                                for f in (findings or [])],
                "user_request": user_query,
                "scope": scope,
                "mode": mode,
            }
        }
        try:
            async with aiohttp.ClientSession(json_serialize=self._json_serialize) as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"error": f"Failed (HTTP {response.status})"}
        except Exception as e:
            return {"error": str(e)}

    async def get_pipeline_plan(self, prompt: str) -> dict:
        """Get a structured multi-phase pipeline plan from the service."""
        url = f"{self.base_url}/api/v1/chat/pipeline/plan"
        payload = {
            "prompt": prompt,
            "session_id": self.session_id,
        }
        try:
            async with aiohttp.ClientSession(json_serialize=self._json_serialize) as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as response:
                    if response.status == 200:
                        return await response.json()
                    body = await response.text()
                    return {"error": f"Pipeline plan failed (HTTP {response.status}): {body[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    async def update_settings(self, settings_payload: dict) -> dict:
        """ Updates `.env` variables via the unified Settings Manager """
        url = f"{self.base_url}/api/v1/settings/"
        try:
            async with aiohttp.ClientSession(json_serialize=self._json_serialize) as session:
                async with session.post(url, json={"settings": settings_payload}) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"error": f"Failed (HTTP {response.status})"}
        except Exception as e:
            return {"error": str(e)}

    async def switch_llm_mode(self, mode: str) -> dict:
        """Hot-switch the LLM provider without restarting the service."""
        url = f"{self.base_url}/api/v1/settings/llm_mode"
        try:
            async with aiohttp.ClientSession(json_serialize=self._json_serialize) as session:
                async with session.put(url, json={"mode": mode}) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"error": f"Failed (HTTP {response.status})"}
        except Exception as e:
            return {"error": str(e)}

    async def export_knowledge(self) -> dict:
        """ Triggers JSONL finetuning extraction from the Knowledge Base """
        url = f"{self.base_url}/api/v1/finetune/export"
        try:
            async with aiohttp.ClientSession(json_serialize=self._json_serialize) as session:
                async with session.post(url) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"error": f"Failed (HTTP {response.status})"}
        except Exception as e:
            return {"error": str(e)}

    async def log_execution(self, code: str, success: bool, output: str = "", user_message: str = "") -> dict:
        """ Logs a patch execution result to the audit trail and knowledge base. """
        if not HAS_AIOHTTP:
            return {"status": "skipped"}
        url = f"{self.base_url}/api/v1/chat/log_execution"
        payload = {
            "session_id": self.session_id,
            "code": code,
            "success": success,
            "output": output[:2000],
            "user_message": user_message,
        }
        try:
            async with aiohttp.ClientSession(json_serialize=self._json_serialize) as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"error": f"Failed (HTTP {response.status})"}
        except Exception as e:
            logger.warning(f"Failed to log execution: {e}")
            return {"error": str(e)}

    async def reset_session(self) -> dict:
        """Clear conversation history and open a new empty stage."""
        if not HAS_AIOHTTP:
            return {"status": "skipped"}
        url = f"{self.base_url}/api/v1/chat/reset"
        payload = {"session_id": self.session_id}
        try:
            async with aiohttp.ClientSession(json_serialize=self._json_serialize) as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"error": f"Failed (HTTP {response.status})"}
        except Exception as e:
            return {"error": str(e)}
