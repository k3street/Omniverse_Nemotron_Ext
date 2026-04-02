# 04 — Source Registry + Retrieval Layer

## Purpose

Provide a trust-scored, version-aware retrieval system that fetches authoritative documentation, release notes, GitHub content, and internal playbooks before any diagnostic or repair action. Every recommendation must trace back to named sources.

## Runtime

Background service

## Phase

1–2 (Weeks 3–8)

## Dependencies

- Environment fingerprint (02) for version-scoped retrieval
- Local storage for index persistence

---

## Functional Requirements

### FR-04.1 Source Registry

Maintain a registry of known sources, each with metadata:

| Field | Description |
|-------|-------------|
| `source_id` | Unique identifier |
| `name` | Human-readable name |
| `type` | `official_docs` · `github_repo` · `release_notes` · `api_docs` · `internal_playbook` · `community` |
| `url` | Base URL or local path |
| `trust_tier` | `authoritative` (tier 1) · `curated` (tier 2) · `community` (tier 3) |
| `version_scope` | Which Isaac Sim/Lab versions this source covers |
| `refresh_policy` | `on_startup` · `daily` · `weekly` · `manual` |
| `enabled` | Boolean |

Default sources to ship:
1. Isaac Sim documentation (versioned by detected version)
2. Isaac Lab documentation (versioned)
3. Isaac Sim GitHub repository README + issues
4. Isaac Lab GitHub repository README + issues
5. NVIDIA Omniverse developer docs (Kit, USD, extensions)
6. Isaac Sim release notes
7. Isaac Lab release notes / changelog
8. OpenUSD API reference

### FR-04.2 Source Modes

Support three retrieval modes (user-configurable, default: official-only):

- **Official-only:** Tier 1 sources only (NVIDIA docs, official repos)
- **Curated:** Tier 1 + Tier 2 (adds internal playbooks, curated community resources)
- **Open:** All tiers with clear labeling of source trust level

### FR-04.3 Version-Scoped Retrieval

All retrieval queries must be scoped to the current environment fingerprint:
- Select the documentation version matching the detected Isaac Sim / Isaac Lab version.
- If documentation for the exact version is unavailable, use the nearest available version and flag it as "approximate match."
- Never silently mix documentation from different major versions.

### FR-04.4 Indexing

Build and maintain a local search index of registered sources:
- Chunk documents into semantically coherent segments (target 500–1000 tokens per chunk).
- Store each chunk with metadata: source_id, source_type, trust_tier, version_scope, section_path, url.
- Support both keyword search (BM25 / FTS5) and semantic search (vector embeddings).
- Re-index on schedule per source refresh_policy.
- Index storage location: `~/.isaac_assist/index/`

### FR-04.5 Retrieval Pipeline

For each retrieval query:

1. **Expand query** with context from the current scene, selection, and fingerprint.
2. **Search** the local index (hybrid: keyword + semantic).
3. **Filter** results by version scope and trust tier (per current mode).
4. **Rank** results by relevance score × trust weight (tier 1 = 1.0, tier 2 = 0.8, tier 3 = 0.5).
5. **Deduplicate** near-identical chunks from the same source.
6. **Return** top-K results (default K=10) with full provenance metadata.

### FR-04.6 Trust Scoring

Every retrieved result carries a trust score:

```
trust_score = relevance_score * trust_weight * version_match_score * freshness_score
```

Where:
- `relevance_score`: Search similarity (0–1)
- `trust_weight`: Tier-based (1.0, 0.8, 0.5)
- `version_match_score`: 1.0 for exact version, 0.7 for adjacent minor, 0.4 for adjacent major
- `freshness_score`: 1.0 for content updated within 30 days, decaying by age

### FR-04.7 Source Viewer

Provide a UI component that shows the user exactly which sources informed a recommendation:
- Source name, type, trust tier
- Specific section/page URL
- Relevance score and version match status
- Full text of the retrieved chunk (expandable)

### FR-04.8 Negative Memory Integration

Accept feedback from the knowledge base (module 08) about sources that led to failed fixes:
- Demote source chunks associated with rolled-back or user-rejected fixes.
- Do not permanently exclude — allow re-promotion if the source is updated.

---

## Data Models

### Source

