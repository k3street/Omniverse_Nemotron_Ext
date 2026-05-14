"""Pydantic models for the RAG retrieval subsystem."""
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime


class Source(BaseModel):
    """A registered documentation source for the FTS index.

    Attributes:
        source_id: Stable identifier, e.g. ``"nvidia_isaac_sim_5_1"``.
        name: Human-readable display name.
        source_type: ``"official_docs"`` | ``"community"`` | ``"auto_captured"``.
        url: Root URL of the source.
        trust_tier: 1 = authoritative, 2 = community, 3 = auto-generated.
        version_scope: Isaac Sim version this source covers, e.g. ``"5.1.0"``.
        refresh_policy: How often the index should be refreshed.
        enabled: When False the source is excluded from retrieval.
        last_indexed: UTC time of the most recent indexing run.
        chunk_count: Number of chunks currently indexed from this source.
    """
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
    """A single indexed document chunk stored in FTSStore.

    Attributes:
        chunk_id: UUID assigned at insertion time.
        source_id: Parent source identifier.
        content: Raw text content (up to ~800 chars per chunk).
        section_path: Heading / path extracted from the document.
        url: Canonical URL of the original document.
        version_scope: Isaac Sim version this chunk covers.
        trust_tier: Inherited from the parent Source.
        indexed_at: UTC timestamp of insertion.
    """
    chunk_id: str
    source_id: str
    content: str
    section_path: str
    url: str
    version_scope: str
    trust_tier: int
    indexed_at: datetime
    
class RetrievalResult(BaseModel):
    """A single ranked retrieval result returned by the RAG query endpoint.

    Attributes:
        chunk: The matched document chunk.
        relevance_score: BM25 rank (higher = more relevant).
        trust_score: Numeric trust tier from the parent source.
        version_match: ``"exact"`` | ``"mismatch"`` | ``"wildcard"``.
        version_match_score: Numeric similarity to requested version scope.
        freshness_score: Recency score based on ``indexed_at``.
    """
    chunk: IndexChunk
    relevance_score: float
    trust_score: float
    version_match: str
    version_match_score: float
    freshness_score: float

class RetrievalQuery(BaseModel):
    """Input payload for the ``POST /retrieval/query`` endpoint.

    Attributes:
        query: Free-text search string.
        context: Optional caller-supplied metadata (session info, etc.).
        mode: ``"curated"`` uses only enabled trusted sources.
        top_k: Maximum number of results to return.
        version_scope: ``"auto"`` detects from ``ISAAC_SIM_PATH``; or specify
            a version string like ``"5.1.0"`` or ``"6.0.0"``.
    """
    query: str
    context: Dict[str, Any] = {}
    mode: str = "curated"
    top_k: int = 5
    version_scope: str = "auto"
