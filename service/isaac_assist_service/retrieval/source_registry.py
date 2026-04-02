import json
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

class SourceRegistry:
    """ Manages the mapping of domains to crawl. MVP: Hardcoded defaults. """
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
        return [s for s in self.sources if s["enabled"]]
