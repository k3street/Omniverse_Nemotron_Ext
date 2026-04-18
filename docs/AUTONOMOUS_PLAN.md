# Isaac Assist — autonomous work plan

Decision-driven plan for long self-directed sessions. Follow linearly: each
section says "if X, do Y". Never ask Anton for confirmation — make the call
from this doc + state-signals.

## Meta-rules

- **Push only to `anton` remote** (Anton's private fork). Never `origin`
  (k3street) autonomously. `git push` is already configured correctly.
- **No time estimates in reports.** Drop "ska ta X minuter", "vecka 1-2".
- **Commit frequently, push at end of each major step.** Lost work is worse
  than messy history.
- **Write a session summary at the end** (see §10) so the next session has
  trend-visibility.

## 1. First action when invoked

Run §2 state check. No exceptions. Do NOT start editing or running
campaigns until state is confirmed.

## 2. State check

Four things must be true before any QA work:

```bash
# 2a. Kit RPC alive
curl -s http://127.0.0.1:8001/exec_sync -X POST \
  -H "Content-Type: application/json" \
  -d '{"code":"print(42)"}' -m 5 -w "HTTP:%{http_code}\n"
```
- HTTP 200 → OK
- Anything else → launch: `nohup /home/anton/projects/robotics_lab/launch_isaac_sim_with_assist.sh > /tmp/isaac_sim_launch.log 2>&1 &`
  Wait for "app ready" in `/tmp/isaac_sim_launch.log` (Monitor tool with `until grep -q "app ready"`).
- If launch fails 3 times → goto §9 abort.

```bash
# 2b. Assist service alive on port 8000
curl -s http://127.0.0.1:8000/api/v1/chat/message \
  -X POST -H "Content-Type: application/json" \
  -d '{"session_id":"boot_check","message":"ok?"}' -m 20
```
- Non-empty reply → OK
- Timeout/error → restart:
  ```bash
  ps aux | grep "uvicorn.*8000" | grep -v grep | awk '{print $2}' | xargs -I {} kill {} 2>/dev/null
  sleep 2
  nohup python3 -c "import uvicorn; uvicorn.run('service.isaac_assist_service.main:app', host='0.0.0.0', port=8000, reload=False)" > /tmp/isaac_assist.log 2>&1 &
  sleep 8
  ```

```bash
# 2c. Git state clean
cd /home/anton/projects/Omniverse_Nemotron_Ext
git status --short
```
- Must show only runtime junk (`logs/`, `workspace/tool_index/`, `workspace/qa_runs/`). If there are uncommitted changes → commit them before proceeding (§8). Don't lose work.

```bash
# 2d. Template index reachable
python -c "
import chromadb
from pathlib import Path
c = chromadb.PersistentClient(path=str(Path('workspace/tool_index'))).get_collection('isaac_assist_templates')
print(f'templates: {c.count()}')"
```
- Non-zero count → OK
- Error/zero → rebuild (§3 template-rebuild snippet).

## 3. Canary run (ALWAYS do this second)

```bash
python -m scripts.qa.direct_eval --tasks G-01,G-02,G-03,FX-03,T-13
sleep 3
ls -t workspace/qa_runs/campaign_direct_*.jsonl | head -1 \
  | xargs -I {} python -m scripts.qa.ground_truth_judge --campaign {}
```

**Log the result:** append to `workspace/qa_runs/canary_trend.log`:
```bash
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) canary=N/M notes=<one-liner>" \
  >> workspace/qa_runs/canary_trend.log
```

**Compare to trend:**
```bash
tail -5 workspace/qa_runs/canary_trend.log
```

Decision gates:
- **Canary ≥ 4/5 AND equal-or-better than last 3 trends** → stable. Goto §4 (expand coverage).
- **Canary = 3/5** → marginal. Goto §5 (investigate before expanding).
- **Canary ≤ 2/5** AND previous 3 were better → regression. Goto §6 (debug regression).
- **Canary ≤ 2/5** AND previous were similar → persistent low baseline. Goto §7 (tool audit — root-cause the failures).

Template rebuild snippet (use whenever adding tasks):
```bash
python -c "
import chromadb, json
from pathlib import Path
persist = Path('workspace/tool_index')
td = Path('workspace/templates')
client = chromadb.PersistentClient(path=str(persist))
try: client.delete_collection('isaac_assist_templates')
except: pass
col = client.create_collection('isaac_assist_templates')
docs, ids, metas = [], [], []
for tf in sorted(td.glob('*.json')):
    t = json.loads(tf.read_text())
    tid = t.get('task_id', tf.stem)
    docs.append(f\"{t.get('goal','')}\n{' '.join(t.get('tools_used',[]))}\".strip())
    ids.append(tid); metas.append({'task_id': tid})
col.add(documents=docs, ids=ids, metadatas=metas)
print(f'built: {col.count()}')"
```

Then restart service per §2b.

## 4. Expand coverage (canary stable)

Pick ONE direction per work-block. Don't mix. Choose whichever you haven't
touched recently (check `git log --oneline -20`):

### 4a. G-series — geometric tasks
Saturation criterion: when 3 consecutive canaries with mixed G-tasks
score ≥ 80% AND fabrication count ≤ 1 across the set, G-series is
saturated — move to 4b or 4c.

Existing: G-01..G-06. Next IDs G-07..G-15. Each covers one new mechanic:
- Hierarchical transforms (parent xform offset + child)
- Non-axis-aligned placements (45°, 30° angles)
- Mixed primitive types in one task (cube + sphere + cylinder with relational constraints)
- Constraints involving rotation in multiple axes
- Distance-based placement with 3+ anchors

Use `docs/qa/tasks/G-01.md` + `workspace/templates/G-01.json` as template.
Every G-task must be snapshot-measurable (coord + tolerance). Include
`## Pre-session setup` if the task assumes any existing prims.

**After adding tasks, rebuild the template index** so few-shot retrieval
picks them up (the lazy-loader only builds the collection once):
```bash
python -c "
import sys; sys.path.insert(0,'service')
from isaac_assist_service.chat.tools.template_retriever import rebuild_index
rebuild_index()"
```

**Snapshot coverage:** the ground-truth snapshot captures `world_translations`,
`world_rotations_quat_wxyz`, `world_scales`, and type-specific `geometry`
(cube size, sphere radius, cylinder radius+height+axis, cone/capsule
dimensions). Only pick success criteria that these fields can verify;
otherwise the judge has nothing to score against.

### 4b. FX-series — function-coverage
Saturation criterion: when the list of uncovered tools drops below 150
OR the top-5 most-failed tools each have a dedicated FX task.

Find uncovered tools:
```bash
python -c "
import json
from pathlib import Path
import sys; sys.path.insert(0,'.')
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
all_tools = {t['function']['name'] for t in ISAAC_SIM_TOOLS}
covered = set()
for tf in Path('workspace/templates').glob('*.json'):
    try: covered |= set(json.loads(tf.read_text()).get('tools_used', []))
    except: pass
uncov = sorted(all_tools - covered)
print(f'uncovered: {len(uncov)}')
for t in uncov[:30]: print(f'  {t}')"
```

Pick 3-5 high-impact ones (look for tools named for real workflows, not
`batch_*`/`list_*` utilities). Build FX-NN.md + FX-NN.json following
FX-01 pattern.

### 4c. Task revision — improve weak-scoring existing tasks
If a task consistently fails in canaries but the scene change IS
reasonable, the task's success criterion may be too strict (judge is
pedantic about literal text etc.).

