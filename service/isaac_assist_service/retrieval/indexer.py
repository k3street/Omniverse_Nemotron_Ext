import logging
from typing import List
from .storage.fts_store import FTSStore
import uuid

logger = logging.getLogger(__name__)

class DocumentIndexer:
    def __init__(self):
        self.store = FTSStore()

    def index_document(self, source_id: str, raw_text: str, url: str, version: str):
        """
        Splits raw document text into simplistic chunks and pushes to FTS5 search index.
        MVP chunking strategy: split by double newlines (paragraphs).
        """
        chunks = raw_text.split('\n\n')
        count = 0
        
        for i, chunk in enumerate(chunks):
            cleaned = chunk.strip()
            if len(cleaned) < 50:
                continue # Skip tiny noise
                
            # Naive heading extraction
            header = "General"
            if cleaned.startswith("#"):
                header = cleaned.split("\n")[0].replace("#", "").strip()
            
            # For MVP, assume trust tier 1 (authoritative docs)
            self.store.insert_chunk(
                source_id=source_id,
                content=cleaned,
                section_path=header,
                url=url,
                version_scope=version,
                trust_tier=1
            )
            count += 1
            
        logger.info(f"Indexed {count} chunks for {source_id} at {version}.")
        return count
