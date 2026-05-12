# Mandate Boundary — RL strategic content stays out of IA

> Phase 17b — companion to the spec's "Scope discipline" clause
> (`specs/IA_FULL_SPEC_2026-05-10.md`, lines 24-46) and the CI scanner
> at `scripts/lint_mandate.py`.

Status: enforced — every commit on `service/`, `exts/`, `web/`,
`scripts/`, or any tracked `*.md` under `docs/specs/` runs through the
scanner. Violations block the pre-commit hook and the CI gate.

---

## 1. The principle

Robotics Lab (RL) is the user's *separate* intellectual property. IA
must never depend on RL's strategic-content layer — the causal-scenario
brain, the ABOM product topology, the make-or-buy decision graph, the
IFC export, the NSGA-II layout optimiser, the manufacturing-process
classifier. IA stands on its own multimodal canvas, workflow lifecycle,
deterministic-PM swarm, governance, MCP wire format, and the 404-tool
Pydantic IR.

The constraint matters because RL and IA share a developer and a
workstation, not a product. Phrases, identifiers, and design fragments
leak across folders if the only barrier is author discipline. The spec
records two slips in rev. 2 alone — both caught manually, both
expensive to unwind. Phase 17b moves the boundary from social to
mechanical: a scanner that rejects RL strategic tokens in IA code at
pre-commit time.

## 2. The forbidden list

The canonical set lives in `scripts/lint_mandate.py:FORBIDDEN_TERMS`.
As of the initial commit:

| Token | RL concept it names |
| --- | --- |
| `ABOM`, `ABOMState`, `ABOMNode`, `ABOMEdge` | RL's product-topology graph (Assemblies / Bills / Op Modes). |
| `NSGA-II`, `NSGA-III`, `NSGA2`, `nsga2` | RL's multi-objective evolutionary layout optimiser. |
| `make_or_buy`, `make-or-buy`, `MakeOrBuyDecision` | RL's procurement-vs-fabrication decision node. |
| `flip_point` | RL's break-even volume in the make-or-buy graph. |
| `operating_mode` | RL's ABOM operating-mode field. |
| `ScenarioEngine` | RL's L3 strategic-brain causal-scenarios driver. |
| `SiteProfile` | RL's site-level macro-economic input bundle. |
| `macro_env`, `Nord Pool`, `nord_pool` | RL's macroeconomic data sources. |
| `MachinePosition` | RL's layout-coordinate type (IA uses `LayoutSpec`). |
| `fac_get_machine` | RL's brain MCP tool name. |
| `WeightSpec` | RL's frozen critic-weights container. |

### Deliberate exception: `MathCritic`

IA's Phase 45 code-quality scorer is also named `MathCritic`. The
namespace collision is intentional — the spec calls it out in the
`FORBIDDEN_TERMS` source comment, and `tests/test_lint_mandate.py`
holds a guard test (`test_mathcritic_is_not_forbidden`) so any future
addition of `MathCritic` to the set fails CI immediately.

## 3. Detection: word-boundary regex per term

The scanner compiles each term to a regex of the form

```
(?<![A-Za-z0-9_]) <escaped-term> (?![A-Za-z0-9_])
```

i.e. the character on either side must NOT be a Python identifier
character. This is stricter than `\b` (which mishandles hyphens) and
correctly treats hyphens and spaces as boundaries. Consequences:

- `from rl_lib import ABOMState` flags `ABOMState`.
- `class MyABOMStateX:` does NOT flag — `ABOMState` is embedded in a
  longer identifier.
- `# layout uses NSGA-II` flags `NSGA-II`.
- The pattern set is sorted longest-first; a `ABOMState` hit consumes
  the span and prevents a redundant `ABOM` match on the same line.

The scanner is line-oriented. Multi-line constructs (docstrings,
multi-line dictionary literals) are scanned line by line; an RL term
buried inside a multi-line string still triggers a violation.

## 4. The `allow-rl-term` escape hatch

Some legitimate cross-references exist — glossary entries, porting
notes, deprecation messages. Each may carry an inline justification
comment on the *same line* as the term:

```python
state = previous_state  # allow-rl-term: porting wave 6 §3 shape only
```

```markdown
We previously discussed RL's ABOM topology.
<!-- allow-rl-term: glossary cross-reference -->
```

