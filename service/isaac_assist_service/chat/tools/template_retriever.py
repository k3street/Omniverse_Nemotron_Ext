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
    """Return the ChromaDB collection, creating and indexing it on first call.

    Returns None if chromadb is not installed (retrieval silently disabled).
    """
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
    """Embed templates with goal + thoughts + tools_used for retrieval.

    Calibration history (2026-05-08):
        Original embed: `goal + tools_used` only
            VR-19 prompt → CP-01 ranked above CP-02 (wrong; gap 0.006)
            CP-01 and CP-02 have very similar tools_used → goal alone wasn't
            enough signal to discriminate "single-station pick-place" from
            "multi-station assembly line"
        Adding `thoughts`:
            CP-01 thoughts focus on single-robot mechanics (orientation,
            settling, friction)
            CP-02 thoughts focus on MULTI-ROBOT specifics ("Two robots
            coexist...", "shared cube in source_paths", "explicit drop_target",
            "two-robot orientation mirror")
            These are highly discriminating tokens that match VR-19's "two
            Franka robots" + "3-station" phrasing.
    """
    docs, ids, metas = [], [], []
    for tf in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            t = json.loads(tf.read_text())
        except Exception as e:
            logger.warning(f"[TemplateRetriever] Skipping bad template {tf.name}: {e}")
            continue
        tid = t.get("task_id", tf.stem)
        goal = t.get("goal", "")
        thoughts = t.get("thoughts", "")
        tools = " ".join(t.get("tools_used", []))
        # Order matters for embedding emphasis: goal first (highest weight
        # for the user's prompt match), thoughts second (discriminating
        # task-specific tokens), tools last (low-signal vocabulary).
        doc = f"{goal}\n\n{thoughts}\n\n{tools}".strip()
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
    """Load a template dict by task_id, using the in-memory cache first.

    Returns None if the template file does not exist or fails to parse.
    """
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


def retrieve_templates_with_scores(query: str, top_k: int = 3) -> List[Dict]:
    """Like retrieve_templates but each entry includes ChromaDB distance +
    a normalized similarity score in [0, 1] (1 = perfect match).

    Returns list of dicts: [{template, task_id, distance, similarity}, ...]

    Used by hard-instantiate path to gate on canonical-match confidence.
    """
    col = _get_collection()
    if col is None:
        return []
    try:
        res = col.query(query_texts=[query], n_results=top_k)
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[1.0] * len(metas)])[0]
        out: List[Dict] = []
        for i, m in enumerate(metas):
            tid = m.get("task_id")
            if not tid:
                continue
            t = _load_template(tid)
            if not t:
                continue
            d = float(dists[i]) if i < len(dists) else 1.0
            # Normalize: ChromaDB default L2 distances on sentence-transformers
            # embeddings are typically in [0, 2]. Map empirically: d=0 → sim=1,
            # d=0.5 → sim≈0.75 (strong), d=1.0 → sim≈0.5 (medium), d>=1.5 → sim=0.
            similarity = max(0.0, min(1.0, 1.0 - d / 1.5))
            out.append({
                "template": t,
                "task_id": tid,
                "distance": d,
                "similarity": similarity,
            })
        logger.info(
            f"[TemplateRetriever] '{query[:60]}' → "
            + ", ".join(f"{x['task_id']}({x['similarity']:.2f})" for x in out)
        )
        return out
    except Exception as e:
        logger.warning(f"[TemplateRetriever] Scored retrieval failed: {e}")
        return []


def rebuild_index() -> None:
    """Drop and fully rebuild the ChromaDB template collection from disk.

    Clears the in-memory cache so subsequent calls re-embed all templates.
    No-op if chromadb is unavailable.
    """
    global _collection, _client, _template_cache
    col = _get_collection()
    if col is None:
        return
    _client.delete_collection(_COLLECTION_NAME)
    _collection = _client.create_collection(_COLLECTION_NAME)
    _template_cache = {}
    _build_index()


# ---------------------------------------------------------------------------
# Structural-filter-first retrieval — multimodal-foundation extension
# ---------------------------------------------------------------------------
# Spec §8.1: retrieval becomes structurally-gated and similarity-tiebroken.
# Stage 1 — hard structural filter on intent (pattern_hint match,
# structural_features compatible, counts within tolerance). Stage 2 —
# embedding similarity over canonical structural fingerprint. Stage 3 —
# tier classification (existing thresholds).
#
# Block 1A.1 scope: this is an EXTENSION. The existing retrieve_templates /
# retrieve_templates_with_scores paths are unchanged — they remain the
# fallback for legacy templates that don't have an `intent` field.
# Templates gain `intent` fields when Block 1B's role-based refactor
# lands; until then, this function gracefully degrades by skipping
# Stage 1 filtering when no template declares intent.

