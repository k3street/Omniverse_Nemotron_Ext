"""Deterministic keyword index over api_deprecations.jsonl.

Purpose: return EXACT cite-facts (API names, tool names, deprecation
warnings, caveats) for cite-hungry agent queries. This is an INDEX,
not RAG — no embeddings, no LLM-side synthesis in the retrieval path.
The agent reads the returned JSON and cites names verbatim.

Scale target: 50-200 rows. Loaded once at module import, kept in
memory. Index structure is a dict[keyword → list[row_id]] built from
each row's `keywords` field (lowercased).

Scoring: a query matches a row when ANY of the row's keywords appears
as a substring in the lowercased query. Ties broken by the number of
matched keywords (more matches = higher score). Deterministic, no
randomness, no cutoff threshold.

Usage (from tool_executor):
    from ..knowledge.deprecations_index import lookup
    result = lookup("deterministic replay for CI")
    # result = [{row}, ...] sorted by match-count desc, top-3

Intentionally minimal — ~80 lines. If the corpus grows past 200 rows
and scoring needs tf-idf weighting, revisit.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent / "deprecations.jsonl"

# Lazy-loaded singletons
_ROWS: List[Dict[str, Any]] | None = None
_KEYWORD_TO_ROW_IDS: Dict[str, List[str]] | None = None
_ROW_BY_ID: Dict[str, Dict[str, Any]] | None = None


def _load() -> None:
    """Load + index the JSONL corpus. Safe to call multiple times."""
    global _ROWS, _KEYWORD_TO_ROW_IDS, _ROW_BY_ID
    if _ROWS is not None:
        return

    rows: List[Dict[str, Any]] = []
    try:
        with _DATA_PATH.open("r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[deprecations_index] skip malformed line {line_no}: {e}"
                    )
                    continue
                if not isinstance(row, dict) or "id" not in row:
                    logger.warning(
                        f"[deprecations_index] skip line {line_no}: missing 'id'"
                    )
                    continue
                rows.append(row)
    except FileNotFoundError:
        logger.warning(
            f"[deprecations_index] corpus not found at {_DATA_PATH} — "
            "lookup will return no results"
        )
        rows = []

    kw_index: Dict[str, List[str]] = {}
    row_by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        row_id = row["id"]
        if row_id in row_by_id:
            logger.warning(f"[deprecations_index] duplicate id '{row_id}' — keeping first")
            continue
        row_by_id[row_id] = row
        for kw in row.get("keywords", []) or []:
            if not isinstance(kw, str):
                continue
            kw_index.setdefault(kw.lower().strip(), []).append(row_id)

    _ROWS = rows
    _KEYWORD_TO_ROW_IDS = kw_index
    _ROW_BY_ID = row_by_id
    logger.info(
        f"[deprecations_index] loaded {len(rows)} rows, "
        f"{len(kw_index)} unique keywords"
    )


def lookup(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Return up to `top_k` rows scored by IDF-weighted keyword overlap.

    A row's score is the sum of inverse-document-frequency weights for the
    keywords (in that row) that appear in the lowercased query. This makes
    rare/specific keywords contribute more than common ones — without IDF,
    a cite with many generic keywords (e.g. "import", "set") drowned out
    cites with one highly-specific keyword (e.g. "URDFParseAndImportFile").

    Default top_k bumped from 3 → 5 (2026-05-05): empirically the cite
    budget is ~2K chars, even at top_k=5 we stay under 3K which leaves
    room for templates and tool schemas.
    """
    import math
    _load()
    assert _KEYWORD_TO_ROW_IDS is not None and _ROW_BY_ID is not None
    n_rows = max(len(_ROW_BY_ID), 1)

    q = (query or "").lower()
    if not q:
        return []

    scores: Dict[str, float] = {}
    for kw, row_ids in _KEYWORD_TO_ROW_IDS.items():
        if not kw or kw not in q:
            continue
        df = max(len(row_ids), 1)
        idf = math.log(1.0 + n_rows / df)
        for rid in row_ids:
            scores[rid] = scores.get(rid, 0.0) + idf

    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [_ROW_BY_ID[rid] for rid, _ in ranked[:top_k]]


def all_rows() -> List[Dict[str, Any]]:
    """Return the full corpus — useful for CI lint / tests."""
    _load()
    return list(_ROWS or [])
