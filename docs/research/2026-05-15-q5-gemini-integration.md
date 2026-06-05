# Q5 — Gemini as Tool-Consumer: Integration, Test Plan, and Determinism Stress-Test

**Phase 2 / Question 5 | Research date: 2026-05-15**

---

## Executive summary

Gemini Flash already has a working provider class in this codebase. The plumbing is
mostly in place: the orchestrator is LLM-agnostic by design, `GeminiProvider` translates
between OpenAI-format messages and Gemini's `generateContent` wire format, and the MCP
server speaks standard JSON-RPC 2.0 over SSE/stdio. No architectural rework is needed.

The interesting work is the harness layer around Gemini: a driver script that replays
the existing task corpus via the `direct_eval.py` protocol, captures per-tool success
rates, and attributes failures to either "Flash makes a worse decision than Claude" or
"our tool surface is ambiguous regardless of model." The latter category is the product
win — each one is a brittleness we should fix before shipping to any user.

---

## 1. Current state of the codebase

### 1.1 MCP server

`service/isaac_assist_service/mcp_server.py` exposes the full tool surface over two
transports:

- **SSE** (default, port 8002): `GET /mcp/sse` for event stream, `POST /mcp` for
  JSON-RPC requests.
- **stdio**: newline-delimited JSON-RPC for local client processes.

The server speaks MCP protocol version `2024-11-05` (line 186). It converts
`ISAAC_SIM_TOOLS` (OpenAI function-calling format, OpenAI `{"type": "function",
"function": {...}}` envelope) into MCP `inputSchema` format at startup
(`mcp_server.py:110-142`). Result content is returned as MCP `{"content":
[{"type": "text", "text": ...}]}` blocks (`mcp_server.py:250-275`).

