from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class Source(BaseModel):
    source_id: str
    name: str
    source_type: str
    url: str
    trust_tier: int
    version_scope: str
    refresh_policy: str
    enabled: bool
    last_indexed: Optional[datetime] = None
    chunk_count: int = 0

class IndexChunk(BaseModel):
    chunk_id: str
    source_id: str
    content: str
    section_path: str
    url: str
    version_scope: str
    trust_tier: int
    indexed_at: datetime
    
class RetrievalResult(BaseModel):
    chunk: IndexChunk
    relevance_score: float
    trust_score: float
    version_match: str
    version_match_score: float
    freshness_score: float

class RetrievalQuery(BaseModel):
    query: str
    context: Dict[str, Any] = {}
    mode: str = "curated"
    top_k: int = 5
    version_scope: str = "auto"
