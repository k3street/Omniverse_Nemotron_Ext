"""
context_retriever.py
--------------------
Version-aware RAG retriever that feeds relevant code patterns and API docs
into the chat orchestrator's system prompt.

Flow:
  1. Detect Isaac Sim version from fingerprint
  2. Extract keywords from user message
  3. Search FTS store with version filter
  4. Return formatted context for LLM injection
"""
from __future__ import annotations
import logging
import os
import json
from typing import List, Dict, Any, Optional

from .storage.fts_store import FTSStore

logger = logging.getLogger(__name__)

# Singleton store instance
_store: Optional[FTSStore] = None


def _get_store() -> FTSStore:
    global _store
    if _store is None:
        _store = FTSStore()
    return _store


def detect_isaac_version() -> str:
    """Detect Isaac Sim version from environment."""
    isaac_path = os.environ.get("ISAAC_SIM_PATH", "")
    if "6.0" in isaac_path:
        return "6.0.0"
    return "5.1.0"


def retrieve_context(user_message: str, version: str = None, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search the FTS index for chunks relevant to the user's message,
    filtered by Isaac Sim version.
    """
    if version is None:
        version = detect_isaac_version()

    store = _get_store()
    results = store.search(user_message, limit=limit, version_scope=version)
    return results


def format_retrieved_context(results: List[Dict[str, Any]]) -> str:
    """Format FTS results into a string for LLM system prompt injection."""
    if not results:
        return ""

    lines = ["--- RETRIEVED KNOWLEDGE (version-specific) ---"]
    for i, r in enumerate(results, 1):
        section = r.get("section_path", "")
        content = r.get("content", "")[:800]
        version = r.get("version_scope", "")
        source = r.get("source_id", "")
        lines.append(f"\n[{i}] {section} (v{version}, source: {source})")
        lines.append(content)
    return "\n".join(lines)


# ── Code pattern store (in-memory, loaded from JSONL) ─────────────────────

_CODE_PATTERNS: Dict[str, List[Dict]] = {}  # version -> list of patterns

PATTERNS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "workspace", "knowledge"
)


def _load_patterns(version: str) -> List[Dict]:
    """Load code patterns from versioned JSONL files."""
    if version in _CODE_PATTERNS:
        return _CODE_PATTERNS[version]

    patterns = []
    pattern_file = os.path.join(PATTERNS_DIR, f"code_patterns_{version}.jsonl")
    if os.path.exists(pattern_file):
        with open(pattern_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        patterns.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    _CODE_PATTERNS[version] = patterns
    logger.info(f"[RAG] Loaded {len(patterns)} code patterns for v{version}")
    return patterns


def find_matching_patterns(user_message: str, version: str = None, limit: int = 3) -> List[Dict]:
    """
    Simple keyword matching against code patterns.
    Returns patterns whose 'keywords' overlap with the user message.
    """
    if version is None:
        version = detect_isaac_version()

    patterns = _load_patterns(version)
    if not patterns:
        return []

    msg_lower = user_message.lower()
    scored = []
    for p in patterns:
        keywords = p.get("keywords", [])
        score = sum(1 for kw in keywords if kw.lower() in msg_lower)
        if score > 0:
            scored.append((score, p))

    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:limit]]


def format_code_patterns(patterns: List[Dict]) -> str:
    """Format matched code patterns for LLM injection."""
    if not patterns:
        return ""

    lines = ["--- KNOWN WORKING CODE PATTERNS ---"]
    for i, p in enumerate(patterns, 1):
        title = p.get("title", "Pattern")
        code = p.get("code", "")
        note = p.get("note", "")
        lines.append(f"\n[{i}] {title}")
        if note:
            lines.append(f"Note: {note}")
        lines.append(f"```python\n{code}\n```")
    return "\n".join(lines)
