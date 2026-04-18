# Isaac Assist — architecture review (2026-04-18)

Snapshot after a day of honesty-focused bug-hunting. 24 tool-level silent-success holes fixed; canary on the 20-task suite now sits around 18-19/20 depending on LLM variance. Two failures keep recurring (T-13 and C-03) — both trace to LLM output truncation rather than tool or orchestrator bugs.

## Structural guards in place

Layer-by-layer, what keeps the agent honest:

1. **Tool-side (code-gen handlers in `service/.../tool_executor.py`)**
   - Post-state verification after every mutation — e.g. `GetAppliedSchemas()` diff on `apply_api_schema`, `HasAuthoredReferences()` on `add_reference`, `os.path.exists` on `save_stage`.
   - Raise `RuntimeError` / `FileNotFoundError` on silent no-ops. Agent sees the failure in its next tool-call result and relays it.
   - Still holds: 344 total handlers, ~24 audited. The remaining ~320 likely have the same patterns.

2. **Orchestrator (`service/.../chat/orchestrator.py`)**
   - **Fix B** — if a tool failed this round but the reply asserts success without acknowledging failure, rewrite. Keyword heuristic, model-agnostic.
   - **Fix C** — inline Python blocks in the reply are AST-validated before handing to the user. Catches agent writing broken code.
   - **Fix D** — regex-detect Kit UI menu paths (`File > Save As > …`) and append a "not retrieved from any tool" warning. Deterministic on the pattern.
   - **Fas 2 verify-contract (5 sub-checks, all deterministic)** — after the LLM commits to a reply:
     - **(a) Path-exists** — scan for `/World/...` paths; auto-invoke `prim_exists` on up to 4 that weren't in tool outputs.
     - **(b) Count-claim** — "16 arms under /World/envs" → `count_prims_under_path`; if shallow count diverges, also try recursive to disambiguate "wrong total" vs "deeper nesting than claimed".
     - **(c) Transform/pose** — "X at (a, b, c)" → `get_world_transform`; flag if coordinates diverge > 0.05m.
     - **(d) Schema/API** — "RigidBodyAPI on /World/X" → `list_applied_schemas`; flag if the claimed schema is absent.
     - **(e) Attribute-value** — "X mass=1.0" / "friction is 0.8" → `get_attribute`; flag if value diverges > 2% (or 0.01 absolute).
     - Each sub-check caps at 2-3 per turn for bounded cost. Mismatches concatenate into one `⚠️ Verification mismatch` block appended to the reply.

3. **Context distiller (`service/.../chat/context_distiller.py`)**
   - `HONESTY DISCIPLINE` + `EXECUTION DISCIPLINE` baked into `RULE_BASE`. Tells the LLM: tool failure = effect did NOT happen; verify before claiming; don't invent menu paths; Isaac Sim 5.x uses `isaacsim.*` namespace.

4. **Harness-side (`scripts/qa/`)**
   - `_reset_stage` does explicit `RemovePrim` loop + `UsdGeom.Xform.Define('/World')` to defeat stage-state leakage between tasks.
   - Extended snapshot captures positions, rotations (orthonormalized), scales, per-primitive geometry (effective dimensions for cube size / sphere radius / cylinder radius+height), authored visibility. Gives judges enough evidence to ground-check claims.
   - Judge `_snapshot_summary` now surfaces these fields (pre-fix, the snapshot had the data but the judge never saw it).

## Known gaps — why we're at 18-19/20 not 20/20

1. **LLM output truncation** — Gemini 3 flash occasionally cuts off mid-sentence on long replies (T-13's cite-able statement, C-03's multi-step diagnosis). `max_tokens=4096` is set in `llm_gemini.py`. Raising it didn't help in quick tests — the truncation looks sentence-level rather than token-cap.

2. **Agent reaches for `run_usd_script` when a registered tool fits** — C-01 and C-03 have run_usd_script as a legitimate fallback but the agent sometimes emits patterns like `CreateMeshPrimWithDefaultXform` which doesn't honor the path arg. When the registered `create_prim` tool does exactly the right thing, the agent shouldn't fall through to raw Python. No current guard for this.

3. **Judge variance** — Gemini judge scores the same transcript differently across runs. Same task can be ✓ one run, ✗ the next, purely due to phrasing choices. Fallback-regex catches truncated verdicts but not scoring drift.

4. **Stochastic LLM tool choice** — agent picks between 2-3 tool chains that all accomplish the task; one chain may miss a verification step the criterion wants. Fixable by softening over-literal criteria, or by letting the agent see the criterion via retrieval (template docs).

## Scanner heuristic — `grep`-based honesty audit

Broad antipattern catalog from 2026-04-18 scan of 344 handlers:

| Pattern | Count | Fixed | Remaining |
|---|---|---|---|
| `try/except` + `print` (swallows errors) | 19 | 4 | 15 |
| `omni.kit.commands.execute('...Command')` | 3 | 2 | 1 (focus_viewport_on — now fixed, scan is stale) |
| `AddReference` without `HasAuthoredReferences` | 5 | 5 | 0 |
| `schema.Apply(prim)` without post-check | 27 | ~3 | ~24 |

Scanner script (copy-paste, doesn't need the repo to be importable):
```python
import re
with open('service/isaac_assist_service/chat/tools/tool_executor.py') as f: src = f.read()
hs = list(re.finditer(r'^(?:async\s+)?def (_gen_[a-z0-9_]+|_handle_[a-z0-9_]+)\(', src, re.M))
for i, h in enumerate(hs):
    name = h.group(1); start = h.start()
    end = hs[i+1].start() if i+1 < len(hs) else len(src)
    body = src[start:end]
    if re.search(r'except Exception[^\n]*:\s*\n[^\n]*print\(', body) \
       and 'raise' not in body[body.find('except Exception'):body.find('except Exception')+400]:
        print(name)
```

## Proposed next steps (ranked by ROI)

1. **CI regression test against the antipattern list** (high ROI, low effort). Write a pytest that scans tool_executor.py handlers for the known antipatterns and fails if a new PR adds one without a corresponding raise/verification. Keeps future bundled tools honest.

2. **Sample 10 random handlers/day for live audit** until the 344-handler surface has been covered once. Two hours of probing a day covers the corpus in a couple of weeks.

3. **Structured output for the judge** (medium ROI, medium effort). Outlines / Guidance would let us force the judge to always return valid JSON with the required fields. Kills the parse-error fallback noise and reduces verdict variance. Also reduces "truncated mid-sentence" judge failures (though not agent-side truncation).

4. **Multi-turn direct-eval** (high ROI, medium effort). Adds a single follow-up "did you fully address criteria X?" turn when the initial reply is short or lists only part of a criterion. Bridges the gap between direct-mode (measures tool correctness) and persona-mode (measures conversational behavior). Expected to recover T-13 and C-03.

5. **Tool-choice enforcement in orchestrator** (high ROI, high effort). When the user's message matches a registered tool with high confidence (retriever score > threshold), penalize or warn when the agent reaches for `run_usd_script` instead. Would reduce C-01-style stochastic failures.

6. **Handler scaffold decorator** `@honesty_checked` (high ROI, high effort). Wraps a code-gen handler and auto-injects post-verification boilerplate (path existence, HasAPI diff, etc.). Prevents new silent-success bugs from being bundled.