Look at last 3 `_groundtruth.jsonl` for tasks that failed with
`notes` mentioning "literal", "verbatim", "did not say exactly".
Soften those criteria in the .md (not the template).

## 5. Marginal canary — investigate before expanding

Before expanding coverage, identify WHY canary is only 3/5. Common causes:
- One task consistently fails (same 2/5) → it's a task-quality issue, soften spec
- Different tasks fail each run → LLM variance, is real ceiling
- Tool failures visible in tool_calls → infrastructure/tool-bug

Run:
```bash
latest=$(ls -t workspace/qa_runs/campaign_direct_*.jsonl | head -1)
python -c "
import json, glob
from pathlib import Path
f = Path('$latest')
gt = Path(str(f).replace('.jsonl', '_groundtruth.jsonl'))
for l in gt.read_text().splitlines():
    r = json.loads(l); v = r['verdict']
    ok = v.get('real_success')
    fab = len(v.get('fabricated_claims', []))
    print(f\"{'✓' if ok else '✗'} {r['task']}: fab={fab} — {v.get('notes','')[:120]}\")"
```

Fix whatever's clearly fixable (§6 if regression, §7 if tool), then re-run canary.

## 6. Debug regression

Canary worse than last 3 → identify the commit that broke it.

```bash
git log --oneline -10
```

