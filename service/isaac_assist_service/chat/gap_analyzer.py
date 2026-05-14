"""
gap_analyzer.py
---------------
Match the expected_tool names from a StructuredSpec against the actually-
registered tool catalog. Returns a three-tier classification:
  - matched:   tool name is registered verbatim
  - partial:   no exact match but a close name exists (embedding similarity)
  - missing:   no plausible match — agent must improvise OR report honestly

Why embedding instead of Levenshtein
------------------------------------
v1 (b93bcca, reverted) used Levenshtein <= 3 for partial matching. That fixed
typos but missed semantic synonyms like "proximity_trigger_sensor" vs the
registered "add_proximity_sensor" — too far on edit distance, identical in
intent. The brainstorm doc spec_first_pattern_cross_project.md flagged this
explicitly as the v1 fix in its "Three reusable parts" section.

v2 uses the existing isaac_assist_tools ChromaDB collection (already used by
tool_retriever for prompt-based selection). The same MiniLM-384 embeddings
that match user messages to tools also match expected-tool names to tools.
No new index, no new dependency.
"""
from __future__ import annotations

import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class GapReport(TypedDict):
    matched: list[str]                 # exact-match tool names
    partial: dict[str, str]            # expected_name → closest registered
    missing: list[str]                 # no plausible match
    notes: str                         # one-line summary, telemetry


# Non-tool expected-action names that should not be checked against the
# tool catalog — these are reasoning/reply sentinels in spec_generator output.
_NONTOOL_SENTINELS = {
    "reply", "reasoning", "answer", "explain", "summarize", "summary",
    "respond", "report",
}


def _exact_lookup(name: str, registered: set[str]) -> bool:
    """Case-sensitive then case-insensitive exact match."""
    if name in registered:
        return True
    lower = name.lower()
    for reg in registered:
        if reg.lower() == lower:
            return True
    return False


def _semantic_closest(name: str, top_k: int = 1) -> str | None:
    """Return the most semantically similar registered tool name, or None."""
    try:
        from .tools.tool_retriever import retrieve_tools
        # retrieve_tools accepts a free-text query; tool names work fine since
        # the index was built from name + description.
        # Use a phrasing that hints "this is a tool to do X" so the embedding
        # match favors action-tools over named entities.
        query = f"tool that {name.replace('_', ' ')}"
        results = retrieve_tools(query, top_k=top_k)
        return results[0] if results else None
    except Exception as e:
        logger.debug(f"[GapAnalyzer] semantic lookup failed for {name!r}: {e}")
        return None


def analyze(spec_steps: list[dict], registered_tools: set[str]) -> GapReport:
    """
    Classify each spec step's expected_tool as matched/partial/missing.
    `spec_steps` items must have an 'expected_tool' field (StructuredSpec
    shape). Sentinel actions (reply, reasoning) are excluded from analysis.
    """
    matched: list[str] = []
    partial: dict[str, str] = {}
    missing: list[str] = []

    seen: set[str] = set()
    for step in spec_steps or []:
        tool = (step.get("expected_tool") or "").strip()
        if not tool or tool in seen:
            continue
        seen.add(tool)
        if tool.lower() in _NONTOOL_SENTINELS:
            continue

        if _exact_lookup(tool, registered_tools):
            matched.append(tool)
            continue

        closest = _semantic_closest(tool)
        if closest and closest != tool:
            # Only treat as partial when the closest match isn't trivially
            # the same string. Also gate by whether the closest is in the
            # registered set — _semantic_closest returns names from the index.
            if closest in registered_tools:
                partial[tool] = closest
                continue

        missing.append(tool)

    notes = (
        f"matched={len(matched)} partial={len(partial)} missing={len(missing)}"
    )
    logger.info(f"[GapAnalyzer] {notes}")
    return {
        "matched": matched,
        "partial": partial,
        "missing": missing,
        "notes": notes,
    }


def get_registered_tools() -> set[str]:
    """Read the set of tool names currently exposed to the LLM."""
    try:
        from .tools.tool_executor import CODE_GEN_HANDLERS, DATA_HANDLERS
        return set(CODE_GEN_HANDLERS.keys()) | set(DATA_HANDLERS.keys())
    except Exception as e:
        logger.warning(f"[GapAnalyzer] failed to read registries: {e}")
        return set()
