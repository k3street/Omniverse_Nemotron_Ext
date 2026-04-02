from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from .models import RetrievalQuery
from .storage.fts_store import FTSStore
from .indexer import DocumentIndexer
from .source_registry import SourceRegistry

router = APIRouter()
store = FTSStore()
registry = SourceRegistry()
indexer = DocumentIndexer()

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