Check the 2-3 most recent commits. If any touched `orchestrator.py`,
`context_distiller.py`, or `tool_executor.py`:

```bash
git diff HEAD~1 -- <file>
```

Common regression sources:
- Prompt-rule edit that confused the LLM → revert or soften
- New guard that strips legit content → check `verify_warnings` in logs
- Regex in a guard that false-positives → tighten the regex
- Rebuild of tool_index that changed tool_retriever ranking → check top-K for relevant queries

If unclear:
```bash
git revert <commit>
# rerun canary; if restored, you found the culprit; iterate on the revert
```

## 7. Tool audit (persistent low baseline)

Aggregate recent fails:
```bash
python -m scripts.qa.aggregate_failures        # default: last 24h, infra errors excluded
python -m scripts.qa.aggregate_failures --hours 2   # tighter window after a batch
python -m scripts.qa.aggregate_failures --tool <name>   # drill into one tool
```

The script defaults to a **24-hour window** and **excludes Kit-RPC-down / connection errors** so a single Kit crash doesn't drown real tool bugs in a sea of connection-refused entries. Pass `--all` to include historical campaigns, `--include-infra` to keep infra errors. `infra_fails` column shows them separately so you can see how much noise the filter dropped.

Pick the top-failed tool with a clear error signature. Common 5.x fixes:
- `GetAllDescendants()` → `list(Usd.PrimRange(prim))[1:]`
- `HasAPI(...JointAPI)` → `IsA(...Joint)`
- `CreateXxxAttr(val)` gone → raw `prim.CreateAttribute(name, Sdf.ValueTypeNames.X).Set(val)`
- `omni.isaac.*` import → find `isaacsim.*` equivalent via `lookup_knowledge` or fail-list
- Missing `Usd` import in `from pxr import ...` line when code uses `Usd.PrimRange`

**Before fixing: live-smoke-test the tool.** Older campaigns may show failures that are already fixed in HEAD. Generate the code for the tool and POST it to `http://127.0.0.1:8001/exec_sync`; if it now passes, the aggregate is just historical noise. Pattern:
```python
import httpx, sys; sys.path.insert(0,'service')
from isaac_assist_service.chat.tools.tool_executor import _gen_<tool>
code = _gen_<tool>({...args...})
print(httpx.post('http://127.0.0.1:8001/exec_sync', json={'code':code}, timeout=60).json())
```

