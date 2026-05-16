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
import os
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
        else:
            # Persistent-index load: ChromaDB has the embeddings, but
            # `_template_cache` is not populated (only `_build_index` does
            # that). Rehydrate from disk so `filter_templates_by_intent` and
            # any other cache-dependent paths work correctly.
            _rehydrate_cache()
        logger.info(f"[TemplateRetriever] Loaded collection ({_collection.count()} templates)")
    except Exception:
        _collection = _client.create_collection(_COLLECTION_NAME)
        _build_index()
    return _collection


def _rehydrate_cache() -> None:
    """Populate `_template_cache` from disk without touching ChromaDB.

    Called when an existing persistent index is loaded — `_build_index` is
    not invoked in that path, so `_template_cache` would otherwise remain
    empty and `filter_templates_by_intent` would silently return nothing.

    Reads every ``workspace/templates/*.json`` file and inserts it into
    `_template_cache` keyed by ``task_id`` (falling back to ``stem``).
    Files that fail to parse are skipped with a warning (mirrors
    `_build_index` behaviour).

    Batched-sleep mitigation (2026-05-16): loads in batches of 32 with a
    brief sleep between, giving the host runtime's GC a chance to run
    between native-extension-allocation bursts. Total overhead ~10ms for
    321 templates. Specifically mitigates Bun 1.3.14 JSC SlotVisitor::drain
    crash pattern when Claude Code's bundled Bun runs pytest on this path.
    """
    import time
    loaded = 0
    for tf in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            t = json.loads(tf.read_text())
        except Exception as e:
            logger.warning(f"[TemplateRetriever] Skipping bad template {tf.name}: {e}")
            continue
        tid = t.get("task_id", tf.stem)
        _template_cache[tid] = t
        loaded += 1
        # Yield to runtime scheduler / GC every 32 templates to avoid
        # allocation bursts. See .claude-session-guardrails.md.
        if loaded % 32 == 0:
            time.sleep(0.001)
    logger.info(f"[TemplateRetriever] Rehydrated cache with {loaded} templates")


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
    import time
    docs, ids, metas = [], [], []
    parsed = 0
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
        parsed += 1
        # Batched-sleep mitigation (2026-05-16): see _rehydrate_cache docstring.
        # Yield every 32 to avoid native-extension allocation bursts.
        if parsed % 32 == 0:
            time.sleep(0.001)
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


# ---------------------------------------------------------------------------
# Motion-controller filter helpers (Round 11, 2026-05-15)
# ---------------------------------------------------------------------------
# ENV: RETRIEVAL_MC_FILTER=on enables the filter; default off.
# Post-filter applied after similarity retrieval so ChromaDB index is unchanged.
# ---------------------------------------------------------------------------

def _mc_filter_enabled() -> bool:
    """Return True when the motion-controller filter is activated via env var."""
    return os.environ.get("RETRIEVAL_MC_FILTER", "off").lower() in ("on", "true", "1", "yes")


def _parse_mc_base_name(controller_name: str) -> str:
    """Strip optional version suffix: 'curobo@1.8.2' → 'curobo'."""
    return controller_name.split("@", 1)[0].strip().lower()


def _apply_motion_controller_filter(
    entries: List[Dict],
    constraint: Dict,
) -> List[Dict]:
    """Post-filter a list of scored entries by motion_controller_constraint.

    Args:
        entries: list of {template, task_id, distance, similarity} dicts
        constraint: dict with optional keys:
            must_verified   — template.motion_controllers.verified must contain ALL (base-name match)
            must_not_failed — template.motion_controllers.failed must NOT contain ANY (base-name match)

    Templates with NO motion_controllers field are INCLUDED regardless (don't
    penalize unmigrated templates).
    """
    must_verified = [
        _parse_mc_base_name(c) for c in (constraint.get("must_verified") or [])
    ]
    must_not_failed = [
        _parse_mc_base_name(c) for c in (constraint.get("must_not_failed") or [])
    ]

    if not must_verified and not must_not_failed:
        return entries  # constraint is a no-op

    filtered: List[Dict] = []
    for entry in entries:
        t = entry.get("template", {})
        mc = t.get("motion_controllers")
        if mc is None:
            # No field → include (unmigrated template, benefit of the doubt)
            filtered.append(entry)
            continue

        # verified is a list; parse each entry's base name
        verified_bases = {
            _parse_mc_base_name(v) for v in (mc.get("verified") or [])
        }
        # failed is a dict keyed by controller name; keys are the names
        failed_bases = {
            _parse_mc_base_name(k) for k in (mc.get("failed") or {}).keys()
        }

        # Check must_verified: all required controllers must be in verified_bases
        if must_verified and not all(c in verified_bases for c in must_verified):
            continue

        # Check must_not_failed: none of the excluded controllers may be in failed_bases
        if must_not_failed and any(c in failed_bases for c in must_not_failed):
            continue

        filtered.append(entry)
    return filtered


