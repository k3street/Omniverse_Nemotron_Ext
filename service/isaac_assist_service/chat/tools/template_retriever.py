"""
template_retriever.py
----------------------
Runtime retrieval of workflow templates (generated offline from task specs).

Given a user message, find the top-K templates with the most similar `goal`.
Inject them as few-shot examples in the LLM context so the runtime model
follows proven tool chains instead of inventing one each turn.

Based on 2024-2026 research (CodeAct, Agent Workflow Memory, Structured
CodeAgent): hybrid JSON+pseudocode templates beat pure prose and pure JSON
on tool-chain accuracy by ~20pp.

Uses the same ChromaDB instance as `tool_retriever`, separate collection.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "workspace" / "templates"
_PERSIST_DIR = Path(__file__).resolve().parents[4] / "workspace" / "tool_index"
_COLLECTION_NAME = "isaac_assist_templates"

_client = None
_collection = None
_template_cache: Dict[str, Dict] = {}


def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
    except ImportError:
        logger.warning("[TemplateRetriever] chromadb not installed; retrieval disabled")
        return None
    _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=str(_PERSIST_DIR))
    try:
        _collection = _client.get_collection(_COLLECTION_NAME)
        if _collection.count() == 0:
            # Orphan-empty state: persist dir was deleted while collection
            # metadata survived → get_collection succeeds but returns 0 entries.
            # _build_index would otherwise never trigger. Rebuild defensively.
            logger.warning("[TemplateRetriever] Collection found but empty — rebuilding index")
            _build_index()
        logger.info(f"[TemplateRetriever] Loaded collection ({_collection.count()} templates)")
    except Exception:
        _collection = _client.create_collection(_COLLECTION_NAME)
        _build_index()
    return _collection


def _build_index() -> None:
    """Embed all templates' goal+tools text for retrieval."""
    docs, ids, metas = [], [], []
    for tf in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            t = json.loads(tf.read_text())
        except Exception as e:
            logger.warning(f"[TemplateRetriever] Skipping bad template {tf.name}: {e}")
            continue
        tid = t.get("task_id", tf.stem)
        goal = t.get("goal", "")
        tools = " ".join(t.get("tools_used", []))
        doc = f"{goal}\n{tools}".strip()
        if not doc:
            continue
        docs.append(doc)
        ids.append(tid)
        metas.append({"task_id": tid})
        _template_cache[tid] = t
    if docs:
        _collection.add(documents=docs, ids=ids, metadatas=metas)
    logger.info(f"[TemplateRetriever] Built index with {len(docs)} templates")


def _load_template(task_id: str) -> Optional[Dict]:
    if task_id in _template_cache:
        return _template_cache[task_id]
    p = _TEMPLATES_DIR / f"{task_id}.json"
    if not p.exists():
        return None
    try:
        t = json.loads(p.read_text())
        _template_cache[task_id] = t
        return t
    except Exception as e:
        logger.warning(f"[TemplateRetriever] Bad template {task_id}: {e}")
        return None


def retrieve_templates(query: str, top_k: int = 3, min_score: float = 0.0) -> List[Dict]:
    """Return top_k templates by semantic similarity to `query`.

    Each template is a dict with keys: task_id, goal, tools_used, thoughts,
    code, failure_modes.
    """
    col = _get_collection()
    if col is None:
        return []
    try:
        res = col.query(query_texts=[query], n_results=top_k)
        templates: List[Dict] = []
        for m in (res.get("metadatas") or [[]])[0]:
            tid = m.get("task_id")
            if not tid:
                continue
            t = _load_template(tid)
            if t:
                templates.append(t)
        logger.info(f"[TemplateRetriever] '{query[:60]}' → {[t.get('task_id') for t in templates]}")
        return templates
    except Exception as e:
        logger.warning(f"[TemplateRetriever] Retrieval failed: {e}")
        return []


def format_for_prompt(templates: List[Dict]) -> str:
    """Format retrieved templates as a concise few-shot block for the system prompt."""
    if not templates:
        return ""
    lines = ["# Reference workflows for similar tasks", ""]
    for t in templates:
        lines.append(f"## {t.get('task_id', '?')}: {t.get('goal', '')}")
        if t.get("thoughts"):
            lines.append(f"**Approach**: {t['thoughts']}")
        lines.append("**Tools**: " + ", ".join(t.get("tools_used", [])))
        if t.get("code"):
            lines.append("**Pattern**:")
            lines.append("```python")
            lines.append(t["code"])
            lines.append("```")
        if t.get("failure_modes"):
            lines.append("**Watch out**: " + " | ".join(t["failure_modes"]))
        lines.append("")
    return "\n".join(lines)


def rebuild_index() -> None:
    global _collection, _client, _template_cache
    col = _get_collection()
    if col is None:
        return
    _client.delete_collection(_COLLECTION_NAME)
    _collection = _client.create_collection(_COLLECTION_NAME)
    _template_cache = {}
    _build_index()
