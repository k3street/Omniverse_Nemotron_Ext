# Kcode research + vault/context-management spec for Isaac Assist

Authored 2026-05-08 in a research session. This document is **for evaluation, not for execution**.
The next session reads this and decides whether/how to act on any part of it.

The user (Anton) explicitly framed it: pragmatism, no doctrine. Best solution wins.
Implementation is a future session's job. This session's job was research and reasoning.

---

## 0. How to read this document

This document has three independent tracks. Each can be evaluated separately:

1. **Track A — Context-vault adoption from Kcode** (sections 1-7). Operational hardening
   for long multi-turn sessions and Gemini 503 mitigation (Open Q C in
   `2026-05-08-session-summary-and-handoff.md`). Contains source-level analysis of
   Kcode, the categorical fragility critique that emerged in research, the
   structured-handler insight that reframes everything, and architectural options.
2. **Track B — Wilson confidence intervals for QA measurement** (section 8). Statistical
   instrument for measuring whether template-ranking iterations (Open Q A) actually
   move the needle. Orthogonal to Track A. Independent value.
3. **Track C — Required measurements before any decision** (section 9). What data must
   be collected before either Track A or Track B is acted on. Non-negotiable.

The author's honest position at end of research:
- **Track B (Wilson) is high-leverage and low-risk.** Recommended for evaluation.
- **Track A (vault) is operational hardening of a secondary problem.** May not be the
  highest-leverage axis. Track C measurements should determine whether Track A is
  worth the architectural surface, with an honest possibility that it isn't.
- **Track C is the prerequisite to deciding either way.**

---

## 1. Source research: what Kcode actually is

Kcode (github.com/icedmoca/kcode, Rust, terminal CLI) is the product referenced in
the X/Twitter post the user shared. Author positions it against Claude Code, Cursor,
Codex CLI, Aider on a specific axis: **long tool-heavy sessions without transcript
explosion**. Kcode docs distinguish their approach from Claude Code's: Claude Code
"compresses/summarizes context to stay within the model window"; Kcode externalizes
exact context to a local vault and compresses references to it.

### 1.1 The single new idea (everything else is well-known infra)

> "Treat context as virtual memory. Summary is breadcrumb, not authority. Exact text
> lives in local vault. Model page-faults back when details needed."

The rest of Kcode (memory store, MCP bridge, sidecar model, sub-agents, swarms,
browser automation) is largely re-implementation of existing harness patterns
(Claude Code, Cursor, Aider). The new idea is architectural: **context as external
vault with reference handles, not summary inside the model window**.

### 1.2 Verified mechanisms from source code

Read from `src/interlang.rs` (68 995 bytes). Key implementation facts:

#### 1.2.1 Mode hierarchy

```rust
pub enum InterlangMode { Off, Safe, Verified, Aggressive, Ultra }
```

Default is `Ultra`. Each mode activates a different subset of encoding paths.

#### 1.2.2 Three encoding paths with different thresholds

| Encoding tag | Active in mode | Min size | What it sends |
|---|---|---:|---|
| `<ctx ... />` vault-ref | Ultra only | ≥ 16 000 chars | SHA-256 hash + topics + summary + auto-flag |
| `<il:seen ... />` seen-ref | Verified/Aggressive/Ultra | ≥ 2 400 chars | hash + summary; **only after exact text shown ≥ 1 time** |
| `<il:v1>...` pattern compression | All active | ≥ 900 chars | `@1=line` / `$1*5` repeats + `@p1=/long/path` prefix-substitution |

A vault-ref looks conceptually like:
```xml
<ctx v=1 k="vault" id="ctx:hash" h="hash" n=14000 c="0.66" p="high" ar="true" t="error,test" s="lines=...; first=..." />
```
Fields: `k`=kind, `id`=stable hash, `n`=original chars, `c`=confidence, `p`=priority,
`t`=topics, `s`=deterministic summary, `ar`=auto-rehydrate flag.

**Important detail not in docs**: `<il:seen>` does not emit hash-ref until the
exact text has already been shown at least once. First sighting: text shown in
full, hash recorded. Second sighting: ref. More conservative than docs claim.

#### 1.2.3 Confidence model is hardcoded

```
base = 0.78
−0.18 if text > 80kB,  −0.10 if > 24kB
−0.08 if > 400 lines
−0.12 if diff/error/panic content    → priority=High
−0.10 if security/auth/credential    → priority=Verify, sensitive=true (clamp 0.49)
−0.06 if kind=reasoning
+0.06 if no topics AND < 8kB
clamp 0.05..0.98
```

#### 1.2.4 Topic detection is hardcoded substring matching

```rust
let markers = [
    ("error", "error"), ("failed", "failure"), ("panic", "panic"),
    ("diff --git", "diff"), ("todo", "todo"), ("token", "token"),
    ("auth", "auth"), ("limit", "limit"), ("test", "test"), ("build", "build"),
];
for (needle, topic) in markers {
    if lower.contains(needle) { topics.push(topic); }
}
```

No embeddings. No NER. Generic English markers.

#### 1.2.5 Sensitive detector is also hardcoded

```rust
fn looks_sensitive(lower: &str) -> bool {
    lower.contains("ghp_") || lower.contains("api_key") || lower.contains("api-key")
        || lower.contains("password") || lower.contains("secret")
        || lower.contains("authorization: bearer") || lower.contains("private key")
}
```

Sensitive blocks are **never auto-rehydrated** — explicit `.ctx_get` only.
Confidence capped at 0.49.

#### 1.2.6 Auto-rehydration is OFF by default

```rust
fn auto_rehydrate_enabled() -> bool {
    std::env::var("KCODE_CTX_AUTO_REHYDRATE")
        .map(|v| matches!(v.trim().to_ascii_lowercase().as_str(),
              "1" | "true" | "yes" | "on"))
        .unwrap_or(false)
}
```

Default off. When enabled, requires:
- User-turn longer than 80 chars **and** task-continuation phrase (`continue`,
  `same error`, `from above`, `keep going`, ...)
- **Plus** precise artifact reference (`src/`, `docs/`, `.rs`, `error`, `panic`,
  `traceback`, ...)
- **Minus** token-accounting question (`how many tokens`, `efficiency`, `bloat`)
  is **actively suppressed**, even if other gates pass.

Max 1 block per turn. Auto-rehydrate threshold: `confidence ≤ 0.56`.

A vault-ref where auto-rehydrate is desired but the smart-allowed gate fails
gets a `<ctx_candidate>` marker appended, so the model knows what's available
without injecting full content.

**Implication for our analysis**: Kcode's *production default* is page-fault-only.
The whole topic-overlap-auto-restore-machinery is opt-in. When the docs pitch
"topic-gated auto-restore", that is a *capability*, not the default mode.

#### 1.2.7 Page-fault protocol

Model emits a line in its response:
```
.ctx_get id=ctx:abc123 reason=need exact stack trace
```