def retrieve_templates_with_scores(
    query: str,
    top_k: int = 3,
    motion_controller_constraint: Optional[Dict] = None,
) -> List[Dict]:
    """Like retrieve_templates but each entry includes ChromaDB distance +
    a normalized similarity score in [0, 1] (1 = perfect match).

    Returns list of dicts: [{template, task_id, distance, similarity}, ...]

    Used by hard-instantiate path to gate on canonical-match confidence.

    Args:
        query: user message or structural fingerprint for similarity search
        top_k: number of candidates to retrieve from ChromaDB
        motion_controller_constraint: optional post-filter dict with keys:
            must_verified   — list of controller names (base-name matched);
                              template.motion_controllers.verified must include ALL
            must_not_failed — list of controller names; template must NOT have
                              any of these in motion_controllers.failed
            Only honored when env var RETRIEVAL_MC_FILTER=on.
            Templates without the motion_controllers field are always included.
            When None, behavior is byte-identical to the no-filter baseline.
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

        # Apply motion-controller post-filter when env gate is on
        if motion_controller_constraint is not None and _mc_filter_enabled():
            out = _apply_motion_controller_filter(out, motion_controller_constraint)

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
    - destination_kind="single_bin": treated as the schema default / "any" —
      the rule-based extractor always emits "single_bin" when it cannot infer
      destination type. Requiring exact match blocks templates with "fixture"
      or "n_bins_routed" that the extractor cannot detect from text alone.
      Fix (R15b): skip destination_kind comparison when spec value is the
      generic default "single_bin".
    - Strict mode: also requires template-set features to be matched by spec.
      Default off — we want broad-to-narrow matches.
    """
    # Fields that carry schema defaults and should not block template admission
    # when the spec emits only the default value (meaning "unset / unconstrained").
    _UNCONSTRAINED_DEFAULTS: Dict = {"destination_kind": "single_bin"}

    for key, spec_v in spec_features.items():
        if isinstance(spec_v, bool):
            if spec_v and not template_features.get(key, False):
                return False
        elif spec_v is None:
            continue  # spec doesn't constrain this field
        else:
            # Skip comparison when spec holds the unconstrained default for
            # this field — the extractor couldn't determine a real value.
            if _UNCONSTRAINED_DEFAULTS.get(key) == spec_v:
                continue
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


def _spec_is_null_signal(spec_intent: Dict) -> bool:
    """Return True when the intent dict carries no real extractor signal.

    The rule-based extractor in text_modality.py defaults to
    ``pattern_hint="pick_place"`` when NO keyword rule fires. That default
    spec (all booleans False, all counts 0, destination_kind="single_bin")
    is indistinguishable from a genuine minimal pick_place prompt by Stage 1
    alone — yet it causes catastrophic mis-routing for prompts that are NOT
    pick_place tasks (RL training, contact-rich insertion, etc.).

    A spec is "null signal" when all four conditions hold:
      1. pattern_hint is "pick_place" (the extractor default catchall)
      2. all entity counts are 0 (no robots/conveyors/bins/cubes/sensors/humans detected)
      3. all boolean structural features are False (no feature keyword fired)
      4. no structural_tags

    In this state the spec tells us nothing reliable about the task domain;
    Stage 1 filtering against it produces a large false-positive set of
    pick_place templates that crowds out the true ground truth.  The correct
    behaviour is to skip Stage 1 entirely and fall through to full-corpus
    embedding retrieval.

    Fix (R15c): called at the top of retrieve_with_intent_filter before
    filter_templates_by_intent.  When True, we short-circuit to fallback.
    """
    # Schema-default numeric/string values that the rule-based extractor
    # always emits when it cannot infer the real value from the prompt.
    # These are NOT evidence of real extractor signal; treat them as noise.
    _NUMERIC_DEFAULTS: Dict = {"n_robot_stations": 1, "n_handoffs": 0, "n_destinations": 1}
    _STRING_DEFAULTS: Dict = {"destination_kind": "single_bin"}

    if spec_intent.get("pattern_hint") != "pick_place":
        return False  # Non-default pattern → extractor found real signal
    counts = spec_intent.get("counts") or {}
    if any(v for v in counts.values()):
        return False  # At least one entity-count > 0 → real signal
    features = spec_intent.get("structural_features") or {}
    # Boolean-True flags indicate a feature keyword matched
    for k, v in features.items():
        if isinstance(v, bool) and v:
            return False  # Feature keyword fired
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            if _NUMERIC_DEFAULTS.get(k) != v:
                return False  # Non-default numeric feature set
        if isinstance(v, str) and v and _STRING_DEFAULTS.get(k) != v:
            return False  # Non-default string feature
    if spec_intent.get("structural_tags"):
        return False  # Tags were populated
    return True