```python
@dataclass
class Source:
    source_id: str
    name: str
    source_type: str         # "official_docs" | "github_repo" | "release_notes" | ...
    url: str
    trust_tier: int          # 1 = authoritative, 2 = curated, 3 = community
    version_scope: str       # Semver range or "*"
    refresh_policy: str      # "on_startup" | "daily" | "weekly" | "manual"
    enabled: bool
    last_indexed: Optional[datetime]
    chunk_count: int

@dataclass
class IndexChunk:
    chunk_id: str
    source_id: str
    content: str
    section_path: str        # e.g., "Installation > Python Environment > Conda"
    url: str                 # Direct URL to this content
    version_scope: str
    trust_tier: int
    embedding: Optional[List[float]]
    indexed_at: datetime

@dataclass
class RetrievalResult:
    chunk: IndexChunk
    relevance_score: float
    trust_score: float
    version_match: str       # "exact" | "adjacent_minor" | "adjacent_major" | "mismatch"
    version_match_score: float
    freshness_score: float

@dataclass
class RetrievalResponse:
    query: str
    context: Dict[str, str]  # fingerprint summary, selection context
    results: List[RetrievalResult]
    mode: str                # "official_only" | "curated" | "open"
    version_scope_used: str
    total_candidates: int
```

---

## API Contract

### Service Endpoints

```
GET /api/v1/sources
  Query params: type, tier, enabled
  Response: { "sources": [Source] }

POST /api/v1/sources
  Request: Source (without source_id, chunk_count, last_indexed)
  Response: Source

PUT /api/v1/sources/{source_id}
  Request: Partial<Source>
  Response: Source

DELETE /api/v1/sources/{source_id}
  Response: { "deleted": bool }

POST /api/v1/sources/{source_id}/index
  Request: { "force": bool }
  Response: { "chunks_indexed": int, "duration_seconds": float }

POST /api/v1/retrieval/query
  Request: {
    "query": str,
    "context": {
      "prim_paths": [str],
      "prim_types": [str],
      "error_messages": [str],
      "fingerprint_summary": dict
    },
    "mode": str,             # "official_only" | "curated" | "open"
    "top_k": int,            # default 10
    "version_scope": str     # override, or "auto" to use fingerprint
  }
  Response: RetrievalResponse

POST /api/v1/retrieval/feedback
  Request: {
    "chunk_ids": [str],
    "outcome": str,          # "helpful" | "unhelpful" | "led_to_failure"
    "patch_plan_id": str | null
  }
  Response: { "updated": int }

GET /api/v1/retrieval/stats
  Response: {
    "total_chunks": int,
    "chunks_by_source": dict,
    "chunks_by_tier": dict,
    "last_full_index": datetime
  }
```

---

## File Structure

```
service/
└── isaac_assist_service/
    └── retrieval/
        ├── __init__.py
        ├── source_registry.py      # Source CRUD and configuration
        ├── indexer.py              # Document fetching, chunking, embedding
        ├── chunker.py              # Semantic chunking logic
        ├── search.py               # Hybrid search (BM25 + vector)
        ├── ranker.py               # Trust-scored ranking pipeline
        ├── version_matcher.py      # Version-scope resolution
        ├── feedback.py             # Negative memory / feedback integration
        ├── sources_default.yaml    # Default source registry
        ├── storage/
        │   ├── __init__.py
        │   ├── fts_store.py        # SQLite FTS5 keyword index
        │   └── vector_store.py     # Vector store (ChromaDB / LanceDB)
        └── routes.py               # FastAPI route handlers
```

---

## Implementation Notes

- **Chunking:** Use a recursive text splitter that respects heading boundaries (Markdown `#` levels). Each chunk should retain its heading hierarchy as `section_path`.
- **Embeddings:** Use a local embedding model (e.g., `all-MiniLM-L6-v2` via `sentence-transformers`) for privacy and offline support. Keep the model small (<100MB).
- **Keyword search:** SQLite FTS5 is sufficient for MVP; it's fast, zero-dependency, and supports BM25 ranking natively.
- **GitHub indexing:** For repos, index README, CHANGELOG, and selected issue/discussion threads. Do not index raw source code (too noisy); index docstrings and module-level comments selectively.
- **Rate limiting:** When fetching external documentation, respect rate limits and cache aggressively. Use `If-Modified-Since` headers to avoid re-downloading unchanged content.
- **Offline mode:** All retrieval must work against the local index. If the index is empty and no network is available, return an empty result set with a clear explanation, not an error.
- **Version-scoped doc URLs:** NVIDIA documentation uses versioned URL paths (e.g., `/4.5.0/`, `/6.0.0/`). Map the detected version to the correct URL prefix.

---

## Acceptance Criteria

- [ ] Default source registry includes all eight listed sources.
- [ ] Indexing completes for at least Isaac Sim and Isaac Lab docs.
- [ ] Retrieval returns version-scoped results matching the current fingerprint.
- [ ] Trust scores are computed and visible in results.
- [ ] Official-only mode excludes tier 2 and 3 sources.
- [ ] Source viewer in the UI shows chunk content, source name, trust tier, and URL.
- [ ] Feedback (helpful/unhelpful) persists and affects future ranking.
- [ ] Retrieval works offline against the local index.
