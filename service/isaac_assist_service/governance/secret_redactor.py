import re
import logging
from typing import List, Optional

from service.isaac_assist_service.governance.models import GovernanceConfig

logger = logging.getLogger(__name__)

class SecretRedactor:
    """Uses regex patterns to prevent accidental credential leakage."""

    def __init__(self, config: GovernanceConfig = None):
        self.config = config or GovernanceConfig()
        self.compiled_patterns = [re.compile(p) for p in self.config.secret_patterns]

    def redact_text(self, text: str) -> str:
        """
        Replaces matched secrets in the text with a [REDACTED] placeholder.
        """
        if not text:
            return text

        redacted_text = text
        for pattern in self.compiled_patterns:
            # We use a lambda to avoid exposing the matched string, just replace it entirely.
            # For a more advanced version, we might want to keep the context.
            redacted_text = pattern.sub("[REDACTED_SECRET]", redacted_text)
            
        return redacted_text

    def redact_dict(self, data: dict) -> dict:
        """
        Recursively redact secrets from dictionary values.
        """
        redacted_data = {}
        for k, v in data.items():
            if isinstance(v, str):
                redacted_data[k] = self.redact_text(v)
            elif isinstance(v, dict):
                redacted_data[k] = self.redact_dict(v)
            elif isinstance(v, list):
                redacted_data[k] = [
                    self.redact_text(item) if isinstance(item, str) 
                    else self.redact_dict(item) if isinstance(item, dict) 
                    else item 
                    for item in v
                ]
            else:
                redacted_data[k] = v
        return redacted_data

    def has_secrets(self, text: str) -> bool:
        """
        Returns true if the text contains secrets.
        """
        if not text:
            return False
            
        for pattern in self.compiled_patterns:
            if pattern.search(text):
                return True
        return False