def canonical_structural_fingerprint(intent: Dict) -> str:
    """Produce a deterministic canonical serialization of an Intent dict for
    embedding similarity. Spec §8.2.

    The fingerprint is "facts about the spec," not English — sorted +
    normalized so semantically-equivalent intents always produce the same
    string. The embedding model does similarity over facts, never over
    natural-language synthesis.

    Accepts either a Pydantic Intent.model_dump() dict or a hand-built dict
    with the same shape; tolerant of missing fields (defaults to no-op).
    """
    pattern_hint = intent.get("pattern_hint", "")
    counts = intent.get("counts", {}) or {}
    features = intent.get("structural_features", {}) or {}
    tags = sorted(intent.get("structural_tags", []) or [])

    # Counts: only emit non-zero entries to keep fingerprints stable across
    # additive count-field bumps.
    count_parts = [
        f"{k}:{v}" for k, v in sorted(counts.items()) if v
    ]

    # Features: emit booleans that are True + non-null numerics. Avoids
    # noise from default-False fields drowning out signal.
    feature_parts = []
    for k in sorted(features.keys()):
        v = features[k]
        if isinstance(v, bool):
            if v:
                feature_parts.append(f"{k}=true")
        elif v is None:
            continue
        elif isinstance(v, (int, float)):
            feature_parts.append(f"{k}={v}")
        elif isinstance(v, (list, tuple)):
            if v:
                feature_parts.append(f"{k}=[{','.join(str(x) for x in v)}]")
        elif isinstance(v, str):
            if v:
                feature_parts.append(f"{k}={v}")

    parts = [f"pattern_hint={pattern_hint}"]
    if count_parts:
        parts.append("counts=" + ",".join(count_parts))
    if feature_parts:
        parts.append("features=" + ",".join(feature_parts))
    if tags:
        parts.append("tags=" + ",".join(tags))
    return "; ".join(parts)


def _features_compatible(
    spec_features: Dict, template_features: Dict,
    strict: bool = False,
) -> bool:
    """Stage-1 filter helper: do template's structural_features satisfy the
    spec's requirements?

    Compatibility rules (per spec §8.1, conservative semantics):
    - Boolean flags: spec requires X=True ⇒ template must have X=True. Spec
      doesn't require X (X=False or absent) ⇒ template can have either.
    - Numeric features: if spec sets a value, template must match. If spec
      leaves null, template can have any value.
    - Strict mode: also requires template-set features to be matched by spec.
      Default off — we want broad-to-narrow matches.
    """
    for key, spec_v in spec_features.items():
        if isinstance(spec_v, bool):
            if spec_v and not template_features.get(key, False):
                return False
        elif spec_v is None:
            continue  # spec doesn't constrain this field
        else:
            template_v = template_features.get(key)
            if template_v is None:
                # Template doesn't declare; can't violate
                continue
            if template_v != spec_v:
                return False
    return True


def _counts_compatible(
    spec_counts: Dict, template_counts: Dict, tolerance: int = 0,
) -> bool:
    """Stage-1 filter helper: do template's counts match the spec's within
    tolerance?

    Default tolerance=0 means exact-match. Higher tolerance accepts
    near-matches — e.g., "robots=1, conveyors=1, bins=1" matches a template
    with "robots=1, conveyors=2, bins=1" if tolerance >= 1.
    """
    for key in {"robots", "conveyors", "bins", "cubes", "sensors", "humans"}:
        s = spec_counts.get(key, 0)
        t = template_counts.get(key, 0)
        if abs(s - t) > tolerance:
            return False
    return True


