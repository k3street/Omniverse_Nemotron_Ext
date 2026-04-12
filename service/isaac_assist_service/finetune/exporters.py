import json
import logging
from typing import List, Dict, Any
from pathlib import Path

from service.isaac_assist_service.knowledge.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Isaac Assist, an AI agent by 10Things, Inc. with full control over "
    "NVIDIA Isaac Sim. You can create and modify USD prims, apply physics and materials, "
    "build OmniGraph action graphs, attach sensors, control the simulation, import robots, "
    "generate synthetic data, and debug console errors. You execute Python code inside the "
    "Kit process using omni.kit.commands (all actions are Ctrl+Z undoable). Always explain "
    "what you will do before executing, and show the code for user approval."
)


class FinetuneExporter:
    """Exports the KnowledgeBase to different LLM fine-tuning structures."""
    
    def __init__(self, kb: KnowledgeBase, output_dir: str = "workspace/finetune_exports"):
        self.kb = kb
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def export_unsloth_format(self, version: str) -> Path:
        """
        Exports data into ShareGPT format, optimal for Unsloth running Qwen / Llama 3 models.
        Schema: {"conversations": [{"from": "system", ...}, {"from": "human", ...}, {"from": "gpt", ...}]}
        """
        entries = self.kb.get_entries(version)
        export_path = self.output_dir / f"unsloth_{version}.jsonl"
        
        with open(export_path, "w", encoding="utf-8") as f:
            for entry in entries:
                sharegpt_record = {
                    "conversations": [
                        {"from": "system", "value": SYSTEM_PROMPT},
                        {"from": "human", "value": entry.get("instruction", "")},
                        {"from": "gpt", "value": entry.get("response", "")}
                    ]
                }
                f.write(json.dumps(sharegpt_record) + "\n")
                
        logger.info(f"Exported {len(entries)} entries to {export_path}")
        return export_path

    def export_gemini_format(self, version: str) -> Path:
        """
        Exports data into GCP Vertex AI Gemini fine-tuning JSONL schema.
        """
        entries = self.kb.get_entries(version)
        export_path = self.output_dir / f"gemini_{version}.jsonl"
        
        with open(export_path, "w", encoding="utf-8") as f:
            for entry in entries:
                gemini_record = {
                    "contents": [
                        {"role": "user", "parts": [{"text": f"[System] {SYSTEM_PROMPT}"}]},
                        {"role": "model", "parts": [{"text": "Understood. I am Isaac Assist, ready to help."}]},
                        {"role": "user", "parts": [{"text": entry.get("instruction", "")}]},
                        {"role": "model", "parts": [{"text": entry.get("response", "")}]}
                    ]
                }
                f.write(json.dumps(gemini_record) + "\n")
                
        logger.info(f"Exported {len(entries)} entries to {export_path}")
        return export_path

    def export_openai_format(self, version: str) -> Path:
        """
        Exports data into OpenAI fine-tuning chat JSONL schema.
        Schema: {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}
        """
        entries = self.kb.get_entries(version)
        export_path = self.output_dir / f"openai_{version}.jsonl"

        with open(export_path, "w", encoding="utf-8") as f:
            for entry in entries:
                record = {
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": entry.get("instruction", "")},
                        {"role": "assistant", "content": entry.get("response", "")},
                    ]
                }
                f.write(json.dumps(record) + "\n")

        logger.info(f"Exported {len(entries)} entries to {export_path}")
        return export_path

    def export_alpaca_format(self, version: str) -> Path:
        """
        Exports data into Alpaca instruction-tuning JSONL schema.
        Schema: {"instruction": ..., "input": "", "output": ..., "system": ...}
        """
        entries = self.kb.get_entries(version)
        export_path = self.output_dir / f"alpaca_{version}.jsonl"

        with open(export_path, "w", encoding="utf-8") as f:
            for entry in entries:
                record = {
                    "instruction": entry.get("instruction", ""),
                    "input": "",
                    "output": entry.get("response", ""),
                    "system": SYSTEM_PROMPT,
                }
                f.write(json.dumps(record) + "\n")

        logger.info(f"Exported {len(entries)} entries to {export_path}")
        return export_path
