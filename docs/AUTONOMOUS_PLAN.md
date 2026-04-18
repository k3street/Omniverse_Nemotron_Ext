# Isaac Assist — autonomous work plan (decision tree)

Använd detta dokument för att jobba självständigt utan att behöva fråga varje steg. Entry-point: läs nuvarande state (senaste campaign summary, senaste judge output), hitta rätt gren, exekvera.

## 0. Entry-point — kolla state först

```
Kör:
  curl -s http://127.0.0.1:8001/exec_sync -X POST -d '{"code":"print(42)"}' -m 5
```

- **HTTP 200** → Kit RPC alive → fortsätt till steg 1
- **HTTP 000 eller timeout** → Isaac Sim nere → kör `/home/anton/projects/robotics_lab/launch_isaac_sim_with_assist.sh`, vänta på "app ready" i loggen, sen fortsätt
- **Annan fel** → avbryt, flagga till Anton

Kolla Assist-service:
```
curl -s http://127.0.0.1:8000/api/v1/chat/message -X POST -H "Content-Type: application/json" -d '{"session_id":"canary","message":"hi"}' -m 20
```

- **Svar OK** → service alive
- **Fel** → restart: `ps aux | grep uvicorn.*8000 | grep -v grep | awk '{print $2}' | xargs -I {} kill {}; sleep 2; nohup python3 -c "import uvicorn; uvicorn.run('service.isaac_assist_service.main:app', host='0.0.0.0', port=8000, reload=False)" > /tmp/isaac_assist.log 2>&1 &`

## 1. Kör canary-svit

Syfte: fånga regressions från senaste ändringar.

```
python -m scripts.qa.direct_eval --tasks G-01,G-02,G-03,FX-03,T-13
```

Sen:
```
python -m scripts.qa.ground_truth_judge --campaign workspace/qa_runs/campaign_direct_<latest>.jsonl
```

**Läs siffran:**
- **5/5 eller 4/5** → systemet stabilt → gå till steg 2 (utöka coverage)
- **3/5 eller 2/5** → regression jämfört med tidigare runs → gå till steg 3 (debug regression)
- **0-1/5** → infrastruktur-fel sannolikt → gå tillbaka till steg 0 (kolla Kit RPC), sen om infra OK → gå till steg 3

## 2. Utöka coverage (om canary ≥ 4/5)

Tre parallella riktningar, välj den med lägst täckning:

### 2a. Fler G-tasks (geometri)
Redan byggda: G-01..G-06. Lägg till G-07..G-15:
- Rotation-kombinationer (inte bara enkelaxel)
- Avstånd mellan flera objekt (inte bara anchor + en ny)
- Hierarkiska prims (parent-child transforms)
- Non-axis-aligned placements (45° positioner)

Varje G-task följer mall:
- `docs/qa/tasks/G-NN.md` — Goal + Success criterion (snapshot-mätbart) + Pre-session setup om behövs
- `workspace/templates/G-NN.json` — goal, tools_used, thoughts, code, failure_modes

Efter 3-5 nya G-tasks, rebuilda template-index:
```
python -c "
import chromadb, json
from pathlib import Path
persist = Path('/home/anton/projects/Omniverse_Nemotron_Ext/workspace/tool_index')
templates_dir = Path('/home/anton/projects/Omniverse_Nemotron_Ext/workspace/templates')
client = chromadb.PersistentClient(path=str(persist))
try: client.delete_collection('isaac_assist_templates')
except: pass
col = client.create_collection('isaac_assist_templates')
docs, ids, metas = [], [], []
for tf in sorted(templates_dir.glob('*.json')):
    t = json.loads(tf.read_text())
    tid = t.get('task_id', tf.stem)
    goal = t.get('goal',''); tools = ' '.join(t.get('tools_used',[]))
    docs.append(f'{goal}\n{tools}'.strip()); ids.append(tid); metas.append({'task_id': tid})
col.add(documents=docs, ids=ids, metadatas=metas)
print(f'Built: {col.count()} templates')"
```

Restart service, kör nya G-tasks i `direct_eval`.

### 2b. Fler FX-tasks (function-coverage)
Hitta tools utan täckning:
```
python -c "
import json
from pathlib import Path
import sys; sys.path.insert(0,'.')
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
all_tools = {t['function']['name'] for t in ISAAC_SIM_TOOLS}
covered = set()
for tf in Path('workspace/templates').glob('*.json'):
    try: t = json.loads(tf.read_text()); covered |= set(t.get('tools_used', []))
    except: pass
print(f'Uncovered: {len(all_tools - covered)} tools')
for t in sorted(all_tools - covered)[:20]: print(f'  {t}')"
```

Plocka 3-5 högimpakt uncovered tools, bygg FX-serien likt FX-01..FX-03.

### 2c. Audit av misslyckade tools
Kör:
```
python << 'PY'
import json, glob
from collections import defaultdict, Counter
from pathlib import Path
tool_stats = defaultdict(lambda: {'calls': 0, 'fails': 0, 'errors': Counter()})
for gt in glob.glob('workspace/qa_runs/campaign_*_groundtruth.jsonl'):
    for l in Path(gt).read_text().splitlines():
        r = json.loads(l); tr = Path(r.get('transcript',''))
        if not tr.exists(): continue
        for line in tr.read_text().splitlines():
            try: d = json.loads(line)
            except: continue
            if d.get('event') != 'isaac_assist_reply': continue
            for tc in d.get('tool_calls', []):
                name = tc.get('tool','?'); result = tc.get('result', {})
                tool_stats[name]['calls'] += 1
                if result.get('success') is False or result.get('executed') is False:
                    tool_stats[name]['fails'] += 1
                    err = str(result.get('output') or result.get('error') or '')[:100]
                    tool_stats[name]['errors'][err] += 1
for name, s in sorted(tool_stats.items(), key=lambda x: -x[1]['fails'])[:15]:
    if s['fails']==0: break
    print(f"{name}: {s['calls']} calls, {s['fails']} fails — {s['errors'].most_common(1)[0][0][:80]}")
PY
```

