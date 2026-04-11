import json
from typing import List, Dict, Any
from pathlib import Path

from service.isaac_assist_service.knowledge.knowledge_base import KnowledgeBase

class FinetuneExporter:
    """Exports the KnowledgeBase to different LLM fine-tuning structures."""
    
    def __init__(self, kb: KnowledgeBase, output_dir: str = "workspace/finetune_exports"):
        self.kb = kb
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def export_unsloth_format(self, version: str) -> Path:
        """
        Exports data into ShareGPT format, optimal for Unsloth running Qwen / Llama 3 models.
        Schema: {"conversations": [{"from": "human", "value": ""}, {"from": "gpt", "value": ""}]}
        """
        entries = self.kb.get_entries(version)
        export_path = self.output_dir / f"unsloth_{version}.jsonl"
        
        with open(export_path, "w", encoding="utf-8") as f:
            for entry in entries:
                sharegpt_record = {
                    "conversations": [
                        {"from": "human", "value": entry.get("instruction", "")},
                        {"from": "gpt", "value": entry.get("response", "")}
                    ]
                }
                f.write(json.dumps(sharegpt_record) + "\n")
                
        return export_path

    def export_gemini_format(self, version: str) -> Path:
        """
        Exports data into GCP Vertex AI Gemini fine-tuning JSONL schema.
        Schema: {
          "contents": [
            {"role": "user", "parts": [{"text": "..."}]},
            {"role": "model", "parts": [{"text": "..."}]}
          ]
        }
        """
        entries = self.kb.get_entries(version)
        export_path = self.output_dir / f"gemini_{version}.jsonl"
        
        with open(export_path, "w", encoding="utf-8") as f:
            for entry in entries:
                gemini_record = {
                    "contents": [
                        {"role": "user", "parts": [{"text": entry.get("instruction", "")}]},
                        {"role": "model", "parts": [{"text": entry.get("response", "")}]}
                    ]
                }
                f.write(json.dumps(gemini_record) + "\n")
                
        return export_path