The scanner accepts either:

- Python / shell `# allow-rl-term: <reason>`
- HTML / Markdown `<!-- allow-rl-term: <reason> -->`

The matcher requires at least one non-whitespace character after the
colon — a bare `# allow-rl-term:` does not silence the rule.

Allowlist entries are reviewed quarterly per Phase 96. Stale entries
(>90 days, judged against `git log -1 --format=%cI <file>`) raise a
warning during that audit. The reviewer either renews the
justification, replaces the wording, or removes the reference.

## 5. Scope of scanning

In scope:

- `service/**`
- `exts/**`
- `web/**`
- `scripts/**`
- `docs/specs/**/*.md`

Out of scope (silently skipped even if passed on the CLI):

- `specs/IA_FULL_SPEC_2026-05-10.md` — the spec itself enumerates the
  forbidden terms in its scope-discipline clause.
- `scripts/lint_mandate.py` — the scanner holds `FORBIDDEN_TERMS` as
  data.
- `.claude/worktrees/**` — agent scratch worktrees, not real source.
- Everything else (`tests/`, `data/`, `README.md`, repo root) — the
  mandate is about *IA shipping code*, not internal tooling or test
  fixtures.

File-extension filter inside the code roots:

`.py .pyi .md .rst .txt .json .yaml .yml .toml .ts .tsx .js .jsx .mjs
.cjs .html .css .sh .bash .cfg .ini`

Binaries, build artefacts, and unfamiliar extensions are ignored.

## 6. Extending `FORBIDDEN_TERMS`

When RL ships a new strategic-content concept that risks bleeding into
IA, add the token to the set. The process:

1. Open a PR that edits `scripts/lint_mandate.py` and adds the new
   entry. Each entry carries a one-line comment naming the RL concept.
2. Update the table in §2 of this document.
3. Add a `test_canonical_terms_present` assertion in
   `tests/test_lint_mandate.py` for the new entry.
4. Run `python scripts/lint_mandate.py` against `HEAD` and triage
   every new violation: either it is a real RL leak (delete) or a
   legitimate reference (add `# allow-rl-term: <reason>`).
5. Spec-level reviewer sign-off on the merged PR — the boundary is the
   load-bearing property of the rev. 2 contract; widening it
   unilaterally would invert the intent.

The reverse — removing a term that IA deliberately reuses — follows
the same path. `MathCritic` is the working example.

## 7. Invocation

Pre-commit hook (added in Phase 17 once `.pre-commit-config.yaml`
exists):

```yaml
- id: lint-mandate
  name: Mandate-guard (RL/IA scope boundary)
  entry: python scripts/lint_mandate.py
  language: system
  pass_filenames: true
  files: '^(service|exts|web|scripts|docs/specs)/'
```

Ad hoc, one file:

```
$ python scripts/lint_mandate.py service/isaac_assist_service/main.py
```

Ad hoc, whole tree:

```
$ python scripts/lint_mandate.py
ERROR: mandate violation at docs/specs/2026-05-09-...md:5 — term "ABOM" appears
       in IA code. IA must not depend on RL strategic content.
       To allow this token, add a comment on the same line:
         # allow-rl-term: <one-line justification>
       and route the change through manual spec review.
```

Exit code is 0 on clean, 1 on any violation, 2 on argparse error.

## 8. What this doc is not

This is not a *roadmap*. It does not catalogue which IA features were
inspired by RL designs. It does not declare RL "off-limits" to thought
or discussion. It enforces one specific rule: IA's shipping code must
not import, name, or assume RL's strategic-content vocabulary.
Anything beyond that — porting an *idea*, replicating a *shape*,
sharing a *test fixture* — is a normal design decision documented in
the relevant phase or wave.

## 9. References

- Spec § "Scope discipline" — `specs/IA_FULL_SPEC_2026-05-10.md`, lines 24-46.
- Phase 17b body — `specs/IA_FULL_SPEC_2026-05-10.md:1981-2060`.
- Phase 17 (pre-commit infrastructure) — same spec, lines just above 1981.
- Phase 96 (quarterly tool/audit cadence) — same spec, search "Phase 96".
- Scanner source — `scripts/lint_mandate.py`.
- Test suite — `tests/test_lint_mandate.py`.