Identifiera tools med `5.x API`-error-mönster:
- "has no attribute" → API-namn fel → fixa i `tool_executor.py`
- "cannot import" → module renamed i 5.x → fixa imports
- "incompatible function signature" → argument-ordning fel → läs ny API docs

Vanliga 5.x-fixar (redan tillämpade idag):
- `prim.GetAllDescendants()` → `list(Usd.PrimRange(prim))[1:]`
- `.HasAPI(UsdPhysics.RevoluteJointAPI)` → `.IsA(UsdPhysics.RevoluteJoint)`
- `artic_api.CreateFixedBaseAttr(True)` → raw `prim.CreateAttribute('physxArticulation:fixedBase', Sdf.ValueTypeNames.Bool).Set(True)`

## 3. Debug regression (om canary ≤ 3/5)

### 3a. Läs senaste campaign-summary + jämför mot kända baseline

Baseline (sparad 2026-04-18):
- G+FX direct: 3/8 = 37.5%
- G+FX persona: 7/8 = 87.5%
- Pareto (M-13, D-12, T-12, T-13) persona: 2/4

Om canary nu sämre:
1. Kolla om senaste git-commit rörde `orchestrator.py`, `context_distiller.py`, eller `tool_executor.py`
2. `git diff HEAD~1 -- <file>` — läs diffen
3. Vanliga regression-orsaker:
   - Prompt-ändring som förvirrade LLM → riv ut, skriv om
   - Verify-contract för aggressiv → stripper legit claims
   - Tool-regex som fångar false positives
4. Om otydligt: `git revert <commit>`, kör canary igen, jämför. Isolerar commit som bröt.

### 3b. Inspektera transcript från failad canary-session
```
latest=$(ls -td workspace/qa_runs/run_direct_* | head -1)
cat $latest/*.jsonl | python -c "
import sys, json
for l in sys.stdin:
    try: d = json.loads(l)
    except: continue
    e = d.get('event')
    if e == 'isaac_assist_reply':
        tc = d.get('tool_calls',[])
        print(f'tools: {[t[\"tool\"] for t in tc]}')
        print(f'reply: {d.get(\"text\",\"\")[:400]}')
"
```

Titta efter: tom reply, truncated reply, fabricated-claim-mönster, skipped verification.

## 4. Om Gemini-problem (503, 429, timeout-bursts)

Känn igen:
- "Gemini 503 (attempt X/4)" i service-loggen → cloud-side overload
- Många timeouts → kan vara rate limit

Handlingar:
1. Vänta några minuter, kör canary igen
2. Om persistent → flagga till Anton, fortsätt inte mer testning den dagen

## 5. Om Kit RPC dör mid-sweep

Sign: `run_usd_script` börjar returnera "Cannot connect to host 127.0.0.1:8001".

Handlingar:
1. Stoppa pågående sweep: `ps aux | grep direct_eval | grep -v grep | awk '{print $2}' | xargs -I {} kill {}`
2. Kolla Isaac Sim-process: `ps aux | grep -i isaac-sim | grep -v grep`
3. Om död → omstart via launcher
4. Analysera vad du har (de som körde innan dödsfallet är giltiga)

## 6. Commits + PR-strategi

Efter ändringar, commita per tema (inte "fixar från idag"):
- `fix(tools): <specific bug>` — enskild tool-fix
- `feat(qa): <new task set>` — nya tasks + templates
- `feat(chat): <guard or rule>` — orchestrator/prompt-ändring
- `docs: <update>` — doc-ändring

Push regelbundet till `feat/qa-runtime-bundle`. Öppna INTE PR utan Anton:s beslut. Se `docs/BRANCH_PR_NOTES.md` för kontexten kring PR-strategi.

## 7. När ska jag avbryta och flagga till Anton?

- Kit RPC dör och launcher misslyckas få igång den igen
- Canary faller från baseline utan identifierbar orsak efter 30 min felsökning
- Git state verkar korrupt (merge mid-state som inte går abortera)
- Upprepat Gemini 503 som varar > 30 min
- Inga tools lyckas exekveras alls — antagligen state-issue som kräver manuell utredning

## 8. Sluta-kriterier

Anton gav 1h/2h → kör tills dess, eller tills:
- Nästa canary är lika eller bättre än baseline
- Minst en ny task-serie utvidgad + validerad
- Allt committat på branchen
- Kort sammanfattning skrivs: vad gjordes, vad fungerar, vad kvarstår

## Kontext att komma ihåg

- Gemini 3 flash preview är nuvarande LLM
- ~350 tools totalt, ~150 har template-täckning
- `workspace/*` är gitignored utom `workspace/knowledge/` och `workspace/templates/`
- Kit RPC = port 8001, Assist service = port 8000
- Pre-session setup läses från `## Pre-session setup`-sektion i task .md
- Persona-mode = multi-turn via Claude Code subprocess (dyrt), direct-mode = single-shot curl (billigt)
- Judge använder Gemini via `scripts/qa/ground_truth_judge.py` — har robust JSON-parse-fallback
- Fix B/C/D guards finns i `orchestrator.py` — heuristiska, kan false-positive, mät om de introducerar regression
