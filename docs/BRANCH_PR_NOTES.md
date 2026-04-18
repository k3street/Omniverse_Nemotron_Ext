# Branch & PR strategy — notes to self

Skrivet 2026-04-18 under session med Claude. Att återkomma till när huvudet orkar.

## Situationen

Branch: `feat/qa-runtime-bundle` (ca 29+ commits framför `origin/master`).

Branchen heter "bundle" men är inte avsiktligt en bundle-PR. Den blev bundle av nödtvång: 65 öppna PRs behövde uppdaterade tool-descriptions, och GitHub låter inte enkelt uppdatera innehåll i en redan-öppen PR utan att stänga/skapa om. Så jag staplade uppdateringar ovanpå ett lokalt sammanslaget bundle istället.

Över tid har branchen också svalt:
- Description-uppdateringar för tools (original-syfte)
- QA-infrastruktur (Phase 12): persona-harness, ground-truth-judge, direct-eval, snapshots
- Chat-service-fixes: anti-ghosting, grounding discipline, template retrieval, Gemini 3 support
- 160+ task-specs + matchande templates
- Isaac Sim 5.x API-fixar (anchor_robot, GetAllDescendants, RevoluteJointAPI m.fl.)
- Verify-before-assert primitives (prim_exists, count_prims_under_path, get_joint_targets)

Dessa tre kategorier — (a) tool-descriptions, (b) QA-infra, (c) kod-fixar — hör inte ihop strategiskt.

## Vad som gör det rörigt

1. **Alla 65 ursprungs-PRs är mina.** De ska landa individuellt för Kimate ska se vad jag bidragit med — det är synlighets-mekanismen, inte bara review.
2. **Description-uppdateringarna sitter fast ovanpå bundle:en.** De kan inte enkelt pushas in i de 65 ursprungs-PRs utan rebase/cherry-pick.
3. **QA-infra och nya fixar är rena add-ons** som inte berör de 65 PRs-kod — de kan gå som separata små PRs från master.
4. **`workspace/templates/*.json` var `.gitignore`:d.** Uppdaterad så de 173 templates kan committas.

## Tre vägar att städa (ej beslutat)

### A. Bundle ersätter alla 65 PRs
- Öppna EN stor PR: `feat/qa-runtime-bundle` → `master`
- När mergad, stäng de 65 ursprungs-PRs som "superseded"
- **Snabbt, men förlorar Kimates synlighet av enskilda bidrag**

### B. De 65 PRs landar individuellt först, sen rest som separata PRs
- Merga de 65 ursprungs-PRs en för en som vanligt
- När master är updated, rebasa `feat/qa-runtime-bundle` mot master (då försvinner allt som redan är mergat)
- Det som återstår = description-updates + QA-infra + nya tasks → splitta i 3-5 små PRs
- **Tar tid (varje rebase kan ha konflikter), men matchar teamets workflow och ger Kimate synligheten**

### C. Hybrid
- Kanske landa de viktigaste 5-10 av 65 PRs individuellt för synlighet
- Resten som en bundle
- QA-infra som egen PR
- **Mittenväg — behöver tänkas igenom**

## "Rebase" — vad det är

`git rebase master` = plocka alla commits på din branch som inte finns i master, applicera dem en-för-en ovanpå senaste master. Slutresultatet: din branch ser ut som om du startade från senaste master och gjorde allt arbete ovanpå. Används för att "hämta in" ändringar från master utan att skapa merge-commits.

Risk: om dina commits och masters commits rör samma filer → konflikter. Du löser dem manuellt under rebase:en.

För ditt fall: när du har mergat några av de 65 PRs till master, rebasa denna branch mot master så försvinner de commits från din branch (eftersom de redan är i master). Det som återstår är det du inte mergat än.

## Hämta Kimates uppdateringar i din branch

När Kimate pushat något till master som du vill ha in i din branch:

```
git fetch origin
git checkout feat/qa-runtime-bundle
git rebase origin/master
```

Om konflikter: git säger vilka filer, du fixar, `git add <fil>`, `git rebase --continue`.

Alternativt enklare om rebase skrämmer:
```
git merge origin/master
```
Det skapar en merge-commit istället för att skriva om historiken. Funkar också.

## Vad Claude gjorde i sessionen (för kontext när du återvänder)

- Commitade alla uncommitted changes från dagen på `feat/qa-runtime-bundle`
- Pushade branchen till GitHub
- Öppnade INGEN PR (avvaktade beslut)
- Uppdaterade `.gitignore` för att tillåta `workspace/templates/`
- Sparade denna doc

## Nästa beslut när du är redo

1. Vill du ha synlighet per bidrag (Kimate ser 65 merges)? → Väg B eller C
2. Vill du bara ha det klart? → Väg A
3. Vet du inte? → Diskutera med Kimate, visa denna fil

Ingen rusning. Inget går förlorat. Commits finns kvar.
