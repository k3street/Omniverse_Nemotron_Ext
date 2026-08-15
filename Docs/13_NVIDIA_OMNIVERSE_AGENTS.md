# NVIDIA Omniverse agents and skills integration

This project uses NVIDIA's agent tooling as isolated sidecars. It does not
replace the existing Isaac Assist tool router, Kit RPC bridge, review queue, or
Newton environment.

## What is active

### NVIDIA USD Validation

`usd-validation-nvidia` 1.21.0 is pinned in
`requirements-omniverse-tools.txt` and installed into its own
`.venv-omniverse-tools` environment:

```bash
./scripts/setup_omniverse_tools.sh
```

On Linux ARM64, NVIDIA's `usd-core` extra has no wheel. The sidecar therefore
uses the pinned `usd-exchange` 2.3.0 package for compatible OpenUSD 25.05
`pxr` bindings; this is still isolated from the Isaac and Newton environments.

The Stage Analyzer exposes an opt-in `nvidia_usd_validation` pack. It runs the
official blocking validation API in a subprocess with NVIDIA's JSON report
format, accepts exit status 1 as a normal "issues found" result, and converts
NVIDIA issues into `ValidationFinding` records.
It is not part of the default pack set because it opens the saved root layer
again with its own OpenUSD runtime.

Use it from the chat tool with:

```json
{"packs": ["nvidia_usd_validation"]}
```

or combine it with any built-in pack names. Anonymous stages must be saved
first. The executable is resolved in this order:

1. `NVIDIA_USD_VALIDATOR` (an explicit upstream-compatible executable);
2. `.venv-omniverse-tools/bin/python` plus the project synchronous bridge;
3. `nvidia_usd_validate` on `PATH`.

The bridge exists because the upstream CLI's asynchronous single-file path
was observed to stay at 0% with the ARM64 `usd-exchange` provider, including
for a minimal stage. NVIDIA's documented blocking `ValidationEngine.validate`
path completes and uses the same rules and JSON encoder.

Interactive validation defaults to the bounded `robotics` ruleset: stage and
layer metadata, material paths/bindings, rigid bodies, colliders, physics
joints, articulations, and mass. It disables variant and instance-proxy
expansion. Set `NVIDIA_USD_VALIDATION_RULESET=full` for an offline exhaustive
pass; the full default NVIDIA rule set exceeded three minutes on this composed
robot. `NVIDIA_USD_VALIDATION_TIMEOUT` defaults to 60 seconds.

Observed on the canonical Amber Revan/Psyonic USDA: the robotics ruleset
completed in about one second and produced one `UsdAsciiPerformanceChecker`
failure—138 large array-valued attributes are stored in ASCII and would load
faster from crate/USDC. This is a derivative/packaging recommendation only;
the integration does not rewrite the canonical robot.

When the sidecar is installed, `scripts/ingest_asset.py` automatically stores
the normalized report at `validation.nvidia_usd` in each final review-queue
record. Set `NVIDIA_USD_VALIDATION_ON_INGEST=0` to disable it, or `=1` to
require a visible "backend unavailable" finding when the sidecar is absent.

### Agent skills

The following NVIDIA skills are installed unchanged in the user's Codex skill
directory and pinned in `config/nvidia_omniverse_integrations.json`:

- `omniverse-cad-to-simready` 0.2.0;
- `omniverse-usd-performance-tuning` 0.1.0;
- `omniverse-realtime-viewer` 0.1.0.

Restart the agent session after first installation so they are discovered.
The realtime-viewer skill is available as guidance only; no GPU renderer or
stream has been started.

The project-owned `.agents/skills/newton-hydroelastic-hands` skill governs the
existing robot-specific wrapper generator. It enforces immutable canonical
USD input, a separate Newton 1.5 environment, bilateral SDF contact, CPU-first
gates, and an explicit GPU-deferred state.

## NVIDIA Kit/USD/Isaac MCP servers

The official `kit-usd-agents` project provides four Streamable HTTP servers:

| Server | Endpoint | Tools |
|---|---|---:|
| OmniUI | `http://127.0.0.1:9901/mcp` | 10 |
| Kit | `http://127.0.0.1:9902/mcp` | 12 |
| USD Code | `http://127.0.0.1:9903/mcp` | 7 |
| Isaac Sim | `http://127.0.0.1:9904/mcp` | 5 |

The checked-in client template is
`config/mcp/nvidia-kit-usd-agents.mcp.json`. The pinned server source is
`kit-usd-agents` 1.3.0 at the commit recorded in the integration lock file.

Use NVIDIA's cloud embedding/reranking mode on this host. It needs Docker,
Git LFS, and `NVIDIA_API_KEY`, but no local GPU. Clone the pinned repository,
copy `.env.nvidia.example` values to the server's untracked
`source/mcp/.env`, and follow its `QUICKSTART.md` or
`source/mcp/docker-compose.ngc.yaml`. Do not commit the key.

The servers are intentionally configured but disabled until a key is present.
Do not start the local-NIM compose file on a busy host: that advanced mode
requires local GPU capacity. The server sidecars are developer knowledge/RAG
tools; the existing `mcp/isaac_spatial_mcp.py` remains the runtime spatial and
live-Kit bridge.

## Boundaries

- NVIDIA's Physics/Joint/Validation Content Agents can help triage and author
  standard USD/PhysX properties, but they are not a Newton hydroelastic
  contact solver and do not replace the project wrapper generator.
- CAD-to-SimReady may be used in validation-only mode without its GPU property
  assignment stage.
- Performance tuning must preserve articulation, physics, joint, sensor, and
  variant semantics; follow the upstream skill's approval gates.
- Viewer work uses `ovrtx`/`ovstream` when resumed. Do not replace it with a
  browser WebGL USD renderer merely because the GPU is temporarily busy.
