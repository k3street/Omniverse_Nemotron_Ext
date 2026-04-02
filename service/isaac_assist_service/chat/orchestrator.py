from typing import Dict, List, Optional
from .llm_ollama import OllamaProvider, LLMResponse
import json

class ChatOrchestrator:
    """
    Manages building the context for Nemotron-Cascade-2 depending on the user query.
    It links the live scene tree (textual), documentation, and chat interface.
    """
    def __init__(self):
        # We assume the user has ran: ollama create isaac-assist-nemotron -f Modelfile
        self.llm_provider = OllamaProvider(model="isaac-assist-nemotron")
        
    async def process_message(self, user_message: str, stage_context: str, selection_context: List[str], prior_history: List[Dict]) -> Dict:
        """
        Process an incoming user message, enrich it with stage data, and query Nemotron.
        """
        messages = []
        # Replay conversation history
        for msg in prior_history[-10:]: # keep context short
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        # Build current turn context
        enriched_prompt = self._build_prompt(user_message, stage_context, selection_context)
        
        # Our recent history already includes the user_message from the DB, but we only want to 
        # augment it with the stage background context on this active turn, not save the 
        # giant stage context to memory.
        if len(messages) > 0 and messages[-1]["role"] == "user":
            messages[-1]["content"] = enriched_prompt
        else:
            messages.append({"role": "user", "content": enriched_prompt})
        
        # Invoke LLM
        response = await self.llm_provider.complete(messages, {})
        
        self.memory.add_message(session_id, "assistant", response.text)
        
        return {
            "text": response.text,
            "actions": response.actions,
            "intent": "general" # In a full system, we might have an intent classifier ahead of this
        }

    def _build_prompt(self, message: str, stage_context: str, selection_context: List[str]) -> str:
        """
        Embeds the USD textual 'vision' and the active selections directly into the prompt.
        """
        prompt = ""
        if stage_context:
            prompt += f"Background Context - Active Stage Hierarchy:\n{stage_context}\n\n"
            
        if selection_context:
            prompt += f"User's Active Selections:\n"
            for sel in selection_context:
                prompt += f"- {sel}\n"
            prompt += "\n"
            
        prompt += f"User Request: {message}\n"
        return prompt
