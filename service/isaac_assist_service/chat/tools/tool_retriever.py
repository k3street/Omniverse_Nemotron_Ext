"""
tool_retriever.py
-----------------
Semantic retrieval layer that narrows the tool set shown to the LLM.

Background: on feat/qa-runtime-bundle there are 346 tools. Even after the
category-based filter in context_distiller (346 -> ~27), the LLM still
over-calls or picks generic fallbacks (`run_usd_script`) instead of the
specialized tool the task needs.

This module builds a ChromaDB collection of tool-name + description +
example-usage embeddings, then exposes `retrieve_tools(query, top_k)` that
returns the most semantically relevant tool schemas for a user message.

The orchestrator calls this to ADD relevant tools to the category-filtered
set — it's additive, never removes essentials.

Uses ChromaDB's default embedding model (sentence-transformers all-MiniLM-L6-v2),
no external API required.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Dict, List, Optional
import json

logger = logging.getLogger(__name__)

_PERSIST_DIR = Path(__file__).resolve().parents[3] / "workspace" / "tool_index"
_COLLECTION_NAME = "isaac_assist_tools"
_client = None
_collection = None


def _get_collection():
    """Lazy-init ChromaDB client + collection. Builds index on first call."""
    global _client, _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
    except ImportError:
        logger.warning("[ToolRetriever] chromadb not installed; retrieval disabled")
        return None

    _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=str(_PERSIST_DIR))
    try:
        _collection = _client.get_collection(_COLLECTION_NAME)
        logger.info(f"[ToolRetriever] Loaded existing collection ({_collection.count()} tools)")
    except Exception:
        _collection = _client.create_collection(_COLLECTION_NAME)
        _build_index()
    return _collection


def _build_index() -> None:
    """Embed all tool descriptions and store in ChromaDB."""
    from .tool_schemas import ISAAC_SIM_TOOLS
    docs, ids, metas = [], [], []
    for t in ISAAC_SIM_TOOLS:
        fn = t.get("function", {})
        name = fn["name"]
        desc = fn.get("description", "") or ""
        params = fn.get("parameters", {}).get("properties", {})
        param_hint = " ".join(params.keys())
        doc = f"{name}\n{desc}\nparameters: {param_hint}".strip()
        docs.append(doc)
        ids.append(name)
        metas.append({"name": name})
    _collection.add(documents=docs, ids=ids, metadatas=metas)
    logger.info(f"[ToolRetriever] Built index with {len(docs)} tools")


def retrieve_tools(query: str, top_k: int = 15) -> List[str]:
    """Return top_k tool NAMES semantically matching the query."""
    col = _get_collection()
    if col is None:
        return []
    try:
        res = col.query(query_texts=[query], n_results=top_k)
        names = [m.get("name") for m in (res.get("metadatas") or [[]])[0] if m.get("name")]
        logger.info(f"[ToolRetriever] '{query[:60]}' → {names}")
        return names
    except Exception as e:
        logger.warning(f"[ToolRetriever] Retrieval failed: {e}")
        return []


def rebuild_index() -> None:
    """Force rebuild — call after tool_schemas.py changes."""
    global _collection, _client
    col = _get_collection()
    if col is None:
        return
    _client.delete_collection(_COLLECTION_NAME)
    _collection = _client.create_collection(_COLLECTION_NAME)
    _build_index()