**Gemini MCP compatibility assessment:** As of 2026-03-xx, Google added experimental
MCP support to the `google-genai` Python SDK (confirmed from
[gofastmcp.com/integrations/gemini](https://gofastmcp.com/integrations/gemini) and
[google-gemini/gemini-cli MCP docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md),
retrieved 2026-05-15). The SDK's MCP client uses `mcp.ClientSession` / `mcp.client.stdio.stdio_client`
and queries the `tools/list` endpoint. **Our SSE transport is directly consumable** by
the `google-genai` MCP client with no wire-format changes required.

Known limitation: Gemini's built-in MCP integration only accesses `tools` from the
`tools/list` endpoint. Resources and prompts (`resources/list`, `prompts/list`) are
not used — this matches our server, which returns empty lists for both
(`mcp_server.py:171-174`). No compatibility gap.

Tool count at startup: `ISAAC_SIM_TOOLS` contains **435 tools** (grep count from
`tool_schemas.py`; MCP server adds 2 settings tools = **437 total**). This is within
Gemini 2.5 Flash's documented 128-function limit per request. **Context-window
problem: we cannot dump all 437 to Gemini at once.** See §1.5.

### 1.2 LLM provider abstraction

The `provider_factory.py` (`chat/provider_factory.py`) already supports six backends:
`local` (Ollama), `cloud` (Gemini), `anthropic`, `openai`, `grok`, `moonshot`. Gemini
is first-class citizen activated by `LLM_MODE=cloud` plus `API_KEY_GEMINI` or
`GEMINI_API_KEY` in the environment (`provider_factory.py:60-65`).

`GeminiProvider` (`chat/llm_gemini.py`) is already production-tested as the **vision
backend** (`config.py:63` — `VISION_MODEL_NAME=gemini-robotics-er-1.6-preview`). Using
it as the **main orchestrator backend** requires only the env-var change. **Zero new
provider code is required.**

### 1.3 Orchestrator: LLM-agnostic by design

`orchestrator.py:632` calls `self.llm_provider.complete(messages, {"tools":
selected_tools})` — the provider is resolved once at startup from the factory. The
orchestrator never imports `anthropic` or calls any SDK directly. All tool-call parsing
reads `response.tool_calls` (an attribute on `LLMResponse`), which `GeminiProvider`
populates in the same OpenAI-format list as `AnthropicProvider`
(`llm_gemini.py:453-468`).

Claude-specific code in the orchestrator: there is none detectable via static search.
The `SYSTEM_PROMPT` in `orchestrator.py:225-296` contains high-level behavioral rules
("match the user's intent", "grounding discipline") — these are model-agnostic. The
`AnthropicProvider` has its own shorter `SYSTEM_PROMPT` (`llm_anthropic.py:16-23`)
that is passed via the `context["system_override"]` key. The Gemini provider also
supports `system_override` (`llm_gemini.py:211-215`). Both providers pick the correct
system prompt through the same mechanism.

### 1.4 Tool schema format and translation cost

`ISAAC_SIM_TOOLS` uses the OpenAI function-calling schema:

```python
{
    "type": "function",
    "function": {
        "name": "create_prim",
        "description": "...",
        "parameters": {"type": "object", "properties": {...}, "required": [...]}
    }
}
```

`GeminiProvider._clean_params()` (`llm_gemini.py:477-489`) strips the `"default"` keys
that Gemini rejects, then formats the list as Gemini `function_declarations`
(`llm_gemini.py:241-251`):

```python
[{
    "name": fn["name"],
    "description": fn.get("description", ""),
    "parameters": cleaned_params,
}]
```

Translation is already implemented and was clearly battle-tested (the Track-C 9.1
incident logger suggests Gemini has been running in some capacity). **No translation
code needs to be written.**

Known gap: Gemini API has a 128-function limit per request
([ai.google.dev structured output docs](https://ai.google.dev/gemini-api/docs/structured-output),
retrieved 2026-05-15). The context-distiller already narrows 435 tools to ~8-15 relevant
ones per call (`context_distiller.py:1-17`). As long as the distiller runs before the
Gemini call, we stay well under 128. **The distiller must be enabled when Gemini is
the backend** — verify this in the orchestrator's tool-selection path
(`orchestrator.py:1214`).

### 1.5 Context-window cost of full tool dump

Each tool schema averages roughly 400-800 characters. 437 tools × 600 chars ≈ 260 KB ≈
~65 000 tokens if naively dumped. Gemini 2.5 Flash has a 1M-token context window, but
at $0.30/M input tokens this would cost $0.02 per call just for the tool list — before
any conversation history. Over 1000 test calls that is $20 in schema tokens alone.

The distiller path is therefore mandatory for cost control. Current distiller selects
~8-15 tools per call, bringing schema overhead to ~3000 tokens — negligible.

### 1.6 Context distiller — LLM-agnostic?

`context_distiller.py` does deterministic keyword+regex matching for tool selection
(lines 32-163) and uses the `distiller_provider` for history compression. It imports
nothing from any LLM SDK. If `get_distiller_provider()` returns a `GeminiProvider` when
`LLM_MODE=cloud`, the whole pipeline is cloud-Gemini end-to-end. **No Claude-isms in
the distiller.**

One soft dependency: the history-compression prompt (`context_distiller.py` calls
`distiller_provider.complete(...)` with a prompt asking for a JSON summary). This works
regardless of model because it is a plain text→JSON task. Flash is fine for it.

---

## 2. Integration architecture recommendation

### Choice: SDK-first (switch the orchestrator's provider), not MCP-first

**MCP-first** would mean running Gemini as an external agent that connects to our MCP
server over SSE. This would exercise the MCP transport layer, but it adds latency (one
extra HTTP hop per tool call), requires a separate process, and bypasses the
orchestrator's compression, anti-fabrication, and retry-spam-halt logic. That would
make failures harder to attribute — is the bug in MCP transport or in the tool surface?

**SDK-first** (set `LLM_MODE=cloud`, `GEMINI_API_KEY=...`, `CLOUD_MODEL_NAME=gemini-2.5-flash`)
routes Gemini through the existing orchestrator. All the test infrastructure
(`direct_eval.py`, `multi_turn_session.py`) keeps working without modification. Failures
surface the same way they do for Claude runs. This is the correct choice for harness
stress-testing.

MCP-first is useful later if we want to test Gemini CLI or third-party agents connecting
to Isaac Assist. That is a separate question from stress-testing our tool surface.

### What needs to be done

| Item | File(s) | LOC est. | Status |
|------|---------|----------|--------|
| Set `LLM_MODE=cloud`, `GEMINI_API_KEY`, `CLOUD_MODEL_NAME=gemini-2.5-flash` | `.env.local` | 3 lines | Config only |
| Verify distiller runs before Gemini call | `orchestrator.py:1214` | Read-only audit | |
| Add `gemini_harness_eval.py` driver | `scripts/qa/` | ~200 LOC | New file |
| Add per-model metrics collector to `direct_eval.py` | `scripts/qa/direct_eval.py` | ~50 LOC | Extend |
| Tool-surface audit script (Claude-isms scanner) | `scripts/qa/tool_audit_flash.py` | ~100 LOC | New file |

Total new code: ~350 LOC across 3 files. No changes to production code.

---

## 3. Tool-surface honesty audit — Claude-isms scan

### 3.1 Directive language analysis

`tool_schemas.py` (9869 lines, 435 tools) uses two types of language that may cause
model-specific behavior differences:

**1. Prescriptive USE THIS / DO NOT patterns (count: 14 × "USE THIS", 7 × "DO NOT"/"do NOT"):**

These are agent-behavioral directives embedded in tool descriptions. Examples:

- `resolve_material_properties` (line 529): *"USE THIS instead of inventing per-material
  physics numbers."*
- `resolve_count_vagueness` (line 1143): *"do NOT invent a number ('a few cubes'
  should not be 7 one turn and 4 the next; use this resolver for stable results)."*
- `resolve_prim_reference` (line 1234): *"Do NOT hallucinate prim paths like
  '/World/Cube' — search the actual stage via this tool."*

**Claude risk:** Claude Sonnet follows these directives well. **Flash risk:** Flash may
treat them as boilerplate text rather than hard rules, especially when a direct answer
is one token away (e.g., it infers "a few = 3" from training data instead of calling
the resolver).

**2. Agent-protocol descriptions in tool returns:**

- `resolve_prim_reference` (line 1239): *"count==1 → use exact_match in the next tool
  call; count>1 → ASK the user 'which one?' with the candidates; count==0 → tell the
  user nothing matches."*

This is correct instruction but requires the model to read and follow multi-branch
decision logic. Claude infers it from training. Flash may apply the wrong branch.

**3. Implicit path constraints:**

`prim_path` descriptions say `"e.g. '/World/MyCube'"` without declaring the `/World/`
prefix as `pattern: "^/World/.*"` in JSON Schema. There is no `"pattern"` field
anywhere in `tool_schemas.py`. Flash may generate paths like `"/Cube"` or `"World/Cube"`
(both are valid-looking strings but will fail USD lookup). Claude likely infers the
convention from context. Flash, being more literal, may not.

**4. Verify-step protocol embedded in descriptions:**

`verify_pickplace_pipeline` (line 677): *"USE THIS after building a pick-place /
assembly-line / multi-station scene and BEFORE declaring the build done."* This assumes
the model autonomously decides to call a verifier tool after a build tool. Claude does
this. Flash may omit the verification step.

**5. Cross-reference assumptions:**

`create_conveyor` description (line 3686): *"Single-shot: agents do NOT need to
create_prim first."* The negative instruction references an implicit assumption that
Flash might not have without Claude's training on tool-calling agent patterns.

### 3.2 Resolve-tool sample audit (4 of 12)

| Tool | Description quality | Flash risk |
|------|--------------------|-----------|
| `resolve_count_vagueness` | Clear: examples in English + Swedish, explicit "use the count directly in the next tool" | LOW — direct instruction |
| `resolve_material_properties` | Good: returns body_type (rigid/deformable) which drives API choice. The "USE THIS instead of inventing" pattern is strong | MEDIUM — Flash may still inline a density guess |
| `resolve_prim_reference` | Complex: 3-branch post-call protocol (count==1/count>1/count==0). Each branch requires a different follow-on action | HIGH — Flash likely implements only the happy path (count==1), ignores ambiguous/no-match |
| `resolve_success_condition` | Complex: 4 intent_kinds, 8 optional parameters, needs_clarification flag. Very long description with nested logic | HIGH — Flash likely picks `object_traversal` for most cases because it has the most parameters filled |

### 3.3 Error message audit

Tool errors come from `tool_executor.py` handlers. Common patterns include:
- `"Unknown tool: {tool_name}"` — clear, self-contained
- `"success=False, output: '...'"` — the `output` field may say "see the console" or
  reference a Kit UI element that Flash has no access to (Flash is driving the API,
  not looking at a screen)
- Physics errors from Kit RPC that return raw PhysX exception text — Flash will not
  know how to interpret or retry

These should be audited programmatically: grep handlers for `"see"`, `"console"`,
`"above"`, `"the error"` as bare strings.

---

## 4. End-to-end test plan

### 4.1 Corpus: 30 representative prompts

Drawn from existing task specs in `docs/qa/tasks/` (303 tasks total). Selection
criteria: mix of trivial/medium/complex × mix of intent types.

**Trivial (8 prompts) — single tool, atomic:**

| ID | Prompt (direct_eval Goal text) | Intent type |
|----|-------------------------------|-------------|
| G-01 | Place a 1m cube at (0,0,1) and a 0.5m sphere at (2,0,1) | prim_create |
| G-02 | Apply a red OmniPBR material to /World/Cube | material |
| G-03 | Take a viewport screenshot and save it | capture |
| G-04 | Set gravity to zero in the physics scene | physics_config |
| CW-01 | Create a 3m×0.2m×0.1m conveyor belt at origin | prim_create |
| CW-03 | List all prims currently in the stage | scene_query |
| G-05 | Rotate /World/Sphere by 45° around the Z axis | transform |
| G-06 | Delete all prims under /World except structural ones | batch_delete |

**Medium (12 prompts) — 2-5 tools, sequential:**

| ID | Prompt | Intent type |
|----|--------|-------------|
| AD-01 | Import the Franka Panda robot and anchor it at origin | robot_setup |
| AD-02 | Connect the Franka to ROS2 and verify joint state topic | ros2_bridge |
| AD-04 | Set all Franka joint stiffness to 100 N·m/rad | joint_config |
| AD-07 | Run a stage health check and fix any physics errors found | diagnose |
| T-01 | Resolve: place "a few" cubes on "the table" | resolver chain |
| T-04 | Place a "large" bin to catch falling parts | size resolver |
| T-06 | Put another robot "to the left of the first one" | relational resolver |
| CP-01 | Build a simple pick-and-place cell with Franka, source bin, target bin | multi-step build |
| CP-05 | Set up a conveyor that carries cubes from station A to B | conveyor setup |
| AD-09 | Clone the Franka 4 times in a 2×2 grid | batch clone |
| AD-11 | Import a Nova Carter and configure differential drive controller | mobile robot |
| T-03 | "Add the same cube as last time but green" | context reference |

**Complex (10 prompts) — multi-round, verification required:**

| ID | Prompt | Intent type |
|----|--------|-------------|
| CP-15 | Build a complete SDG pipeline for object detection training | SDG |
| CP-20 | Set up ROS2 navigation with Isaac ROS cuMotion | ros2+motion |
| A-01 | Load a custom URDF and get a joint moving in under 30 min | robot import |
| AD-14 | Diagnose why the Franka can't reach the target, fix workspace | diagnose+fix |
| T-09 | "Make the robot do the same motion as before but faster" | sequence+context |
| CP-37 | Pick place with vision-gated routing (color sorter) | vision+pick |
| AD-16 | Build a train env with 8 Frankas running parallel RL | RL training |
| T-10 | "Twice the size of the current table, same material" | relational |
| CP-42 | Export the scene as a USDZ package for review | export |
| AD-19 | Set up OPC-UA bridge connecting conveyor PLC signals to Isaac Sim | industrial |

### 4.2 Cost arithmetic

**Gemini 2.5 Flash pricing** (as of 2026-05-15 per
[ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)):
- Input: $0.30 / M tokens
- Output: $2.50 / M tokens

**1 SEK ≈ 0.092 USD** (approximate, Google bills USD and converts monthly).
**1000 SEK ≈ 92 USD.**

**Per-prompt cost estimate:**

| Tier | Avg rounds | Input tokens/round | Output tokens/round | Total input | Total output | Cost/prompt |
|------|-----------|-------------------|-------------------|-------------|--------------|-------------|
| Trivial | 1 | 4 000 | 500 | 4 000 | 500 | $0.003 |
| Medium | 3 | 6 000 | 800 | 18 000 | 2 400 | $0.011 |
| Complex | 6 | 10 000 | 1 500 | 60 000 | 9 000 | $0.041 |

Token estimates include: system prompt (~800 tokens), distilled tool schemas (~3000
tokens/call), conversation history (grows per round, compression caps at 200 KB /
~50 000 tokens by `_apply_per_call_budget`).

**30-prompt corpus cost per run:**
- 8 trivial × $0.003 = $0.024
- 12 medium × $0.011 = $0.132
- 10 complex × $0.041 = $0.410
- **Total per run: ~$0.57 per full corpus run**

**At 92 USD budget:**
- 92 / 0.57 ≈ **161 runs possible** with Flash
- Realistically plan ~50 runs (corpus × baseline + after each fix round + model-swap
  re-runs), spending ~$28.50 total. Reserve $63.50 for overruns, model-swap, and
  exploratory calls.

**Gemini 3.1 Flash comparison** (newer model, from
[discuss.ai.google.dev](https://discuss.ai.google.dev/t/gemini-3-1-flash-image-preview-inconsistent-pricing-between-ai-studio-and-api-docs/127608),
retrieved 2026-05-15 — pricing inconsistencies between Studio and API docs noted):
- Reported: $0.25 input / $1.50 output per M tokens (lower than 2.5 Flash)
- Cross-run cost would be ~$0.39 per corpus run — cheaper but note the pricing
  discrepancy in source means verify before committing

---

## 5. Day-by-day burndown (10 days, 1000 SEK = ~92 USD budget)

**Budget allocation:**
- 70% (~$64) for Flash test runs
- 30% (~$28) reserved for re-runs after fixes + model-swap

| Day | Activity | Spend est. |
|-----|----------|-----------|
| **Day 1** | Env setup: `.env.local` with Flash creds. Smoke-test 3 trivial prompts to verify provider works end-to-end. Fix any startup issues (import errors, schema reject). | $0.01 |
| **Day 2** | Baseline run: all 30 prompts with `gemini-2.5-flash`. Record per-tool success/fail, error messages, tool-call chains. Save as `flash_baseline_run1.jsonl`. | $0.57 |
| **Day 3** | Analyze baseline failures. Categorize: (A) tool-surface bugs (bad schema, misleading description), (B) resolver failures (Flash picks wrong branch), (C) Flash reasoning errors (not fixable by us), (D) harness bugs. Write bug reports for category A+B. | $0 |
| **Day 4** | Fix 3-5 highest-confidence tool-surface bugs from Day 3 analysis. Re-run only the failing tasks. Measure fix-rate. | $0.20 |
| **Day 5** | Full corpus re-run after fixes. Measure delta vs baseline. Identify next round of bugs. | $0.57 |
| **Day 6** | Fix next 3-5 bugs. Add pattern/constraint annotations to schemas where Flash generated wrong paths. Re-run failing subset. | $0.30 |
| **Day 7 (≈2026-05-22)** | **Model swap day.** New Gemini model expected ~2026-05-22. Run the same 30-prompt corpus with new model. Save as `flash_new_run1.jsonl`. | $0.57 |
| **Day 8** | Compare old Flash vs new Flash: Δ-success-rate, Δ-failure-categories. Also: re-run old Flash on the same 30 prompts (control run — isolates harness changes from model changes). | $0.57 + $0.57 |
| **Day 9** | Decision gate: if new model significantly better on resolver tasks → switch to new model for remaining runs. If marginal → continue with 2.5 Flash. Write tool-surface patch batch based on accumulated findings. | $0.20 |
| **Day 10** | Final corpus run with best-available model + all patches applied. Write findings report. Close harness issues that are not tool-surface bugs. | $0.57 |
| **Total** | | **~$3.60 actual API cost** |

The 1000 SEK budget is extremely comfortable at Flash pricing. The bottleneck is
iteration time (fixing + re-running), not cost. Consider running the 30-prompt corpus
multiple times per day to measure variance — Flash is stochastic and a task that passes
once may fail on the next run.

---

## 6. Harness and determinism stress-test plan

### 6.1 Brittleness dimensions we expect Flash to expose

**A. Directive language in descriptions — "USE THIS" as boilerplate**

Flash's lower parameter count means it is more likely to shortcut resolver tools when
a plausible answer is in training data. Expected finding: `resolve_count_vagueness` is
skipped when the phrase is "a few" (Flash outputs 3 directly). `resolve_material_properties`
is skipped when the material is "metal" (Flash invents density=7800). These are false
successes at task level but brittleness in multi-turn scenarios (different runs produce
different numbers).

**Measurement:** For each resolver tool, run 5 variants of the same prompt with
different phrasing. Count how many times Flash calls the resolver vs inline-answers.

**B. Multi-branch post-call protocols**

`resolve_prim_reference` instructs 3-way behavior based on `count`. Flash will likely:
- Follow the count==1 branch (easy, deterministic)
- Either ask the user for count>1 (correct) or pick the first candidate without asking
  (common Flash shortcut)
- Ignore count==0 and fabricate a path (most dangerous failure)

**Measurement:** Stage 0 prims, 1 prim, and 2+ prims of the same type. Measure branch
coverage.

**C. Implicit path constraints**

Flash may generate prim paths like `"Cube"`, `"cube_1"`, `"/cube"`, or `"World/Cube"`
instead of `"/World/Cube"`. Claude infers the convention from the system prompt. Flash
may not.

**Measurement:** Count USD API failures with `Sdf.Path: invalid path` or similar in
tool_executor output. Parse failing prim_path args from QA logs.

**D. Retry semantics**

The orchestrator's `_SPAM_HALT_THRESHOLD` fires after N consecutive failed patches
(`orchestrator.py:1409`). Claude typically diagnoses a failure and changes strategy
after 1-2 failures. Flash may repeat the same patch more times before changing approach,
triggering the halt at a different threshold. Or Flash may give up after a single
failure and return a partial-success reply.

**Measurement:** Log `consecutive_fail_count` per task. Compare distribution Flash vs
Claude baseline.

**E. Tool ordering and dependency enforcement**

Many multi-step tasks require a specific call order (e.g., `import_robot` before
`anchor_robot`, `create_prim` before `set_attribute`). The schemas declare no explicit
`depends_on` field. Claude infers ordering from description text. Flash may call tools
out of order.

**Measurement:** Extract tool-call sequences from QA logs. Compare ordering against
expected chains in task specs. Flag inversions.

**F. Empty-result handling**

When `scene_summary` returns `{"prims": []}` (empty stage), the agent must recognize
this as a signal to create prims first. Claude handles this gracefully. Flash may:
(a) respond to the user saying "the stage is empty" without taking action when action
was requested, or (b) proceed to call tools with fabricated paths.

**Measurement:** Run empty-stage variants of build tasks. Classify responses as:
"correct (created prims)", "incorrect (asked user)", "incorrect (fabricated paths)".

**G. Error messages with context assumptions**

Some Kit RPC errors reference the Kit console or UI:
- "Check the Script Editor console for traceback"
- Physics errors: "Contact between /World/X and /World/Y failed: invalid mesh"

Flash is an API client with no UI access. It should either retry with a different
approach or report to the user clearly. If instead it hallucinate-describes what the
console "shows", that is a fabrication failure.

**Measurement:** Count tool errors that trigger fabricated follow-on text. Use the
orchestrator's existing anti-fabrication pattern (`_extract_count_claims` etc.)
as a proxy.

### 6.2 Bug find-rate metric

Define **find-rate** as: bugs-surfaced-per-prompt = unique tool-surface defects
identified / prompts run.

A "bug" is counted when:
1. Flash fails a task that Claude passes on the same prompt AND
2. The failure is attributable to a tool schema, description, or error message (not
   Flash reasoning) AND
3. A concrete code change can fix it (schema annotation, description rewrite, error
   message improvement)

Target: ≥0.5 bugs/prompt on the first baseline run (15+ bugs from 30 prompts). If
find-rate is lower, the tool surface is already well-tuned; if higher, prioritize fixes
before the Day 7 model-swap run.

---

## 7. Mid-stream model swap protocol (~2026-05-22)

### 7.1 Trigger

New Gemini model expected ~2026-05-22 (search results confirm 2.5 Flash updates plus
3.1 series already exists; the specific May-2026 release was referenced in project
context). Trigger the swap on Day 7 regardless of whether the expected model is a
minor point release or a major version.

### 7.2 Protocol

```
Step 1: Lock the 30-prompt corpus (no prompt changes after Day 5)
Step 2: Run corpus with OLD model one final time (control run, same harness state)
Step 3: Update CLOUD_MODEL_NAME to new model ID in .env.local
Step 4: Run same 30-prompt corpus with NEW model
Step 5: Three-way comparison:
  - old_model_run1 (Day 2, pre-fix)
  - old_model_run_final (Day 7 control, post-fix)
  - new_model_run1 (Day 7, new model)
```

**Attribution logic:**

```
Δ_harness = old_model_run_final.success_rate - old_model_run1.success_rate
Δ_model   = new_model_run1.success_rate - old_model_run_final.success_rate
```

- `Δ_harness > 0` means our fixes improved things regardless of model.
- `Δ_model > 0` means the new model is better on our tool surface.
- `Δ_model < 0` means the new model regressed — report to Google or hold.

### 7.3 Decision gate

| Scenario | Decision |
|----------|----------|
| `Δ_model > 0.10` (10+ pp improvement) | Switch to new model for all remaining runs |
| `-0.05 < Δ_model < 0.10` | Keep old model; new model is not meaningfully better on our specific task distribution |
| `Δ_model < -0.05` | Hold. File regression report. Do not switch until root cause is clear. |

### 7.4 Re-run cost

Two additional full corpus runs (control + new model) on Day 7:
$0.57 × 2 = $1.14. Well within the 30% reserve ($28).

---

## 8. Production-rollout decision

**Recommendation: This is a stress-test instrument, not a production switch.**

Rationale:

1. **Gemini Flash is deliberately weaker.** The entire point of using Flash is to
   surface brittleness that Claude masks. Making Flash the production backend would
   regress user-facing task success rates — we have no baseline data yet, but category-C
   failures (Flash reasoning errors, not our fault) are expected to be non-trivial.

2. **The vision backend already uses Gemini.** `vision_gemini.py` and
   `vision_real_gemini.py` both use Gemini for vision tasks. The production split
   (Claude for agentic reasoning, Gemini for vision) already reflects Flash's
   comparative strengths. This split should be preserved.

3. **GeminiProvider is production-grade code.** The retries, payload compression, and
   incident logging in `llm_gemini.py` are already production-quality. If after the
   stress-test the **new model** (expected ~2026-05-22) shows parity with Claude Sonnet
   on our task distribution, re-evaluate then with data in hand.

4. **Cost argument for production is weak.** Flash costs $0.30/$2.50 vs Claude Sonnet
   4.6 pricing. For a B2B robotics tool, the dominant cost is engineering time and
   customer support, not token spend. A 10-20% task success rate reduction at Flash
   pricing does not justify the savings.

**If after Day 10 the new Gemini model shows ≥90% of Claude's task success rate:** open
a separate decision track for Gemini as an alternative driver (lower price tier for
cost-sensitive deployments). Until then, production stays on Claude.

---

## 9. Specific bugs predicted for Flash

These are predictions, not confirmed bugs. Success = at least 5 of 10 prove true in
the Day 2 baseline run.

**Bug 1: `resolve_count_vagueness` skipped for common terms**

Flash will inline `3` for "a few" and `5` for "several" instead of calling the
resolver. Result: determinism violation across turns (next turn Flash picks a different
number). Likely affects ~40% of count-vague prompts.

*Evidence basis:* Flash is a smaller model; the resolver description begins with a
warning against inventing numbers, but Flash may treat this as stylistic rather than
mandatory. The resolver call requires an extra round-trip Flash may skip for speed.

**Bug 2: `resolve_prim_reference` count>1 branch omitted**

When stage has 2 cubes and the user says "the cube", Flash returns the first candidate
without asking which one. Claude asks. Expected failure mode: wrong cube selected →
subsequent `set_attribute` applied to wrong prim → task fails at snapshot verify.

*Evidence basis:* Multi-branch decision protocol in description is complex; Flash
training on tool use has a strong bias toward "proceed and use first result".

**Bug 3: prim_path missing `/World/` prefix**

Flash generates `"Cube_1"` or `"/Cube_1"` for a prim path. USD lookup fails with
`Sdf.Path invalid` or returns None. Kit RPC error message does not explain the path
convention. Flash retries with the same wrong pattern.

*Evidence basis:* No `pattern` constraint in JSON Schema; description says `"e.g.
'/World/MyCube'"` which is a hint, not a requirement.

**Bug 4: `verify_pickplace_pipeline` not called after build**

Flash builds the pick-place cell correctly but skips the post-build verification step.
The description says "USE THIS after building" but Flash does not read it as mandatory.
Task snapshot shows a plausible scene but the pipeline is not reachability-verified.

*Evidence basis:* Verification is a second-level behavior. Flash prefers to end turns
after a successful build tool returns `success=True`.

**Bug 5: `resolve_success_condition` ignored entirely**

Flash never calls `resolve_success_condition` — it goes directly to build tools.
The description is long (lines 640-671) and begins with "ACCEPTANCE-EXTRACTOR" —
unusual vocabulary that Flash may parse as a capability description rather than a
mandatory call.

*Evidence basis:* Complex description + non-intuitive name + no "required" field in
the orchestrator's always-include list (`context_distiller.py:150-163` — this tool
IS in `_ALWAYS_TOOLS`, but Flash may still skip it).

**Bug 6: Retry-spam on Kit RPC physics errors**

Flash receives a PhysX error like `"PxRigidBody mass is invalid"` and retries the
same `apply_physics_material` call with identical parameters 3+ times, triggering the
spam-halt at `_SPAM_HALT_THRESHOLD`. Claude typically changes the parameter on the
second attempt. Flash is more stubborn.

*Evidence basis:* Flash's smaller reasoning capacity means it is more likely to repeat
a failed action than diagnose the cause. The error message from Kit does not tell Flash
which parameter is wrong.

**Bug 7: Wrong branch in `resolve_prim_reference` count==0 case**

Flash generates a fabricated path (`/World/Robot_01`) when count==0, instead of
reporting to the user that nothing matches. The orchestrator's anti-fabrication pattern
(`_COUNT_PAT`, `_POSE_PAT`) may not catch path-fabrication that does not include
a numeric count.

*Evidence basis:* count==0 is the least common branch; Flash training likely underweights
the "tell the user nothing matches" protocol.

**Bug 8: `resolve_size_adjective` object_class defaulting incorrectly**

Flash passes `object_class=""` for adjectives applied to robots or conveyors (since the
resolver says "leave empty if unknown"). The resolver's default scale is designed for
cubes. A "large conveyor" defaults to 2m when Flash leaves object_class empty,
producing an undersized conveyor.

*Evidence basis:* The description says "falls back to a sane default scale" without
specifying what that scale is. Flash, reading this as safe to omit, leaves object_class
blank for non-standard objects.

**Bug 9: `export_finetune_data` provider format wrong**

When asked to export training data, Flash will likely pass `format="anthropic"` or
leave it blank, defaulting to OpenAI format (`llm_schemas.py:4008`). The task may
succeed but produce the wrong output format for the training pipeline.

*Evidence basis:* Format selection requires Flash to know which provider it is — Flash
does not know it is "Gemini" unless explicitly told. The description says "Filters by
quality and converts to OpenAI, Anthropic, Ollama, or Alpaca JSONL" without specifying
a default.

**Bug 10: `HALT` injection message ignored**

When `spam_halted=True`, the orchestrator injects a `"role": "user"` message saying
`"HALT: you've run N patches in a row..."` (`orchestrator.py:1422-1436`). The expected
response is a diagnostic summary. Flash may interpret the HALT message as a new user
request and continue calling tools (since it is injected as a "user" message), triggering
a second spam-halt cycle.

*Evidence basis:* The HALT message uses all-caps and a specific format. Claude follows
these injected meta-instructions reliably. Flash, being less instruction-tuned for
agent-specific injection patterns, may treat it as normal user input.

---

## 10. Summary of findings from code investigation

| Finding | File | Line | Severity |
|---------|------|------|----------|
| GeminiProvider already exists and is production-grade | `chat/llm_gemini.py` | 173 | — (good) |
| Provider factory already supports `LLM_MODE=cloud` | `chat/provider_factory.py` | 60-65 | — (good) |
| MCP server speaks standard JSON-RPC 2.0 MCP 2024-11-05 | `mcp_server.py` | 186 | — (good) |
| Schema `default` stripping already implemented | `llm_gemini.py` | 477-489 | — (good) |
| 435 tools exceed Gemini 128-function limit | `tool_schemas.py` | all | MUST ensure distiller runs |
| 14× "USE THIS" directives in descriptions | `tool_schemas.py` | various | MEDIUM risk |
| No `pattern` constraint on prim_path fields | `tool_schemas.py` | all | HIGH risk |
| resolve_prim_reference has 3-branch post-call protocol | `tool_schemas.py` | 1239 | HIGH risk |
| Verify step not schema-enforced | `tool_schemas.py` | 677 | MEDIUM risk |
| Error messages from Kit may reference UI console | `tool_executor.py` | handlers | MEDIUM risk |

---

## Sources

- [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing) — retrieved 2026-05-15
- [Gemini 2.5 Flash pricing detail](https://pricepertoken.com/pricing-page/model/google-gemini-2.5-flash) — retrieved 2026-05-15
- [Gemini SDK FastMCP integration](https://gofastmcp.com/integrations/gemini) — retrieved 2026-05-15
- [google-genai Python SDK docs](https://googleapis.github.io/python-genai/) — retrieved 2026-05-15
- [Gemini function calling docs](https://ai.google.dev/gemini-api/docs/function-calling) — retrieved 2026-05-15
- [Gemini MCP server docs (gemini-cli)](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md) — retrieved 2026-05-15
- [Gemini structured output / tool limits](https://ai.google.dev/gemini-api/docs/structured-output) — retrieved 2026-05-15
- [Gemini 3.1 Flash pricing discussion](https://discuss.ai.google.dev/t/gemini-3-1-flash-image-preview-inconsistent-pricing-between-ai-studio-and-api-docs/127608) — retrieved 2026-05-15
- [Google Cloud billing / free tier changes](https://docs.cloud.google.com/free/docs/free-cloud-features) — retrieved 2026-05-15

> **Pricing warning:** Gemini pricing changes frequently. All prices above were current
> as of 2026-05-15. Verify at [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)
> before committing budget. The 3.1 Flash pricing in particular shows discrepancies
> between AI Studio and the API documentation — use the API docs pricing for budget
> planning.