Apply fix to `service/isaac_assist_service/chat/tools/tool_executor.py`. **Restart the assist service** so `CODE_GEN_HANDLERS` rebinds to the new functions (they're captured at import time):
```bash
ps aux | grep "uvicorn.*8000" | grep -v grep | awk '{print $2}' | xargs -I {} kill {} 2>/dev/null
sleep 2
nohup python3 -c "import uvicorn; uvicorn.run('service.isaac_assist_service.main:app', host='0.0.0.0', port=8000, reload=False)" > /tmp/isaac_assist.log 2>&1 &
sleep 8
```
Re-run canary. Add an L0 regression test in `tests/test_qa_scripts.py` asserting the code-gen output contains the fix (e.g. the right import line).

## 8. Commit + push discipline

After each logical change:

```bash
git add <specific files — never 'git add -A'>
git commit -m "$(cat <<'EOF'
<type>(<scope>): <one-line subject>

<body: what changed and why, 2-5 lines>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Types: `fix`, `feat`, `docs`, `chore`, `refactor`, `test`.
Scopes: `tools`, `qa`, `chat`, `service`.

Push after 2-3 commits or at end of session (whichever first):
```bash
git push   # defaults to anton remote
```

If push rejected (rare on `anton`): read error, act accordingly. Never push to origin autonomously.

## 9. Abort conditions — stop and leave a note for Anton

Stop autonomous work if:
- Kit RPC launch fails 3 times → isaacsim install may be broken
- Canary drops to 0/5 after your changes AND revert doesn't restore → state corrupted
- `git` refuses to commit (e.g. index locked, corrupt) → don't force, leave
- Gemini returns 503 on > 50% of calls for > 10 min → upstream outage, later
- Uncaught exception that hits service-level error handler you can't diagnose

When aborting: write `docs/ABORT_<timestamp>.md` with:
- What was the last known good state (last canary score, commit hash)
- What you were trying
- What failed
- What you tried to recover
- What Anton should check first

Commit that file. Push. Stop.

## 10. Session summary (end of work-block)

Before stopping (even a healthy stop), write:

```
docs/SESSION_SUMMARY_<YYYY-MM-DD>.md
```

Contents:
- Starting canary score
- Direction chosen (4a/4b/4c/audit/…)
- Commits made (git log --oneline HEAD~N..HEAD)
- Ending canary score
- Tasks added / tools fixed / guards updated (1-line each)
- Next session's entry-point: which direction to pick if state is similar

Commit with `docs: session summary <date>`. Push.

Also append a line to `workspace/qa_runs/canary_trend.log` if not already:
```
YYYY-MM-DDTHH:MM:SSZ canary=N/M commits=X direction=<4a/4b/…>
```

## 11. Using Sonnet agents (parallelize expensive research)

Fire `Agent` calls for work that would otherwise blow main-context tokens:

### Use Sonnet agents for:
- **Tool audit in parallel**: spawn 3 Explore agents, each auditing a subset
  of the top-20 failing tools — each returns "here's the bug + fix in 200 words".
- **Pattern synthesis** across many transcripts: spawn general-purpose agent
  with "read these 5 transcripts, tell me the common failure shape in 300 words".
- **Task drafting**: spawn agent with "write a G-NN spec + template JSON for
  <description>, following existing G-01 style".
- **External research**: claude-code-guide or general-purpose for "what's the
  correct isaacsim.* name for omni.isaac.X" when lookup_knowledge fails.

### Don't use Sonnet agents for:
- Single-file edits (Edit tool is direct)
- Git commands (Bash is direct)
- Running canary (direct script is faster)
- Things you can check in 2 tool calls

### Running agents in parallel
If spawning >1 agent for independent work, send them in a single message
with multiple Agent blocks — they run concurrently.

### Subagent types reference
- `Explore` — fast codebase search, up to "very thorough"
- `general-purpose` — multi-step research, full tool access
- `Plan` — architectural planning only, no writes
- `claude-code-guide` — Claude Code / API / SDK questions

## 12. Persistent state recap

Hardcoded facts (update if they change):
- Repo root: `/home/anton/projects/Omniverse_Nemotron_Ext`
- Active branch: `feat/qa-runtime-bundle`
- Remote: `anton` (push here), `origin` (do NOT push)
- Kit RPC: `http://127.0.0.1:8001/exec_sync`
- Assist service: `http://127.0.0.1:8000`
- LLM: Gemini 3 flash preview (`gemini-robotics-er-1.6-preview`)
- Launch script: `/home/anton/projects/robotics_lab/launch_isaac_sim_with_assist.sh`
- Baseline snapshots (as of 2026-04-18):
  - G+FX direct: 3/8 = 37.5%
  - G+FX persona: 7/8 = 87.5%
  - Pareto persona: 2/4

Updated thresholds to chase (not time-bound, just goal):
- Direct-mode on G+FX canary: ≥ 4/5 consistently
- Direct-mode on broader sample: ≥ 50% within next 5-10 work-blocks
- Fabrication count per session: ≤ 1 median

## 13. When in doubt

- Re-read §1 and §2. Most mistakes come from skipping state-check.
- If a decision isn't covered here, pick the most conservative option
  (don't write if you can read; don't revert if you can investigate first;
  don't force if you can abort).
- Write an ABORT doc (§9) if truly stuck — it's a clean hand-off, not a failure.