def retrieve_with_intent_filter(
    spec_intent: Dict,
    top_k: int = 3,
    count_tolerance: int = 0,
    fallback_to_embedding_only: bool = True,
    original_query: Optional[str] = None,
) -> List[Dict]:
    """Structural-filter-first retrieval per spec §8.1.

    Stage 1: hard structural filter via `filter_templates_by_intent`.
    Stage 2: embedding similarity over the ORIGINAL user prompt among
        Stage-1 candidates only. (The fingerprint is for metadata filtering,
        not for embedding queries — see R14 root-cause analysis.)
    Stage 3: returns same shape as `retrieve_templates_with_scores`
        ({template, task_id, distance, similarity}) for downstream
        tier-classification compatibility.

    If Stage 1 returns no candidates (e.g., no templates have intent fields
    yet — Block 1B not landed), and `fallback_to_embedding_only=True`
    (default), the function falls back to embedding-similarity over the
    ORIGINAL USER PROMPT (not the fingerprint) against the full corpus.
    This makes the function useful immediately and progressively stricter
    as templates add intent.

    Additionally (R15c fix): if the spec carries no real extractor signal
    (``_spec_is_null_signal`` returns True), Stage 1 is bypassed entirely
    and we fall through to fallback immediately. This handles prompts that
    don't match ANY extractor keyword and receive the default
    ``pick_place`` pattern_hint — e.g., "Allegro hand IsaacLab task" or
    "peg insertion with force sensing". Without this bypass, Stage 1 would
    filter in ~53 false-positive pick_place templates, excluding all
    non-intent templates (like M-08 and CP-58) and returning wrong results.

    Args:
        spec_intent: structured intent dict (Intent.model_dump())
        top_k: max results to return
        count_tolerance: relaxes count-match tolerance in Stage 1
        fallback_to_embedding_only: when True, fall back to full-corpus
            embedding search if Stage 1 returns no candidates
        original_query: the original user prompt string. When provided,
            used as the embedding query for Stage 2 AND for the fallback
            path. When None, the fingerprint is used (legacy behaviour,
            kept for backward compatibility but produces semantic mismatch).

    Returns: list of {template, task_id, distance, similarity} dicts,
    most-similar first.
    """
    fingerprint = canonical_structural_fingerprint(spec_intent)
    # Bug fix (R15): use original_query for embedding when available.
    # The fingerprint is a structured fact string; ChromaDB was indexed on
    # natural-language goal+thoughts+tools. Querying with the fingerprint
    # yields near-random rankings (~0.4 sim vs ~0.7 for the prompt).
    embed_query = original_query if original_query else fingerprint

    # R15c fix: bypass Stage 1 when the spec has no real extractor signal.
    # A null-signal spec (all-defaults, no keywords fired) routes every
    # prompt to the ~53-template pick_place candidate set, excluding all
    # intent-less templates from ranking. Full-corpus embedding is strictly
    # better in this case.
    if _spec_is_null_signal(spec_intent):
        logger.info(
            "[TemplateRetriever] spec is null-signal (all defaults); "
            "bypassing Stage 1, falling back to full-corpus embedding"
        )
        if not fallback_to_embedding_only:
            return []
        return retrieve_templates_with_scores(embed_query, top_k=top_k)

    candidates = filter_templates_by_intent(spec_intent, count_tolerance)

    if not candidates:
        if not fallback_to_embedding_only:
            return []
        # Fallback: embedding-only over the full template set using the
        # ORIGINAL user prompt (not the fingerprint). Same return shape.
        # Bug fix (R15, Failure Mode B): was retrieve_templates_with_scores(fingerprint, ...)
        logger.info(
            "[TemplateRetriever] structural-filter found no candidates; "
            "falling back to embedding-only retrieval over full set"
        )
        return retrieve_templates_with_scores(embed_query, top_k=top_k)

    # Stage 2: embed similarity over candidates only.
    # We restrict the ChromaDB query to the candidate IDs via the `where`
    # filter (chromadb supports `$in`). The query text is the original user
    # prompt (or fingerprint if no prompt was passed — legacy fallback).
    # Bug fix (R15, Failure Mode A): was query_texts=[fingerprint].
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
            query_texts=[embed_query],
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


