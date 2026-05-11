# Plan: nästa session — 7 PRs till Kimate först, sen Fas A + B

Skriven 2026-05-05. Återgå hit efter konversations-kompaktering.

**Ordning**: PRs FÖRST (skicka existing commits till Kimate), sedan Fas A + B (nytt arbete som blir en framtida PR-batch).

---

## Status just nu (lokal lägesrapport)

- **Branch**: `feat/qa-runtime-bundle` på lokal + `anton/`-fork. HEAD = `599b41c`.
- **Origin/master**: separat — 195 commits efter senaste sync, men de är nyligen från Kimate (Phase 1 RL+VLA, multi-provider vision, etc).
- **Lokala commits ej i origin/master**: 195 (verifierat 2026-05-05).
- **Verifierade templates**: 62 i ChromaDB-collection `isaac_assist_templates`.
- **Kit RPC** (port 8001) + **Assist service** (port 8000): bägge igång.

### Pågående parallell session
- En annan session arbetar på UI / orchestrator / routes / session_trace / chat-tools/descriptions.
- Vi rör INTE de filerna förrän de pushar sina ändringar.

---

---

## Fas C (FÖRST) — 7 stacked PRs av existing commits

**Körs först.** Skickar all aktuell `feat/qa-runtime-bundle`-arbete (195+ commits) till Kimate som 7 stacked PRs. Detaljer längre ned i dokumentet under "Fas C — 7 stacked PRs". Hoppa till den sektionen när du startar.

Efter att alla 7 PRs är öppnade hos Kimate, fortsätt med Fas A + B nedan. Det nya arbetet (T4-volym, CW-50, tool-audit) blir en framtida PR-batch.

---

## Fas A — Verified-count expansion

**Körs efter Fas C** (PRs är skickade).

**A1**: Sync `anton/master` ← `origin/master` (fast-forward, alltid säkert).

**A2**: Triple-run T4-canary för stable-pass.
- Kör `python -m scripts.qa.direct_eval --tasks T4-01,T4-02,T4-03,T4-04,T4-05` × 2 mer gånger
- Markera triple-perfect i ChromaDB (cf. `scripts/qa/mark_verified.py`)
- Förväntat: +3-4 verified

**A3**: Skriv `T4-06.md` .. `T4-15.md` — 10 nya high-level intent-tasks. Föreslagna scenarier:
- T4-06: Nova Carter warehouse navigation
- T4-07: RL training för humanoid locomotion
- T4-08: Quality-inspection station med kameror
- T4-09: Multi-robot pick-place (2 Frankas)
- T4-10: Digital twin av befintlig fabriksgolv
- T4-11: Replicator SDG med domain randomization
- T4-12: Sensor calibration scene
- T4-13: Teleoperation training environment
- T4-14: ROS2 simulation bridge end-to-end
- T4-15: Benchmark scene för controller comparison

Varje task med `## Scripted followups` + `## Pre-session setup`. Format: se `T4-01.md..T4-05.md`.

**A4**: Skriv `CW-31.md` .. `CW-50.md` — 20 fler common-workflow-tasks. Fokusera på låg-hängande frukter:
- get/set-attribute-varianter
- list-something-tasks (lights, joints, materials)
- Per-prim-attribut-readers
- enkla scen-mods

Format: se `CW-01.md..CW-30.md`.

**A5**: Kör samtliga tasks (T4-01..15 + CW-01..50) en gång. Triple-pass-mätning kommer i en framtida session.

**A6**: Markera alla deterministically-passade som verified med `mark_verified.py`.

**A7**: Logga slutsiffran i `workspace/qa_runs/canary_trend.log`.

---

## Fas B — Tool-audit Phase A (silent failures)

**B1**: Skapa `scripts/qa/audit_data_handlers.py`.
- Importerar `DATA_HANDLERS` från `tool_executor`
- För varje handler: bygg sensible default-args från `tool_schema`
- Anropa handler(args), klassificera utfall:
  - Returnerar `{error: ...}` → SOFT FAIL
  - Raise:ar exception → HARD FAIL
  - Returnerar OK → PASS
- Output: `workspace/qa_runs/tool_audit_<date>.jsonl`

**B2**: Kör mot live Kit + service.

**B3**: Kategorisera failures:
- Real bugs (kräver fix)
- Stage-state-dependent (förväntat fail utan setup — markera så audit-script ignorerar)
- Deprecated (bör avregistreras)

**B4**: Output-rapport till `docs/qa/tool_audit_phase_a.md`.

**B5**: Inga fixes i denna fas — bara katalogisering. Fixes kommer i framtida sessions baserat på data.

---

## Fas C — 7 stacked PRs till Kimate (FÖRSTA STEG)

Skickas FÖRE Fas A + B. Totalen är ~213 commits (195 + 18 från denna session). Andra sessionens 3 live-progress-UI-commits ingår också.

### Strategi: strikt mekanisk chunking

Bryt commits 1..N i 7 lika chunks (~30-32 commits per PR). Inte tema-medveten — strikt by chunk.

### Stacked branches