`parse_exact_request` extracts; `maybe_rehydrate_response` injects the exact
content into the *next* turn as a `<system-reminder>`:
```xml
<system-reminder>
Kcode ctx_get rehydration fulfilled for id=ctx:abc123 hash=abc123 reason=need exact stack trace.
Exact original content follows. Treat it as authoritative and continue the task.

<ctx_exact id="ctx:abc123" hash="abc123" original_chars=14000>
... full original text ...
</ctx_exact>
</system-reminder>
```

Note: page-fault is text-parsing on assistant response (not tool-call protocol).
For our typed-tool harness, this is the wrong shape — we'd register `ctx_get(id)`
as a tool instead.

#### 1.2.8 Storage is in-memory Mutex<HashMap>

```rust
fn seen_blocks() -> &'static Mutex<HashMap<String, SeenBlock>>
```

**Not persistent across processes/restarts.** This is hidden in docs but central.
A multi-day session loses vault on every uvicorn-restart. For Isaac Assist (where
service-restart is documented in memory), this implementation choice is a
showstopper if adopted as-is.

#### 1.2.9 Default Ultra-mode tunings

| Env var | Default | Purpose |
|---|---:|---|
| `KCODE_CONTEXT_DIET_TRIGGER_TOKENS` | 24 000 (docs); 6 000 (source) | Start replacing old bulky blocks once prompt exceeds this |
| `KCODE_CONTEXT_DIET_RECENT_MESSAGES` | 6-8 | Keep newest N messages exact |
| `KCODE_CONTEXT_DIET_MIN_BLOCK_CHARS` | 300-420 | Old text/tool/reasoning blocks at or above this can become refs |

Source and docs disagree (probably docs were updated ahead of source); the source
constants are the authoritative trigger values.

### 1.3 Honest critique of Kcode's claims

**"92% reduction"** — true *but*: compared against Kcode's own recorded uncompressed
replay baseline, NOT against Claude Code's actual compaction. Apples-to-apples
replay comparison only. Kcode docs acknowledge this clearly in their "Baseline
sensitivity note", but it requires reading the footnote.

**"Lossless externalized context"** — architecturally true (byte-for-byte in
HashMap). But:
- In-memory only — restart loses everything
- 8-byte SHA-256 truncation (64 bits) — collision astronomically unlikely but
  theoretically possible with adversarial input
- Vault itself is uncompressed — long sessions grow memory unbounded
- No persistence layer for cross-session

**"Exact rehydration prevents hallucination"** — *requires LLM cooperation*. Model
must *choose* to call `.ctx_get` when it needs exact text. If it confabulates from
summary, the safety guarantee evaporates. Kcode claims GPT-5.5 cooperates;
unvalidated for Gemini 3 Flash, Claude Sonnet, Kimi.

**"Topic-gated auto-restore"** — actually brutal-conservative as implemented. Default
off. Smart-allowed requires task-continuation phrase + precise artifact reference
+ absence of token-accounting phrase. Effect: page-fault is *primarily* model-driven
(`.ctx_get`), not automatic. Not wrong — deliberate — but not what shallow reading
implies.

**Silence about**:
- Page-fault latency cost (extra round-trip when model requests rehydration)
- Vault hit-rate in practice — if rehydration is rare, vault is dead weight
- Tag-collision when vaulted tags re-pass as user input (guarded with
  `text.contains("<ctx")` check, but fragile)

### 1.4 Source tree structure (for reference)

```
src/
  agent.rs          26721 bytes
  ambient.rs        37052
  background.rs     44173
  compaction.rs     70064 bytes  -- session/transcript compaction
  config.rs         28353
  embedding.rs      15285
  goal.rs           27406
  import.rs         50787
  interlang.rs      68995 bytes  -- vault encoding/decoding (analyzed above)
  local_model.rs    66285        -- GGUF sidecar bridge
  memory.rs         67582
  memory_agent.rs   64414
  memory_graph.rs   21032        -- internal memory graph (NOT a graph DB)
  message.rs        23823
  ...
crates/
  kcode-agent-runtime
  kcode-azure-auth
  kcode-desktop
  kcode-embedding
  kcode-mobile-core
  kcode-mobile-sim
  kcode-notify-email
  kcode-pdf
  kcode-provider-core
  kcode-provider-gemini
  kcode-provider-openrouter
  ...
docs/
  ABOUT.md
  TOOLS_AND_AGENTS.md
  BENCHMARKS.md
  ru.md
  TODO.md
  INSTALL.md
  sidecartodo/claudesidecar.md
```

---

## 2. Academic prior art for context

Kcode is not a research breakthrough. The virtual-memory-for-context idea has prior
art:

| Work | Year | Idea | Difference vs Kcode |
|---|---:|---|---|
| **MemGPT** (Packer, Wooders et al., → Letta) | 2023 | OS-paging metaphor for LLM context with `pagein`/`pageout` function calls | Structured working memory (recall vs archive), not byte-vault. LLM functions instead of `.ctx_get` text-parsing |
| **ReadAgent** (Lee et al.) | 2024 | Gist+lookup for long documents | Single-pass document reading; not multi-turn agent state |
| **vLLM PagedAttention** | 2023 | KV-cache paging on GPU | Different layer entirely — system-level, not prompt-level |
| **Anthropic Claude Code "context compaction"** | 2024 | Summarization of older turns at context-window limit | Lossy by design — what Kcode argues against. Mechanism not publicly documented |
| **LangChain ConversationBufferWindowMemory + retrievers** | 2023+ | Rolling window + RAG | Lossy summaries default; less rigor on "exact recall" |

Kcode's contribution is engineering: a working implementation of MemGPT's idea
inside a usable terminal harness, plus benchmark discipline. Not novel research.

---

## 3. Verified Isaac Assist current architecture

### 3.1 Current state (verified 2026-05-08 from disk)

**Branch**: `feat/live-progress-ui`, HEAD `2196d4f`.
PR #89 untouched. Push to `anton` remote.

**Major commits since last memory note** (handoff 2026-05-08):
- `2196d4f` simulate_traversal_check: orientation check (REORIENT-01 enabler)
- `b93baa4` verify_pickplace_pipeline: footprint_within_bounds check (CONSTRAINT-01)
- `3036b2e` qa+specs: task specs for SORT-01/CONSTRAINT-01/REORIENT-01/MULTIMODAL-01
- `a812a26` template_retriever: include thoughts in embedded text (Open Q A iteration)
- `a99cd76 → baf456b` Hard-instantiate canonical templates (Phase 1.3)
- `487aadf` ChromaDB defensive rebuild on orphan-empty (L1 root-cause fix)

### 3.2 Verified storage stack

#### 3.2.1 ChromaDB (vector / embedding store)

- `workspace/tool_index/chroma.sqlite3` — 4.6 MB, 3 collection-UUID directories
- Shadow: `service/workspace/tool_index/chroma.sqlite3` — 2.4 MB
- **Used by**:
  - `chat/tools/tool_retriever.py` → `chromadb.PersistentClient` for semantic
    top-K of tool descriptions (44 → ~20 tools)
  - `chat/tools/template_retriever.py` → canonical-template retrieval
    (CP-01..CP-04, A-01..A-10)