# ---------------------------------------------------------------------------
# Soft-filter hybrid retrieval — R15d (2026-05-16)
# ---------------------------------------------------------------------------
# Replaces the hard Stage-1 restrict + Stage-2 $in query with a softer
# hybrid that preserves full-corpus recall while using structural intent as
# a ranking boost rather than a hard gate.
#
# Algorithm:
#   1. Query full corpus for top_k * oversample candidates (full recall).
#   2. Build a set of "boosted" task_ids: templates whose intent.pattern_hint
#      matches spec_intent["pattern_hint"], AND spec is not null-signal.
#   3. For each candidate: if task_id in boosted_set → similarity *= boost.
#   4. Re-sort by boosted_similarity descending, return top_k.
#
# Key properties:
#   - Bypasses ChromaDB $in truncation entirely (full-corpus query only).
#   - Templates NOT in Stage-1 can still appear if baseline similarity is
#     sufficient — recall preserved.
#   - Stage-1 candidates get a nudge that improves their rank without making
#     them exclusive.
#   - Null-signal specs (all-default pick_place) skip the boost entirely,
#     behaving identically to the embedding-only baseline.
#   - Controlled by MULTIMODAL_TEXT_INTENT=soft env-var.
# ---------------------------------------------------------------------------


def retrieve_with_intent_soft_filter(
    spec_intent: Dict,
    top_k: int = 3,
    original_query: Optional[str] = None,
    boost: float = 1.15,
    oversample: int = 3,
) -> List[Dict]:
    """Soft-filter hybrid retrieval (R15d).

    Queries the full corpus for ``top_k * oversample`` candidates, applies a
    similarity boost multiplier to templates whose ``intent.pattern_hint``
    matches ``spec_intent["pattern_hint"]``, re-sorts, and returns top_k.

    This avoids the ChromaDB ``$in`` truncation bug and preserves recall vs
    the hard-filter path while still using structural intent as a ranking
    signal.

    Args:
        spec_intent: structured intent dict (Intent.model_dump())
        top_k: number of results to return
        original_query: the original user prompt string. Used as the embedding
            query against the full corpus. Falls back to fingerprint when None.
        boost: similarity multiplier applied to pattern_hint-matching
            candidates. Default 1.15 (15% boost). No boost is applied when
            spec_is_null_signal returns True.
        oversample: full-corpus fetch multiplier. Fetches top_k * oversample
            candidates before re-ranking and trimming to top_k.

    Returns: list of {template, task_id, distance, similarity,
        similarity_boosted, boost_applied} dicts, most-similar first.
        ``similarity_boosted`` is the post-boost score used for sorting;
        ``boost_applied`` is True when the multiplier was applied.
    """
    fingerprint = canonical_structural_fingerprint(spec_intent)
    embed_query = original_query if original_query else fingerprint

    # Fetch extended candidate set from full corpus
    n_fetch = top_k * oversample
    full_results = retrieve_templates_with_scores(embed_query, top_k=n_fetch)

    # Determine which templates are eligible for boost
    # Null-signal spec: no boost applied to anyone (mirrors baseline exactly)
    is_null = _spec_is_null_signal(spec_intent)
    spec_pattern = spec_intent.get("pattern_hint") if not is_null else None

    # Build boost set: task_ids whose intent.pattern_hint matches spec_pattern
    boost_set: set = set()
    if spec_pattern:
        # We can walk _template_cache directly (guaranteed populated by
        # retrieve_templates_with_scores → _get_collection above).
        for tid, tmpl in _template_cache.items():
            t_intent = tmpl.get("intent")
            if t_intent and t_intent.get("pattern_hint") == spec_pattern:
                boost_set.add(tid)

    # Apply boost and tag each result
    reranked = []
    for entry in full_results:
        tid = entry["task_id"]
        sim = entry["similarity"]
        apply_boost = (tid in boost_set) and (spec_pattern is not None)
        sim_boosted = sim * boost if apply_boost else sim
        reranked.append({
            **entry,
            "similarity_boosted": sim_boosted,
            "boost_applied": apply_boost,
        })

    # Re-sort by boosted similarity descending
    reranked.sort(key=lambda x: x["similarity_boosted"], reverse=True)
    top = reranked[:top_k]

    logger.info(
        f"[TemplateRetriever] soft-filter: pattern={spec_pattern} "
        f"boost_set={len(boost_set)} null_signal={is_null} "
        f"fetched={len(full_results)} → "
        + ", ".join(
            f"{x['task_id']}({x['similarity']:.2f}"
            + (f"→{x['similarity_boosted']:.2f}" if x.get('boost_applied') else "")
            + ")"
            for x in top
        )
    )
    return top