```
master
  ↑
  PR #1: pr/chunk-1 (commits 1-32 från origin/master)
         ↑
         PR #2: pr/chunk-2 (33-64, base=pr/chunk-1)
                ↑
                PR #3: pr/chunk-3 (65-96, base=pr/chunk-2)
                       ↑
                       PR #4: pr/chunk-4 (97-128, base=pr/chunk-3)
                              ↑
                              PR #5: pr/chunk-5 (129-160, base=pr/chunk-4)
                                     ↑
                                     PR #6: pr/chunk-6 (161-192, base=pr/chunk-5)
                                            ↑
                                            PR #7: pr/chunk-7 (193-end, base=pr/chunk-6)
```

Bevarar individuella commits (inte squash). Stacked = varje PR visar bara sin chunks-diff (30 commits) i GitHub UI.

### Steg

```
# 0. Säkra läget
git fetch anton origin
git checkout feat/qa-runtime-bundle  # local HEAD med alla commits

# 1. Räkna och slica
N=$(git log --oneline origin/master..HEAD | wc -l)
chunk_size=$(( (N + 6) / 7 ))  # ceil-divide

# 2. Skapa 7 branches stacked
git log --reverse --format='%H' origin/master..HEAD > /tmp/all_commits.txt

base="origin/master"
for i in 1 2 3 4 5 6 7; do
    start=$(( (i-1) * chunk_size + 1 ))
    end=$(( i * chunk_size ))
    [ "$i" = "7" ] && end="$N"
    
    branch="pr/chunk-$i"
    git checkout -b "$branch" "$base"
    
    sed -n "${start},${end}p" /tmp/all_commits.txt | while read sha; do
        git cherry-pick "$sha"
    done
    
    git push anton "$branch"
    base="$branch"
done

# 3. Öppna 7 PRs via gh
gh pr create --base master         --head antonbj3:pr/chunk-1 \
  --title "PR 1/7: chunk 1 (commits 1-32)" \
  --body "Strikt mekanisk chunk-1 av 7 från feat/qa-runtime-bundle.
          Stacked PR — review i ordning #1 → #7.
          See docs/qa/PLAN_NEXT_STEPS.md."

gh pr create --base pr/chunk-1 --head antonbj3:pr/chunk-2 \
  --title "PR 2/7: chunk 2 (commits 33-64)" \
  --body "..."

# ... osv för #3..7
```

### Konflikt-hantering vid cherry-pick

Sannolikt är 195+ kronologiska commits i sin naturliga ordning utan konflikter. Om en cherry-pick failar:
- `git cherry-pick --abort`
- Granska konflikt manuellt
- Fixa, `git cherry-pick --continue`

---

## Centrala kontext-fakta att veta efter kompaktering

### Vad lokala commits innehåller (sammanfattning)

| Tema | Ungefärligt antal commits | Lokation i tidslinjen |
|---|---|---|
| Bundle/Phase 12 fundamenten | ~30 | tidigt |
| Tool honesty/silent-success-fixes | ~30-40 | mid-april |
| Conveyor + pick-place + cuRobo dive | ~30 | 19-20 april |
| Brainstorm + spec-first-revert | ~10 | 19-20 april |
| Tool description polish | ~30 | mid-april |
| Live-progress UI (annan session) | ~3 | senast (5 maj) |
| Denna sessions strategic-brain + cites | ~10 | 4-5 maj |
| Denna sessions tool-fixes + CW + T4 | ~10 | 4-5 maj |

### Strategic-brain-arkitektur (denna session)

Implementerat i tre faser:
- **Fas 1**: complexity-classifier i `intent_router.py` (single/multi/complex)
- **Fas 2**: `negotiator.py` — intent disambiguation gate (inte plumbing)
- **Fas 3**: `spec_generator.py` + `gap_analyzer.py` — structured execution plan

Plus `setup_helpers.py` auto-prepended till varje pre-session-setup. `import_urdf_safe()` med fallback till manual placeholder articulation.

### Tool-fixes denna session

- `get_bounding_box`: Tokens.default → robust getattr-fallback
- `apply_force`: 4-path fallback chain (IPhysxSimulation → physicsUtils → tensors → velocity-impulse)
- `solve_ik`: cuRobo first (works without Kit Articulation init), Lula fallback

### Cites tillagda denna session

I `service/isaac_assist_service/knowledge/deprecations.jsonl`:
- `asset_path_discovery` — sök filer innan agenten frågar om path
- `articulation_action_4_2_change` — ArticulationAction kwarg-rename på 4.2
- `execute_when_explicit` — agenten ska bygga med explicita siffror, inte fråga
- `verify_before_claiming` — kontrollera scene_state innan beskrivning
- `apply_dont_describe` — execute via tools, inte bara beskriv

Plus IDF-rankning i `deprecations_index.py` (top_k 3→5).

### Canary-status

| Canary | Pass-rate |
|---|---|
| Phase 12 broad (51 tasks, baseline) | 17.6% |
| Phase 12 broad (efter strategic-brain + setups + cites) | 31.4% |
| CW common-workflow (30 tasks) | 90% (27/30) — 21 stable + 6 retry |
| T4 high-level intent (5 tasks, multi-turn) | 80% (4/5) |

---

## Sammanfattningskommando för kompaktering

När konversationen kompakteras, behöll:
1. Status-tabellen ovan
2. Fas A + B + C-stegen
3. Centrala kontext-fakta
4. Verified-template-count-tracking

Resten kan strippas. Den totala arbetsmängden är operativ — kommandona ska köras i ordning, inte diskuteras om.