- Embedding model: `sentence-transformers all-MiniLM-L6-v2` (default ChromaDB embedder)
- L1 root-cause fix `487aadf` (defensive rebuild on orphan-empty collections)
  is in this layer
- **Known hazard** (memory): "Never run parallel ChromaDB writes — segfaults +
  corrupts HNSW index"

#### 3.2.2 SQLite FTS5 (full-text BM25 keyword search)

- `workspace/rag_index.db` — currently 0 bytes (idle)
- Shadow: `service/workspace/rag_index.db`
- **Used by** (when populated):
  - `retrieval/storage/fts_store.py` → SQLite virtual `fts5` table, BM25 rank
  - `retrieval/context_retriever.py` + `indexer.py` → version-gated knowledge chunks
- Code path exists, schema defined, currently unused

#### 3.2.3 Plain SQLite (short-term memory)

- `service/isaac_assist_service/memory.db` — 12 KB
- **Used by**: `MemoryManager` in `memory.py` — `conversation_logs(session_id, role, content, timestamp)`
- Append + last-N-fetch per session

#### 3.2.4 JSONL append-only event/data streams

- `workspace/session_traces/{session_id}.jsonl` — per-session event trace
- `workspace/qa_runs/campaign_*.jsonl` + `..._groundtruth.jsonl` — QA results
- `workspace/knowledge/knowledge_{version}.jsonl` — `KnowledgeBase` long-term
- `workspace/turn_snapshots/{session_id}/` — per-turn USD stage snapshots
- `workspace/audit.jsonl` — audit log

#### 3.2.5 JSON files (curated canonical data)

- `workspace/templates/*.json` — CP-01..CP-04, A-01..A-10 + others, canonical
  patterns and task definitions

### 3.3 What is NOT in the stack (verified)

- No Neo4j, Kuzu, Memgraph, TigerGraph, ArangoDB
- No NetworkX or other in-process graph library
- No `*.graph`, `*.gpickle`, or graph-schema files
- No imports for graph DB

