"""Source registry — maps doc-domain IDs to crawl targets.

MVP: sources are hardcoded.  A future migration will load them from a
JSONL config file so operators can add custom sources without code changes.
"""
import json
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


class SourceRegistry:
    """In-memory registry of enabled documentation sources.

    Provides a single ``get_sources()`` method that returns only enabled
    entries; callers do not need to filter themselves.
    """

    def __init__(self):
        self.sources = [
            {
                "source_id": "nvidia_isaac_sim_5_1",
                "name": "Isaac Sim 5.1 Docs",
                "source_type": "official_docs",
                "url": "https://docs.isaacsim.omniverse.nvidia.com/5.1.0/",
                "trust_tier": 1,
                "version_scope": "5.1.0",
                "enabled": True
            },
            {
                "source_id": "nvidia_isaac_sim_6_0",
                "name": "Isaac Sim 6.0 Docs",
                "source_type": "official_docs",
                "url": "https://docs.isaacsim.omniverse.nvidia.com/6.0.0/",
                "trust_tier": 1,
                "version_scope": "6.0.0",
                "enabled": True
            }
        ]

    def get_sources(self) -> List[Dict]:
        """Return all sources whose ``enabled`` flag is True.

        Returns:
            list[dict]: Enabled source entries in insertion order.
        """
        return [s for s in self.sources if s["enabled"]]