def filter_templates_by_intent(
    spec_intent: Dict,
    count_tolerance: int = 0,
) -> List[Dict]:
    """Stage 1 — hard structural filter.

    Returns the list of templates whose `intent` field is compatible with
    the spec's intent (pattern_hint match + features compatible + counts
    within tolerance). Templates without `intent` field are EXCLUDED from
    structural-filter results — they participate only via the legacy
    embedding-only retrieval path.

    Args:
        spec_intent: dict shape matching multimodal.types.Intent.model_dump()
        count_tolerance: relax exact count-match by ±N per entity class
    """
    # Lazily ensure the index/cache is built
    _get_collection()

    spec_pattern = spec_intent.get("pattern_hint")
    spec_features = spec_intent.get("structural_features") or {}
    spec_counts = spec_intent.get("counts") or {}

    candidates: List[Dict] = []
    for tid, template in _template_cache.items():
        t_intent = template.get("intent")
        if not t_intent:
            continue  # legacy template — handled by fallback path
        if t_intent.get("pattern_hint") != spec_pattern:
            continue
        if not _features_compatible(
            spec_features, t_intent.get("structural_features") or {}
        ):
            continue
        if not _counts_compatible(
            spec_counts, t_intent.get("counts") or {},
            tolerance=count_tolerance,
        ):
            continue
        candidates.append(template)
    return candidates


def retrieve_with_intent_filter(
    spec_intent: Dict,
    top_k: int = 3,
    count_tolerance: int = 0,
    fallback_to_embedding_only: bool = True,
) -> List[Dict]:
    """Structural-filter-first retrieval per spec §8.1.

    Stage 1: hard structural filter via `filter_templates_by_intent`.
    Stage 2: embedding similarity over canonical structural fingerprint
        among Stage-1 candidates only.
    Stage 3: returns same shape as `retrieve_templates_with_scores`
        ({template, task_id, distance, similarity}) for downstream
        tier-classification compatibility.

    If Stage 1 returns no candidates (e.g., no templates have intent fields
    yet — Block 1B not landed), and `fallback_to_embedding_only=True`
    (default), the function falls back to embedding-similarity over the
    fingerprint without structural filtering. This makes the function
    useful immediately and progressively stricter as templates add intent.

    Returns: list of {template, task_id, distance, similarity} dicts,
    most-similar first.
    """
    candidates = filter_templates_by_intent(spec_intent, count_tolerance)

    fingerprint = canonical_structural_fingerprint(spec_intent)

    if not candidates:
        if not fallback_to_embedding_only:
            return []
        # Fallback: embedding-only over the fingerprint, against the full
        # template set (legacy mode, pre-Block-1B). Same return shape.
        logger.info(
            "[TemplateRetriever] structural-filter found no candidates; "
            "falling back to embedding-only retrieval over full set"
        )
        return retrieve_templates_with_scores(fingerprint, top_k=top_k)

    # Stage 2: embed similarity over candidates only.
    # We restrict the ChromaDB query to the candidate IDs via the `where`
    # filter (chromadb supports `$in`). The query text is the structural
    # fingerprint — embedding similarity over facts about the spec, not
    # natural-language prose.
    col = _get_collection()
    if col is None:
        # Without ChromaDB, return all candidates in arbitrary order
        return [
            {"template": t, "task_id": t.get("task_id", "?"),
             "distance": 0.0, "similarity": 1.0}
            for t in candidates[:top_k]
        ]

    candidate_ids = [t.get("task_id") for t in candidates if t.get("task_id")]
    try:
        res = col.query(
            query_texts=[fingerprint],
            n_results=min(top_k, len(candidate_ids)),
            where={"task_id": {"$in": candidate_ids}} if candidate_ids else None,
        )
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[1.0] * len(metas)])[0]
        out: List[Dict] = []
        for i, m in enumerate(metas):
            tid = m.get("task_id")
            if not tid:
                continue
            t = _load_template(tid)
            if not t:
                continue
            d = float(dists[i]) if i < len(dists) else 1.0
            similarity = max(0.0, min(1.0, 1.0 - d / 1.5))
            out.append({
                "template": t,
                "task_id": tid,
                "distance": d,
                "similarity": similarity,
            })
        logger.info(
            f"[TemplateRetriever] structural-filter retrieved "
            f"{len(out)}/{len(candidates)} of {len(candidate_ids)} candidates: "
            + ", ".join(f"{x['task_id']}({x['similarity']:.2f})" for x in out)
        )
        return out
    except Exception as e:
        logger.warning(f"[TemplateRetriever] Stage-2 query failed: {e}")
        # Conservative fallback: return all candidates in arbitrary order
        return [
            {"template": t, "task_id": t.get("task_id", "?"),
             "distance": 0.0, "similarity": 1.0}
            for t in candidates[:top_k]
        ]
