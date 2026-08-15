## Description: <br>
Build and validate a non-destructive Newton 1.5 hydroelastic-contact companion
USD for the Amber Revan robot's bilateral Psyonic hands. <br>

This skill is intended for project engineering use. <br>

## Owner
HomeHero project <br>

### License/Terms of Use: <br>
Project repository license and the licenses of Newton, Warp, OpenUSD, and the
source robot asset apply. <br>

## Use Case: <br>
Engineers adding distributed-pressure SDF contact to the robot hands while
keeping the canonical Isaac Lab asset and runtime unchanged. <br>

### Deployment Geography for Use: <br>
Project-controlled environments <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [No] <br>

Requires the project `.venv-newton` runtime, OpenUSD bindings, NumPy, and
SciPy. CUDA is optional for final contact generation evidence. <br>

## Known Risks and Mitigations: <br>
Risk: Accidentally replacing the canonical robot USD or contaminating Isaac
Lab's pinned environment. <br>
Mitigation: Enforce distinct source/output paths, source hashing, relative
reference composition, and a separate Newton virtual environment. <br>

Risk: Assuming one-sided SDF configuration creates hydroelastic grasps. <br>
Mitigation: Require hydroelastic SDF collision on both the hand and the
manipulated object. <br>

Risk: A busy GPU prevents the CUDA smoke. <br>
Mitigation: Keep deterministic CPU generation/validation authoritative and
record the GPU gate as deferred until a device is available. <br>

## Reference(s): <br>
- [Workflow Contract](references/contract.md) <br>
- `scripts/make_newton_hydro_hands.py` <br>
- `scripts/smoke_newton_hydro_hands.py` <br>
- `tests/test_newton_hydro_hands.py` <br>

## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands, USD wrapper, JSON manifest] <br>
**Output Format:** [USDA and structured JSON evidence] <br>
**Other Properties Related to Output:** [Canonical input remains unchanged] <br>

## Testing Completed: <br>
**[x] Deterministic contract tests** <br>
**[x] CPU wrapper validation** <br>
**[x] CUDA SDF/contact smoke on GB10** <br>
**[ ] Agent Red-Teaming** <br>

## Skill Version(s): <br>
0.1.0 <br>
