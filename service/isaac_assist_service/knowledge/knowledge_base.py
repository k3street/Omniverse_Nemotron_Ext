import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from service.isaac_assist_service.config import config

logger = logging.getLogger(__name__)

class KnowledgeBase:
    """
    Long-term experiential memory. Stores instructional data 
    separated by the application version to build fine-tuning datasets.
    """
    def __init__(self, storage_dir: str = "workspace/knowledge"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
    def _get_file_path(self, version: str) -> Path:
        # Sanitize version string for file names
        clean_version = "".join(c for c in version if c.isalnum() or c in "._-").strip()
        if not clean_version:
            clean_version = "default_version"
        return self.storage_dir / f"knowledge_{clean_version}.jsonl"

    def add_entry(self, version: str, instruction: str, response: str, source: str = "audit"):
        """Appends a new QA pair / instruction pair to the version-specific KB if opt-in is enabled."""
        if not config.contribute_data:
            logger.info("Data contribution opt-in is disabled. Skipping storing knowledge base entry.")
            return False
            
        file_path = self._get_file_path(version)
        entry = {
            "instruction": instruction,
            "response": response,
            "source": source
        }
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            return True
        except Exception as e:
            logger.error(f"Failed to append to knowledge base {version}: {e}")
            return False

    def get_entries(self, version: str) -> List[Dict[str, str]]:
        """Retrieves all stored context for a given version."""
        file_path = self._get_file_path(version)
        entries = []
        if not file_path.exists():
            return entries
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))
            return entries
        except Exception as e:
            logger.error(f"Failed to read knowledge base {version}: {e}")
            return []

    def get_supported_versions(self) -> List[str]:
        """Lists all versions we currently have knowledge for."""
        versions = []
        if not self.storage_dir.exists():
            return versions
            
        for file in self.storage_dir.glob("knowledge_*.jsonl"):
            # Strip knowledge_ prefix and .jsonl suffix
            ver = file.name[10:-6]
            versions.append(ver)
        return versions
