from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List, Optional
from .models import RetrievalQuery
from .storage.fts_store import FTSStore
from .indexer import DocumentIndexer
from .source_registry import SourceRegistry
import json
from pathlib import Path

router = APIRouter()
store = FTSStore()
registry = SourceRegistry()
indexer = DocumentIndexer()

# ── Product spec database ────────────────────────────────────────────────────
_SPECS_PATH = Path(__file__).resolve().parents[2] / "workspace" / "knowledge" / "sensor_specs.jsonl"
_specs_cache: Optional[List[Dict]] = None

def _load_specs() -> List[Dict]:
    global _specs_cache
    if _specs_cache is not None:
        return _specs_cache
    specs = []
    if _SPECS_PATH.exists():
        for line in _SPECS_PATH.read_text().splitlines():
            line = line.strip()
            if line:
                specs.append(json.loads(line))
    _specs_cache = specs
    return specs


@router.get("/specs")
def list_product_specs(
    sensor_type: Optional[str] = Query(None, description="Filter by type: camera, lidar, imu, gripper, force_torque_sensor"),
    manufacturer: Optional[str] = Query(None),
):
    """List all product specs, optionally filtered by type or manufacturer."""
    specs = _load_specs()
    if sensor_type:
        specs = [s for s in specs if s.get("type") == sensor_type]
    if manufacturer:
        specs = [s for s in specs if manufacturer.lower() in s.get("manufacturer", "").lower()]
    return {"specs": specs, "count": len(specs)}


@router.get("/specs/lookup")
def lookup_product_spec(product_name: str = Query(..., description="Product name to search for")):
    """Fuzzy-match a product name against the sensor specs database."""
    query = product_name.lower()
    specs = _load_specs()

    # Exact match
    for s in specs:
        if s["product"].lower() == query:
            return {"found": True, "spec": s}

    # Partial match
    matches = [s for s in specs if query in s["product"].lower()]
    if matches:
        return {"found": True, "spec": matches[0],
                "alternatives": [m["product"] for m in matches[1:4]]}

    # Token match
    tokens = query.split()
    scored = []
    for s in specs:
        name_lower = s["product"].lower()
        hits = sum(1 for t in tokens if t in name_lower)
        if hits > 0:
            scored.append((hits, s))
    scored.sort(key=lambda x: -x[0])
    if scored:
        return {"found": True, "spec": scored[0][1],
                "alternatives": [s["product"] for _, s in scored[1:4]]}

    return {"found": False, "message": f"No specs found for '{product_name}'",
            "available_types": list(set(s.get("type") for s in _load_specs()))}

@router.get("/sources")
def list_sources():
    return {"sources": registry.get_sources()}

@router.post("/query")
def execute_query(req: RetrievalQuery):
    """
    Called by ChatOrchestrator to fetch factual USD pipeline examples.
    """
    try:
        results = store.search(req.query, limit=req.top_k)
        
        # Format matching the RetrievalResponse spec loosely
        mapped = []
        for r in results:
            mapped.append({
                "chunk": {
                    "source_id": r.get("source_id", "unknown"),
                    "content": r.get("content", ""),
                    "url": r.get("url", ""),
                    "version_scope": r.get("version_scope", "")
                },
                "relevance_score": r.get("rank", 0.0), # BM25 Rank
                "trust_score": float(r.get("trust_tier", 1)),
                "version_match": "exact" if req.version_scope in r.get("version_scope", "") else "mismatch"
            })
            
        return {
            "query": req.query,
            "results": mapped,
            "mode": req.mode
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sources/{source_id}/index_mock")
def index_mock_doc(source_id: str):
    """
    MVP Endpoint: Inject a fake document simulating an Omniverse scraping run.
    """
    mock_doc = """
    # Creating a USD Cube using PXR
    
    To generate a cube in Omniverse using Python:
    ```python
    from pxr import Usd, UsdGeom
    stage = omni.usd.get_context().get_stage()
    cube = UsdGeom.Cube.Define(stage, '/World/MyCube')
    cube.GetSizeAttr().Set(10.0)
    ```
    Ensure you test this carefully.
    
    # Binding Physics
    
    To bind a RigidBody:
    ```python
    from pxr import UsdPhysics
    UsdPhysics.RigidBodyAPI.Apply(cube.GetPrim())
    ```
    """
    
    count = indexer.index_document(
        source_id=source_id,
        raw_text=mock_doc,
        url="https://docs.isaacsim.omniverse.nvidia.com/internal_mock",
        version="6.0.0"
    )
    return {"chunks_indexed": count}


@router.get("/sources/{source_id}/stats")
def source_stats(source_id: str):
    """Return the number of FTS chunks indexed for a source."""
    count = store.count_chunks(source_id=source_id)
    return {"source_id": source_id, "chunk_count": count}


@router.post("/sources/{source_id}/ingest")
def ingest_source(source_id: str, reset: bool = False):
    """
    Trigger a real web crawl of the registered source and load it into FTS.
    Imports the scraper lazily to avoid network calls at app startup.
    """
    source_map = {s.source_id: s for s in registry.get_sources()}
    if source_id not in source_map:
        raise HTTPException(status_code=404, detail=f"Unknown source: {source_id}")

    try:
        from scripts.scrape_isaac_docs import SOURCES, crawl_source  # noqa: PLC0415
        cfg = next((s for s in SOURCES if s["source_id"] == source_id), None)
        if cfg is None:
            raise HTTPException(status_code=422,
                                detail=f"No crawler config for source '{source_id}'")

        pages, chunks = crawl_source(
            source=cfg,
            indexer=indexer,
            store=store,
            reset=reset,
        )
        return {"source_id": source_id, "pages_crawled": pages, "chunks_indexed": chunks}
    except ImportError as exc:
        raise HTTPException(status_code=500,
                            detail=f"Crawler not available: {exc}") from exc