"Graph" in code refers exclusively to:
- **OmniGraph** (Isaac Sim's runtime action/push/lazy graph)
- **USD scene graph** (USD's prim hierarchy)
- **TF tree** (ROS2 transform tree)

These are runtime constructs in Isaac Sim/USD, not data infrastructure.

### 3.4 Tool layer structure

`chat/tools/tool_executor.py` — 31 642 bytes, 376 `_handle_*` and `_gen_*` functions.

Two patterns observed in handler signatures:

**Pattern A — code-gen tools** (return code string for Kit RPC exec):
```python
def _gen_create_prim(args: Dict) -> str
def _gen_apply_api_schema(args: Dict) -> str
def _gen_anchor_robot(args: Dict) -> str
```
Result of executing the code is whatever Kit RPC echoes back. Output is in
Kit-RPC envelope `{success, output, error}`.

**Pattern B — handler tools** (return structured Dict directly):
```python
async def _handle_scene_summary(args: Dict) -> Dict
async def _handle_list_all_prims(args: Dict) -> Dict
async def _handle_get_articulation_state(args: Dict) -> Dict
async def _handle_get_console_errors(args: Dict) -> Dict
async def _handle_lookup_product_spec(args: Dict) -> Dict
async def _handle_capture_viewport(args: Dict) -> Dict
```

Pattern B handlers parse Kit RPC responses into typed Dicts before returning.
This is the **structurally critical detail for Track A** (see section 5).

### 3.5 Existing context/distillation infrastructure

Already implemented:

- `chat/context_distiller.py` (739 lines) — tool-schema selection (44 → ~8-15 via
  semantic top-K + category fallback), rule pruning, history compression
- `chat/tools/tool_retriever.py` — semantic top-20 tool retrieval
- `chat/tools/template_retriever.py` — canonical-template retrieval
- `chat/tools/tool_honesty.py` — `@honesty_checked` decorator + in-Kit validation
- `tests/test_tool_honesty_scan.py` — 56-handler allowlist scanner
- `chat/session_trace.py` — append-only JSONL per session
- `chat/turn_snapshot.py` — per-turn USD stage snapshots
- `chat/canonical_instantiator.py` — sandboxed exec of canonical templates
- `scripts/qa/direct_eval.py`, `scripts/qa/canary_trend.py` — QA harness
- 9 adversarial tasks (AD-01..AD-09), all currently 100% pass
- `docs/qa/judge_rubric.md` — 5-criterion scoring

### 3.6 Project design principles (observed convergence)

Examining commits, lessons-learned, and reverted work, the project has converged
on a pattern:

**"Harness is dumb-but-honest. LLM is smart-on-bounded-domain."**

Evidence:
- Hard-instantiate: deterministic binary gate (sim ≥ 0.45 + margin ≥ 0.20),
  no soft classifier
- Spec-first pipeline reverted 2026-04-19: harness-side classification regressed
- `tool_honesty.py`: in-Kit validation, not prompt engineering
- Open Q E (LLM ignores listed paths): directives are weak;
  exec-replacement is strong
- LESSONS_LEARNED.md: "Deterministic orchestrator-guards > prompt engineering"

**Implication**: smartness in harness (auto-restore, topic-detection,
classification pipelines) has either failed or is fragile. Smartness has been
moved either *up* to canonical templates (deterministic match-execute), *down*
to tool_honesty (in-Kit validation), or *to the model* (explicit tool-call
instead of directive).

Track A (vault) must respect this principle to be a fit.

---

## 4. The categorical fragility argument

### 4.1 The user's correction (paraphrased)

> "Det är en risk för hårdkodning. Vi har valt att gå från regex för att det gör
> det mer fragilt. Det är något vi behöver tänka på."

Translation: pattern-based extraction (regex, hardcoded substrings) creates silent
brittleness. The project has moved away from regex-style mechanisms because they
fail invisibly when vocabulary or data shifts.

### 4.2 Categorization of mechanisms

| Mechanism | Family | How it breaks |
|---|---|---|
| Regex extraction (`re.search(r"/World/[^ ]+", text)`) | Pattern-match | New prim-path convention → silent miss |
| Substring detector (Kcode `lower.contains("error")`) | Pattern-match | Localized strings, aliases, paraphrasing → silent miss |
| FTS5 BM25 with default tokenizer | Pattern-match | Special chars, paths, new vocabulary → tokenization artifacts, poor rank |
| SQL `WHERE x LIKE '%franka%'` | Pattern-match | Identical to substring |
| Hardcoded topic lists / sensitive lists | Pattern-match | List goes stale; no signal when wrong |
| **SQL WHERE on exact-match metadata** (`WHERE session_id = ?`) | **Not pattern-match** | Only fragile if metadata extraction was regex (then fragility moved upstream) |
| **Hash lookup (`WHERE id = ?`)** | **Not pattern-match** | Not fragile — content-addressed |
| Embedding similarity | Graceful-degradation | Fragile on *ranking quality*, not on *breakage* — new vocabulary embeds approximately, system doesn't crash |
| LLM classifier (intent_router style) | Adaptive | Model drift, but learns new vocabulary without code changes |
| **Set-intersection on structured metadata** | **Not pattern-match** | Only fragile if upstream extraction is pattern-match |

### 4.3 Where SQLite as vault storage actually lands

A key clarification: **SQLite as storage is fragility-neutral**.
Hash-lookup (`WHERE id = ?`) and exact-match metadata filtering
(`WHERE session_id = ?`) are not pattern-match mechanisms.

The fragility risk enters when SQLite is used with:
- FTS5 BM25 over arbitrary tool_result text
- `LIKE '%pattern%'` over text content
- Substring extraction at vault-write time

The first version of this analysis incorrectly proposed FTS5 for topic-overlap.
That was a category error — silently re-importing the regex-family fragility
under a different name.

### 4.4 ChromaDB as vault storage in fragility terms

ChromaDB's embedding-similarity has different fragility profile:
- Graceful degradation, not silent breakage
- New vocabulary embeds approximately rather than missing
- But: ranking quality is not always preserved (especially for keyword-tight
  domains like USD prim paths)
- Concurrent-write segfault is *infrastructure* fragility, not pattern fragility
  (real risk, but separate category)

### 4.5 The architectural question reframes

The storage choice (SQLite vs ChromaDB) is **downstream** of a deeper question:

> **Does vault have auto-restore, or only explicit page-fault?**

Two clean architectures:

#### 4.5.1 Architecture A — page-fault-only

- Vault tool_result by hash
- Replace in prompt with `<ctx_ref id="ctx:hash" tool="scene_summary" n=12340>`
- LLM calls `ctx_get(id)` as a registered tool when wanting exact text
- **No topics, no keyword rules, no embedding queries**
- Total fragility = storage robustness only

#### 4.5.2 Architecture B — auto-restore vault (Kcode-style)

- Everything in A, *plus* harness proactively rehydrates relevant vault blocks per turn
- Requires topic-overlap detection between user-turn and vault block
- This is where fragility lives — regardless of implementation:
  - Hardcoded substring (Kcode): pattern fragility
  - FTS5 / regex: pattern fragility
  - Embedding (Chroma): graceful-degradation fragility
  - **Set-intersection on structured metadata**: not in fragility families above

Storage choice behaves differently under A vs B:

| | Architecture A | Architecture B |
|---|---|---|
| SQLite (plain key-value) | Trivially correct — hash-lookup, no pattern-match | Insufficient — lacks similarity |
| SQLite + FTS5 | Overkill — BM25 unused | Pattern-match fragility, hardcoded tokenizer |
| ChromaDB (no embedding-query) | Works — `collection.get(ids=...)` is O(1) | Underused, but works |
| ChromaDB (with embedding-query) | Overkill — embedding cost without benefit | Graceful-degradation fragility — best-in-class IF B chosen |
| SQLite + structured metadata | Overkill (page-fault doesn't need metadata) | **Cleanest fit — set-intersection, no pattern-match** |

---

## 5. The structured-handler insight (critical reframing)

### 5.1 Observation

The user noted: "Har inte tools handlers?"

Verified in `chat/tools/tool_executor.py`: handler-pattern tools return
`Dict[str, Any]`. The "tool_result" the LLM sees is the JSON-serialized version
of that Dict. **In the harness, the structure is preserved.**

Examples:
```python
async def _handle_scene_summary(args: Dict) -> Dict
async def _handle_list_all_prims(args: Dict) -> Dict
async def _handle_get_articulation_state(args: Dict) -> Dict
```

### 5.2 Why this matters

Kcode's regex/substring topic-detection is necessary for *Kcode's* domain because
their tool outputs are arbitrary text (shell commands, file reads, build outputs).
They have nowhere else to extract topics from.

**Isaac Assist does not have this problem.** Tool outputs are typed Dicts at the
handler layer:

- `scene_summary` returns `{"prims": [...], "robots": [...], "lights": [...]}`
- `list_all_prims` returns `{"paths": [...]}`
- `get_articulation_state` returns `{"robot_path": "...", "joints": [...]}`
- `get_console_errors` returns `{"errors": [...], "warnings": [...]}`

We do not need to extract topics from tool_result text. We need to read fields
from the Dict.

### 5.3 The metadata-extraction adapter

```python
def vault_metadata_for(tool_name: str, result_dict: Dict) -> Dict:
    """Extract structured metadata from handler return for vault indexing.

    Per-tool-family adapter. Reads fields the handler already produces.
    Not regex on text — dict access on typed return.
    """
    if tool_name == "scene_summary":
        return {
            "prim_paths": [p["path"] for p in result_dict.get("prims", [])],
            "robot_paths": [r["path"] for r in result_dict.get("robots", [])],
            "has_error": bool(result_dict.get("errors")),
        }
    elif tool_name == "list_all_prims":
        return {"prim_paths": result_dict.get("paths", [])}
    elif tool_name == "get_articulation_state":
        return {"robot_paths": [result_dict.get("robot_path")]}
    elif tool_name == "get_console_errors":
        return {
            "has_error": bool(result_dict.get("errors")),
            "error_codes": [e.get("code") for e in result_dict.get("errors", []) if e.get("code")],
        }
    # ... ~30 entries for handlers that produce vault-worthy bulky output
    else:
        return {}  # default — empty metadata, hash-only lookup still works
```

This is **not** an audit of 376 handlers. Most handlers produce small results
that won't hit any vault threshold (`prim_exists`, `count_prims_under_path`,
`get_world_transform`, etc.). The vault adapter likely needs ~30 entries for
handler-families that produce ≥ 4 kB results.

### 5.4 Set-intersection auto-restore

With structured metadata at vault-time, auto-restore becomes:

```python
def relevant_vault_entries(current_turn_context: Dict, session_id: str) -> List[VaultEntry]:
    """Find vault entries whose structured metadata overlaps with current scene context.

    No regex, no embedding, no FTS5. Set-intersection on typed fields.
    """
    current_paths = set(current_turn_context.get("prim_paths", []))
    current_robots = set(current_turn_context.get("robot_paths", []))

    # SQL JSON1 array intersection, or post-fetch Python set intersection
    return vault_query(
        session_id=session_id,
        prim_path_overlap=current_paths,
        robot_path_overlap=current_robots,
        limit=3,
    )
```

This is deterministic. Not in pattern-match family. Not in graceful-degradation
family. It's typed-set intersection, which is how deterministic systems are built.

### 5.5 Edge case: code-gen tools and free-form Kit RPC output

Code-gen tools (`_gen_*`) produce code that Kit RPC executes. The result is
whatever Kit RPC echoes — `{success: bool, output: str, error: str}` envelope.

The `output` field is free-form. For these, structured metadata is unavailable
unless the executed code is templated to emit structured output.

**Two responses**:
1. Code-gen tools rarely produce vault-worthy bulky output. Their effect is on
   scene state (which is captured by `turn_snapshot.py`), not on tool_result text.
2. For the rare case where code-gen output is large and important
   (`run_usd_script` returning a long inspection result): hash-lookup vault
   still works without structured metadata. Page-fault path is unaffected.
   Auto-restore degrades gracefully to "no metadata, only available via
   explicit ctx_get".

### 5.6 Storage choice under structured-metadata architecture

When vault metadata is typed (prim_paths, robot_paths, has_error, etc.),
the storage requirement is:
- Hash-keyed primary lookup
- Indexed metadata fields for set-intersection / overlap queries
- Concurrent-safe writes (orchestrator can have parallel tool_calls)

**SQLite** with JSON1 extension or separate columns per metadata field is the
natural fit:
- Hash as PRIMARY KEY → O(log N) lookup, well under 1ms
- WAL mode handles concurrent writes without corruption
- JSON1: `WHERE EXISTS (SELECT 1 FROM json_each(metadata, '$.prim_paths') WHERE value IN (...))`
- Or columns: `prim_paths_csv TEXT, robot_paths_csv TEXT` with full-table scan
  acceptable at expected vault sizes (< 10 000 entries)
- `sqlite3` is stdlib; zero new dependencies

**ChromaDB** is not naturally suited to this:
- Embedding is unused (we have structured metadata, embedding adds no signal)
- Concurrent-write segfault risk is real and would directly affect vault
- Metadata WHERE-clauses work but the embedding overhead is dead weight

The structured-handler insight thus **resolves the storage debate**: SQLite is
the architecturally clean choice for vault, while ChromaDB stays in its
existing role for tool/template retrieval (where embedding does carry signal,
because tool descriptions are rich English prose).

This is not "SQLite > ChromaDB". It is "different jobs, different stores".

---

## 6. Architectural options for evaluation

The next session may evaluate one or more of the following. Each is independent.

### 6.1 Option P0 — Do nothing (status quo)

Default. Cost zero. Risk zero. Open Q C (Gemini 503) remains unmitigated.

### 6.2 Option P1 — Per-tool result-size cap at handler-time

**Mechanism**: Wrapper or decorator on handlers that truncates returns over a
per-tool threshold. `scene_summary` capped at 8 kB, `list_all_prims` at 4 kB,
etc. Truncation is destructive (data is gone); LLM sees a marker.

**Implementation**:
- New decorator in `tool_executor.py`: `@result_capped(max_chars_for_tool)`
- Per-tool config table (~20-30 entries)
- Marker format: `{"_truncated": true, "original_chars": N, "kept_chars": M, "tool": "scene_summary"}`
  inserted at end of capped result

**Fragility**: minimal — per-tool config is explicit and known
**Cost**: small implementation, large per-tool tuning effort
**Reversibility**: destructive (must re-call tool to recover)
**Independent value**: addresses Open Q C without any vault infrastructure
**Disable**: per-tool env override or global flag

### 6.3 Option P2 — Per-call request budget

**Mechanism**: Before provider call, count total request tokens/chars. If over
threshold T, drop oldest tool_results until under T. Replace with marker
`<dropped tool_result n=12340 from_turn=3 tool=scene_summary>`.

**Implementation**:
- Hook in `orchestrator.py` before provider call
- FIFO drop policy on tool_results from oldest message
- Marker preserves tool name + size for LLM awareness

**Fragility**: minimal — FIFO is deterministic
**Cost**: small implementation
**Reversibility**: destructive without vault; reversible if combined with P3
**Independent value**: catches whatever P1 misses, deterministic 503 prevention
**Disable**: env flag or threshold override

### 6.4 Option P3 — Hash-keyed vault with explicit `ctx_get` tool (page-fault only)

**Mechanism**: Vault tool_result by SHA-256 hash. Register `ctx_get(id)` as a
tool. LLM calls explicitly when needing exact text. **No auto-restore.**

**Implementation**:
- New file `chat/tools/context_vault.py`
- New SQLite DB at `workspace/vault.db` (or extend `memory.db`)
- New tool schema for `ctx_get(id)` in `tool_schemas.py`
- Add `ctx_get` to `_ALWAYS_TOOLS` and to `ALLOWED_AFTER_INSTANTIATE`
- Hook in `orchestrator.py` after `execute_tool_call`: if result > vault threshold,
  vault and emit `<ctx_ref>` in tool_result content

**Fragility**: minimal — hash-lookup is content-addressed
**Cost**: medium — new module, schema, integration points
**Reversibility**: full — exact recovery via ctx_get
**Independent value**: makes P1/P2 reversible; enables long-session debugging
**Disable**: env flag; without flag, ctx_get tool is just absent from schema

### 6.5 Option P4 — Set-intersection auto-restore on structured metadata

**Mechanism**: At vault-write time, extract structured metadata from handler
return Dict (per section 5.3 adapter). At each new user turn, query vault for
entries whose metadata overlaps with current scene context. Inject up to N
relevant entries proactively.

**Implementation**:
- Extends P3 with metadata extractor
- Per-tool-family adapter table (~30 entries)
- Query hook in `orchestrator.py` at user-turn start
- Configurable threshold for "how many candidates is too many"

**Fragility**: not in pattern-match family (set-intersection on typed fields)
**Cost**: medium — adapter table per tool-family + query logic
**Reversibility**: same as P3 (since this is on top of vault)
**Independent value**: helps when LLM doesn't realize it should call ctx_get
**Risk**: untested whether proactive injection helps or hurts grounding;
   could trigger Open Q E pathology if injected paths conflict with user-prompt
   paths
**Disable**: env flag; reduces to P3

### 6.6 Stacking and dependencies

| | P0 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|
| **P0** | — | superset | superset | superset | superset |
| **P1** | replaces | — | independent | independent | independent |
| **P2** | replaces | independent | — | independent | independent |
| **P3** | replaces | makes reversible | makes reversible | — | prerequisite |
| **P4** | replaces | adds smartness | adds smartness | extends | — |

Recommended evaluation order:
1. P1 + P2 first (deterministic primitives, low risk, address Open Q C)
2. P3 next if reversibility is needed (P1+P2 are destructive without it)
3. P4 last (auto-restore is opt-in even in Kcode; needs evidence it helps)

### 6.7 What this analysis explicitly rejects

- **Full Kcode port** with `<il:seen>`, `<il:v1>`, hardcoded topic detection,
  hardcoded sensitive detection, in-memory storage. Wrong domain (text vs typed),
  wrong storage (in-memory vs persistent), wrong default mode (auto-restore vs
  page-fault — even Kcode defaults to page-fault).
- **FTS5 BM25 over tool_result text**. Pattern-match family. Same fragility as
  hardcoded substrings, just at higher abstraction.
- **Embedding-similarity vault queries**. Embedding signal is weak in
  USD-domain (paths, schemas, numerics); concurrent-write hazard is real;
  better job for ChromaDB is keep doing tool/template retrieval where embedding
  does carry signal.

---

## 7. Storage decision summary

Conditional on the architectural option chosen:

| Option | Storage | Why |
|---|---|---|
| P0 (do nothing) | N/A | — |
| P1 (per-tool cap) | None — config table in code | Per-tool cap is config, not data |
| P2 (request budget) | None — runtime-only | FIFO drop is stateless |
| P3 (page-fault vault) | SQLite (`vault.db` or extend `memory.db`) | Hash-lookup, persistent, concurrent-safe via WAL |
| P4 (auto-restore) | Same SQLite + JSON1 metadata | Set-intersection on typed fields |

**ChromaDB is not the right store for vault.** It stays in its existing role for
tool/template retrieval (sections 3.2.1, 1.2.4 explain why).

**SQLite stdlib + WAL mode + JSON1 extension** covers vault operations cleanly:
- Hash as PRIMARY KEY on the main vault table
- Metadata as JSON column or split columns (depending on query pattern complexity)
- Indexed columns for `session_id`, `tool_name`, `turn_idx` for filtered queries
- Append-only or with TTL/expiry policy for size management

**Vault size estimate** (back-of-envelope):
- Typical bulky tool_result: 4-16 kB
- Per-session: 10-50 such results = 50-800 kB raw
- 100 active sessions retained: 5-80 MB total
- SQLite handles this trivially. ChromaDB HNSW index would add ~3x overhead.

---

## 8. Track B — Wilson confidence intervals (independent track)

### 8.1 Why this is orthogonal to Track A

Vault/storage is operational architecture (how data flows). Wilson intervals are
measurement methodology (how to read pass/fail data already collected). They
operate on `qa_runs/*.jsonl` regardless of harness internals.

Wilson is not in pattern-match family. The implementation is pure formula on
counts:

```python
def wilson(passes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for binomial proportion at confidence level z.

    z=1.96 is 95% confidence. Returns (lower, upper) bounds.
    """
    if n == 0:
        return (0.0, 1.0)
    p = passes / n
    denom = 1 + z*z/n
    centre = (p + z*z/(2*n)) / denom
    half = z * ((p*(1-p)/n + z*z/(4*n*n))**0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))
```

Two integers in, two floats out. No vocabulary. No tokenizer. Not even close
to fragility families.

### 8.2 What Wilson intervals enable

#### 8.2.1 Template ranking iteration measurement (Open Q A)

Currently: triple-perfect on small canary set. Coarse signal.

With Wilson: bounded CI comparison.

Example baseline: 13/20 T1-fire rate. p=0.65, 95% CI [0.43, 0.82].
After `a812a26` (include thoughts in embedded text): 16/20. p=0.80, 95% CI [0.58, 0.92].

Interpretation: intervals overlap heavily → improvement is not yet statistically
significant; need more prompts. The CI itself tells you when you have enough
data and when you don't.

This is **the highest-leverage application** of Wilson because Open Q A is the
highest-leverage roadmap question (better template ranking → higher T1-fire-rate
→ broader prompt coverage → directly serves the user-visible goal).

#### 8.2.2 Adversarial suite expansion (statistical strength)

Current: 9/9 pass on AD-01..AD-09. 100% pass rate. Wilson 95% CI [0.66, 1.00].
The lower bound 0.66 means we cannot confidently distinguish "actual hallucination
guard works" from "pretty good but maybe 1/3 of natural prompts would fail".

Expansion to 80 prompts: if 80/80, Wilson 95% upper bound on failure rate is
~4.6%. That's a defensible statistical claim.

The 80-prompt expansion can target:
- 20 fake-symbol traps (invented APIs, invented tools, invented schemas)
- 20 documentation conflicts (4.x vs 5.x version mixing)
- 20 missing tool-output claims (user asserts prior state that doesn't exist)
- 20 cross-turn memory conflicts (T4 sessions where earlier turn contradicts later)

Each bucket reports its own Wilson CI. Aggregate is computed from totals.

#### 8.2.3 Canonical-eval pass-rate per phase

Plan section "Phase 1.3 — Run VR-19 v2" ends with "honest pass/fail recorded".
Phase 2-4 follow same pattern (Run X agent-eval + analyse).

Today: typically 1 run per task. Single pass/fail.
With Wilson: N runs, X pass. Reported as p ± CI.

For T4-tier (high-level intent, stochastic): N-of-M with Wilson lower bound
threshold replaces triple-perfect. Suggested: `wilson_lower(passes, n) > 0.5`
as verified-threshold. This addresses memory note `project_isaac_assist_t4_stochastic.md`
("T4 needs 5-run / N-of-M, not 3-run triple-perfect").

For CW-tier (atomic, deterministic): triple-perfect is fine, no change.

#### 8.2.4 T4 canary trend tracking

`scripts/qa/canary_trend.py` exists. Add per-bucket Wilson CI to make the trend
graph have error bars. Each campaign run logs `(passes, n, wilson_lower,
wilson_upper)` instead of just `passes`. Comparing two runs becomes a
non-overlapping-CI test.

### 8.3 Implementation outline

Files affected:
- New helper: add `wilson()` and friends to `scripts/qa/_stats.py` (new file)
  or to existing `scripts/qa/canary_trend.py`
- `scripts/qa/canary_trend.py`: change point-estimate logging to CI logging
- `scripts/qa/mark_verified.py`: change triple-perfect threshold to
  `wilson_lower > X` for T4-tier tasks; keep triple-perfect for CW-tier
- New: `scripts/qa/adversarial_80_benchmark.py` (when adversarial expansion happens)
- `workspace/qa_runs/CAMPAIGN_REPORT.md` template: include Wilson CI per task-tier

**Implementation effort**: 1-2 hours for the helper + canary_trend changes;
adversarial expansion is a separate (larger) project.

### 8.4 Fragility and dependencies

- No vocabulary dependencies
- No tokenizer dependencies
- No data-format dependencies (works on existing `qa_runs/*.jsonl` schema)
- No infrastructure dependencies
- Compatible with all Track A options (vault has no effect on Wilson computation)
- Compatible with all current QA harness (`direct_eval.py`, `multi_turn_session.py`,
  `judge_session.py`)

The only fragility is **upstream**: what counts as "pass"? That's the ground-truth
oracle question. Wilson does not introduce this fragility; it makes it
*explicit* by forcing accurate counts.

### 8.5 Why Wilson is more valuable now than originally framed

In earlier analysis, Wilson was bundled with Track A as part of "vault adoption
package". After the structured-handler insight reframed Track A, Wilson stands
on its own — and is more clearly the higher-leverage track:

- Vault helps with Open Q C (one of five open questions)
- Wilson helps directly measure Open Q A iterations (the highest-leverage
  open question)
- Vault is operational hardening (helpful but not roadmap-central)
- Wilson is measurement discipline (directly informs roadmap decisions)

If the next session evaluates only one track, Track B is the recommendation.

---

## 9. Track C — Required measurements before any decision

Both Track A and Track B operate on assumptions that should be empirically
validated before significant work.

### 9.1 503 root-cause investigation

**Question**: What is Gemini 503's actual root cause in our sessions?

**Hypotheses**:
1. Total request tokens > Gemini threshold → P1+P2 fix it
2. Single tool_result > some shape limit → P1 fixes it
3. Rate limit (RPM) → no architectural fix; needs retry/backoff
4. Specific malformed payload (UTF-8, escape) → debug, not architecture
5. Provider-side instability → multi-provider fallback

**Method**: On next 503 incident, capture and persist:
- Full request body (sanitized)
- Response headers
- Response body
- Wall-clock timing relative to prior request
- Total tokens reported in last successful call

**Disposition**: Without this data, optimizing for hypothesis 1 may miss the
actual problem. Track A's value is conditional on (1) being the right hypothesis.

### 9.2 Tool_result size distribution

**Question**: What is the actual size distribution of tool_results in T4
multi-turn sessions vs T1 canonical-fire sessions?

**Method**: Parse existing `workspace/qa_runs/campaign_*.jsonl` files. For each
tool_call event, extract result_chars. Bucket by:
- Tool name
- Session type (T1-fire vs T5-iteration)
- Turn index
- Total session length

**Output**: Histogram. % of sessions where any tool_result > 8 kB. % where any
> 16 kB. Per-tool average and 95th percentile.

**Disposition**: If < 30% of sessions have any > 8 kB tool_result, vault
infrastructure is unjustified by data. If 70%+ do, vault is well-motivated.

### 9.3 T1-fire-rate measurement

**Question**: For a representative prompt mix (covering VR/SORT/CONSTRAINT/REORIENT
shapes), what fraction of prompts T1-fire (sim ≥ 0.45 + margin ≥ 0.20)?

**Method**: Run 50 prompts through the existing canonical-instantiator gate
without executing. Log fire/no-fire per prompt.

**Output**: T1-fire-rate p ± Wilson CI.

**Disposition**: This number is gold for **multiple** decisions:
- If p > 0.7, 503 problem is concentrated in 30% tail (T5 path); vault is
  smaller-scope problem
- If p < 0.4, T5 path dominates; vault more important
- Independent value: bench for Open Q A iterations

### 9.4 Token-vs-char ratio for Isaac Sim outputs

**Question**: For tool_results that are USD prim paths, schemas, numerics, what
is the actual `chars / tokens` ratio under Gemini's tokenizer?

**Method**: Sample 20 representative tool_results. Run through Gemini
tokenizer. Compute ratio per result and aggregate.

**Output**: Mean and 95th-percentile chars-per-token.

**Disposition**: Calibrates any threshold in P1/P2 that is set in chars but
needs to gate at tokens. Default `chars / 4` over-estimates for path-heavy
content (likely closer to `chars / 3`).

### 9.5 Cross-provider page-fault cooperation (only relevant if P3 is pursued)

**Question**: Does Gemini 3 Flash actually call `ctx_get` when needed, or does
it confabulate from summary?

**Method**: After P3 prototype lands behind a flag, run synthetic test: prompt
sequence where the answer requires data only available in vault. Measure:
- % of prompts where model calls `ctx_get`
- % of prompts where model confabulates
- % of prompts where model says "I don't have that data"

**Disposition**: If cooperation is low (< 50%), P3 alone is insufficient and
some form of P4 (proactive injection) is required. Affects evaluation of
whether P4 is ever worth the architectural surface.

### 9.6 Vault hit-rate prediction (replay simulation)

**Question**: Given vault-implementation X, how often would page-fault actually
happen on existing sessions?

**Method**: Replay-simulate over `workspace/qa_runs/*.jsonl`. For each turn,
determine which prior tool_results would be vaulted under thresholds T. Count
how often the model's subsequent text references concrete content from those
vaulted results.

**Output**: Estimated hit-rate. Estimated unused-vault-rate.

**Disposition**: If predicted hit-rate is < 10%, vault is dead weight. If 30%+,
vault is well-motivated. This measurement does not require running provider
calls — pure local replay.

---

## 10. Test strategy for any Track A change

Any vault-style change is non-trivial and should be validated in layers before
merge. Adapted from Kcode's own test discipline.

### 10.1 Layer 1 — Replay simulation (no Kit, no provider)

Parse existing `workspace/qa_runs/campaign_*.jsonl`. Compute, per session:
- Total tool_result chars
- Distribution per tool
- Repeated identical results (`<il:seen>` candidates)
- Blocks > vault threshold

**Pass criterion**: vault would actually hit ≥ 30% of large tool_result events.
Otherwise, halt evaluation.

### 10.2 Layer 2 — Deterministic vault benchmark

Mirror Kcode's `context_benchmark.py`. 80 deterministic synthetic
scene_summary/list_all_prims-style blocks + 12 queries.

Three strategies measured:
- Full context (always send all)
- Vault + page-fault only (P3)
- Vault + auto-restore on structured metadata (P4)

Metrics: retrieval precision, recall, prompt chars, hallucination rate.

**Pass criterion**: P3 must have hallucination rate ≤ full-context baseline.
P4 must have miss-rate ≤ 5%.

### 10.3 Layer 3 — A/B against real Kit, same seed

Branch + flag-gated implementation. Run T4-canary (T4-01..T4-05) and CW-batch
twice with the same seed:
- Vault off (baseline)
- Vault on (treatment)

Metrics: provider input-tokens (already in `qa_runs` traces), wall-clock,
success-rate (form+function gates), 503-rate.

**Pass criterion**:
- Vault on must not regress success rate (with Wilson CI overlap)
- Must reduce input-tokens > 30% in T4-tier
- Must reduce 503-rate

### 10.4 Layer 4 — Hallucination regression (non-negotiable)

Existing AD-01..AD-09 + any new vault-specific adversarial prompts.

**Pass criterion**: 100% pass maintained. No stage-fabrication from summary
instead of exact text. If any AD-task fails, vault is rolled back.

### 10.5 Layer 5 — Long-session stress

Specific T4 task with many scripted followups. Run 5 times.

Metrics: vault hit-rate (how often model page-faults), peak input-tokens, total
cost, success rate.

**Pass criterion**: hit-rate > 10% (otherwise vault is dead weight).

### 10.6 Layer 6 — Canary window

Two weeks behind a flag, in background. Log `vault_stats.jsonl`. No user-facing
change. Review afterward for real-world hit-rate vs lab prediction.

### 10.7 Rollback triggers

- Layer 1 shows < 30% potential on real workload → halt; focus on P1+P2 only
- A/B success-rate regress > 2 percentage points → roll back
- AD-suite regress → roll back immediately
- Vault hit-rate < 10% in canary → mechanism is dead weight; remove

---

## 11. Honest unknowns and open questions

The author flags these for the next session's awareness. They are *not* blockers
for evaluation, but they are gaps that affect how confident any recommendation is.

### 11.1 Unknown: Gemini 503 actual root cause

Hypothesis (a) is intuitive but unverified. May be (c) rate-limit or (e) provider
instability. Track C 9.1 must complete before architectural commitment.

### 11.2 Unknown: T1-fire-rate in production prompts

Affects prioritization weight of vault vs canonical-coverage expansion (Open Q B).
Track C 9.3 is the empirical answer.

### 11.3 Unknown: Cross-provider cooperation with page-fault

Kcode claims GPT-5.5 cooperates with `.ctx_get`. Unvalidated for Gemini 3 Flash
(primary) and Claude Sonnet (fallback). If cooperation is poor, P3-alone is
insufficient and we are in P4 territory.

### 11.4 Unknown: Token-vs-char ratio for our tool outputs

Affects threshold tuning. Default `chars / 4` likely under-estimates token cost
for path-heavy USD content. Track C 9.4 calibrates.

### 11.5 Unknown: Open Q E remediation path

Open Q E (LLM ignores listed paths in directive) is *not* a context-window
problem. Vault does not address it. Possible remedies (model upgrade,
exec-replacement broadening, dropped-fields formatting) are out of scope of
this document. Mention only because earlier framings of "vault solves
hard-instantiate path-hallucination" were incorrect.

### 11.6 Known limitation: structured metadata adapter completeness

Section 5.3 sketch covers ~5 handler families. Full coverage of vault-worthy
handlers requires walking all `_handle_*` and identifying which return Dicts
with extractable structured fields. Estimated effort: 1-2 days for adapter
table to cover all bulky-output handlers.

### 11.7 Known limitation: code-gen tool output

Code-gen tools (`_gen_*`) execute via Kit RPC and return the RPC envelope.
Output is free-form. Section 5.5 discusses; structured-metadata auto-restore
degrades gracefully here, but if these turn out to be dominant in real sessions,
the structured-metadata advantage shrinks.

### 11.8 Known limitation: ChromaDB concurrent-write hazard

Memory note `Korruptions-VARNING` documents the segfault risk. This does not
affect the SQLite-vault recommendation, but if any future change adds vault
metadata to ChromaDB, this hazard becomes immediate.

### 11.9 Unverified: Kcode's own benchmark numbers

Kcode reports 92% reduction. This is replay-baseline comparison, not
provider-billing comparison. Their docs are honest about this; readers must
internalize the distinction. Our own measurements (Track C 9.2) would produce
analogous numbers in our context, but they should be reported with the same
caveat.

---

## 12. What this document does NOT recommend

To be explicit (per project pragmatism preference):

- **Does not recommend immediate Track A implementation.** Recommends
  evaluation of measurements (Track C) first.
- **Does not recommend full Kcode port.** Section 6.7 explains.
- **Does not recommend ChromaDB for vault storage.** Sections 4.4, 5.6, 7.
- **Does not recommend FTS5/regex/substring topic-detection.** Sections 4.2-4.3.
- **Does not recommend auto-restore as default.** Even Kcode defaults to off
  (section 1.2.6). P4 is opt-in even within this document.
- **Does not recommend that vault is the highest-leverage axis.** Sections 8.5,
  9.3 suggest canonical-coverage and Wilson measurement may dominate.
- **Does not recommend committing to architectural choice without measurements.**
  Section 9 is non-negotiable as prerequisite.

What it does recommend (one item, low-risk):

- **Wilson interval helper + canary_trend integration.** 1-2 hours, no risk,
  immediate value for Open Q A iteration. Section 8.

---

## 13. References and source citations

### 13.1 Kcode

- Repo: github.com/icedmoca/kcode (Rust)
- Core file: `src/interlang.rs` (68 995 bytes)
- Docs: `docs/ABOUT.md`, `docs/BENCHMARKS.md`, `docs/ru.md`, `docs/TOOLS_AND_AGENTS.md`
- Author claim: "Kcode lets you run long, tool heavy coding sessions without
  blowing up token costs by compressing old context into references and only
  restoring exact data when needed, reducing hallucinations by grounding the
  model in real, retrievable source data instead of guesswork."
- Source citations in section 1.2 are direct excerpts from `src/interlang.rs`
  at HEAD as of 2026-05-08.

### 13.2 Academic prior art

- MemGPT: Packer et al., "MemGPT: Towards LLMs as Operating Systems", 2023
- ReadAgent: Lee et al., "A Human-Inspired Reading Agent with Gist Memory", 2024
- vLLM PagedAttention: Kwon et al., "Efficient Memory Management for LLM
  Serving with PagedAttention", 2023

### 13.3 Isaac Assist

- Branch: `feat/live-progress-ui`, HEAD `2196d4f` (verified 2026-05-08)
- `docs/specs/2026-05-08-session-summary-and-handoff.md`
- `docs/specs/2026-05-08-next-session-autonomous-plan.md`
- `docs/specs/2026-05-08-canonical-task-gap-analysis.md`
- `docs/specs/2026-05-08-harness-layers-and-failure-modes.md`
- `docs/qa/judge_rubric.md`
- `docs/qa/LESSONS_LEARNED.md`
- `chat/tools/tool_executor.py` (376 handlers)
- `chat/tools/tool_honesty.py`
- `chat/context_distiller.py`
- `chat/canonical_instantiator.py`

### 13.4 Open questions referenced

From `docs/specs/2026-05-08-session-summary-and-handoff.md` section "Open
architectural questions":

- A. Template ranking quality (currently being iterated; commit `a812a26`)
- B. T2 implementation: parameterized canonicals
- C. Gemini 503 mitigation strategies
- D. T3 (composition) reasoning
- E. The remaining LLM behaviour gap in T1 (sub-path hallucination)

---

## 14. Author position summary

After full research and four rounds of user-driven sharpening:

1. **Wilson intervals (Track B): high-leverage, low-risk, recommend evaluation.**
   1-2 hours of work, directly serves Open Q A iteration measurement, no
   fragility profile, no architectural surface.

2. **Vault-style mechanisms (Track A): operational hardening of a secondary
   problem.** Worth evaluating *after* Track C measurements. P1 and P2
   (deterministic primitives) are low-risk and worth prototyping. P3 (vault
   page-fault) is worth prototyping if 503-root-cause is request-size. P4
   (auto-restore) requires structured-metadata adapter and is opt-in even
   when prototyped. Full Kcode port is rejected.

3. **Storage choice for vault: SQLite, not ChromaDB.** Driven by structured-
   handler insight (section 5). ChromaDB stays in tool/template retrieval.

4. **Open Q B (T2 parameterized canonicals) and Open Q A (template ranking)
   likely dominate vault as roadmap-leverage axes.** Track C 9.3 (T1-fire-rate
   measurement) provides empirical evidence for this claim.

5. **Track C measurements should precede any architectural commitment.**
   Without 503-root-cause data and tool_result-size distribution, vault
   evaluation is gambling on hypothesis instead of data.

The author's confidence in these positions is conditional on the project's
observed convergence toward "deterministic primitives, LLM smartness on
bounded domain". If that pattern reverses, the analysis would shift.

The document is for evaluation. The next session decides.
