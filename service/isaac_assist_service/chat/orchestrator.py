from typing import Dict, List
from .provider_factory import get_llm_provider
import logging

logger = logging.getLogger(__name__)


class ChatOrchestrator:
    """
    Manages multi-turn chat sessions, injects stage context, and calls the
    configured LLM provider (Ollama local or Gemini cloud).
    """

    def __init__(self):
        self.llm_provider = get_llm_provider()
        # Lightweight in-memory session history: {session_id: [msg, ...]}
        self._history: Dict[str, List[Dict]] = {}

    async def handle_message(self, session_id: str, user_message: str) -> str:
        """
        Primary entry point called by the route handler.
        Returns the assistant's reply as a plain string.
        """
        history = self._history.setdefault(session_id, [])
        logger.info(f"[{session_id}] USER: {user_message}")

        # Build the messages list for the LLM
        messages = list(history[-10:])  # rolling context window
        messages.append({"role": "user", "content": user_message})

        try:
            response = await self.llm_provider.complete(messages, {})
            reply = response.text
        except Exception as e:
            logger.error(f"LLM provider error: {e}")
            raise

        logger.info(f"[{session_id}] ASSISTANT: {reply}")

        # Persist to in-memory history
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})

        return reply

    def _build_prompt(self, message: str, stage_context: str, selection_context: List[str]) -> str:
        prompt = ""
        if stage_context:
            prompt += f"Background Context - Active Stage Hierarchy:\n{stage_context}\n\n"
        if selection_context:
            prompt += "User's Active Selections:\n"
            for sel in selection_context:
                prompt += f"- {sel}\n"
            prompt += "\n"
        prompt += f"User Request: {message}\n"
        return prompt
