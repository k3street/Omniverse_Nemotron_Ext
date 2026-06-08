# Remote Scale Providers

Isaac Assist should treat remote compute as a capacity backend, not as a
separate product path. The extension and floor-plan UI should keep the same
workflow whether the heavy job runs locally, on Brev, on DGX Spark, or through
Isaac Automator.

## Provider Roles

| Provider | Best use |
| --- | --- |
| `local` | Fast iteration, Isaac Sim GUI, floor-plan UI, small local models. |
| `dgx_spark` | Same-LAN Cosmos/LLM/model-serving node with larger GPU capacity and low latency. |
| `brev` | Temporary NVIDIA GPU capacity for Cosmos, GR00T, Isaac Lab training, SDG, or batch validation. |
| `isaac_automator` | Cloud Isaac Workstation deployment, remote Isaac Sim/Isaac Lab runs, scene replay, regression sweeps, artifact collection. |

## Extension-Facing Contract

The extension should eventually expose this as a small status/control surface:

- selected provider,
- provider health,
- active jobs,
- GPU/memory capacity,
- artifact download links,
- one-click rerun of failed jobs.

When any remote provider is configured, the extension should notify the user
before heavy work starts. The notification should be advisory, not alarming:

```text
Remote scale capacity is configured. This job may run better on DGX Spark,
Brev, or IsaacAutomator. Isaac Assist will ask before launching remote capacity.
```

The first backend surface for this is:

```text
GET /api/v1/settings/scale_notice
GET /api/v1/settings/scale_notice?job_kind=cosmos_reasoner
```

The response includes `configured`, `preferred_provider`, `should_notify`, and
`requires_user_approval`. UI clients should only interrupt the user when
`should_notify=true`; otherwise the notice can live quietly in settings/status.

The extension should not need to know provider-specific launch details. It
should call backend routes such as:

```text
GET  /api/v1/scale/providers
POST /api/v1/scale/jobs
GET  /api/v1/scale/jobs/{job_id}
POST /api/v1/scale/jobs/{job_id}/cancel
POST /api/v1/scale/jobs/{job_id}/collect
```

Those routes can translate into Brev API calls, SSH/rsync Isaac Automator
workflows, or local subprocess execution.

## Cosmos Placement

Cosmos should use this provider layer in two different ways:

```text
Isaac extension / floor-plan UI
        |
Isaac Assist backend
        |
scale provider selector
        |
local Cosmos | DGX Spark Cosmos | Brev Cosmos | NIM endpoint
        |
CosmosSceneObservation
        |
POST /api/v1/canvas/{session_id}/cosmos/propose
```

The `cosmos/propose` route remains stable because it only accepts structured
observations. The provider layer owns the expensive model invocation.

### DGX Spark NIM Runbook

Prefer DGX Spark over Brev for the first Cosmos 3 Reasoner deployment when the
Spark is already on the same network. It avoids cloud lifecycle latency, keeps
scene screenshots and asset names local, and frees the Isaac Sim workstation GPU
for rendering. Brev remains useful for burst experiments, batch synthetic-data
runs, or when Spark is unavailable.

On the Spark:

```bash
git clone <repo-url> Omniverse_Nemotron_Ext
cd Omniverse_Nemotron_Ext

export NGC_API_KEY=nvapi-...

COSMOS_NIM_CACHE=$HOME/nim-cache/cosmos3-reasoner \
  COSMOS_NIM_PORT=8081 \
  NIM_MAX_MODEL_LEN=32768 \
  ./scripts/start_cosmos3_reasoner_nim.sh
```

The first boot downloads roughly tens of GB of model artifacts into the cache.
Watch startup with:

```bash
docker logs -f nvidia-cosmos3-reasoner
```

A healthy startup includes:

```text
Using max model len 32768
The server is up and ready to serve!
Application is ready to receive API requests.
```

From the Isaac Assist workstation:

```bash
curl http://<spark-host-or-ip>:8081/v1/health/ready
curl http://<spark-host-or-ip>:8081/v1/models
```

Then set:

```text
COSMOS3_MODE=local
COSMOS3_REASONER_BASE_URL=http://<spark-host-or-ip>:8081/v1
COSMOS3_REASONER_MODEL=nvidia/cosmos3-nano-reasoner
```

If the Spark firewall is enabled, allow only the workstation to reach the NIM
port:

```bash
sudo ufw allow from <workstation-ip> to any port 8081 proto tcp
```

### Local Workstation Fallback

Cosmos 3 Nano Reasoner NIM can run on a 32 GiB workstation GPU, but it is not a
good default when Isaac Sim must run interactively on the same card. With
`NIM_MAX_MODEL_LEN=32768`, NIM can still reserve almost all RTX 5090 VRAM. Use
`8192` or `16384` for emergency local tests, then stop the container before
launching Isaac Sim:

```bash
docker stop nvidia-cosmos3-reasoner
```

## Isaac Automator Placement

Isaac Automator should own cloud Isaac Workstation lifecycle and remote Isaac
execution. The target upstream is
[`isaac-sim/IsaacAutomator`](https://github.com/isaac-sim/IsaacAutomator),
which deploys Isaac Sim, Isaac Lab, and Isaac Lab Arena workstations to AWS,
GCP, Azure, and Alibaba Cloud.

- deploy/repair/start/stop/destroy cloud Isaac Workstations,
- launch Isaac Sim/Isaac Lab on the remote GPU instance,
- run scene-build scripts generated from approved `LayoutSpec`,
- execute QA sweeps and regression scenarios,
- export logs, screenshots, USDs, videos, and metrics,
- sync artifacts back to `workspace/remote_runs/{job_id}`.

This keeps the local Isaac Sim GUI responsive while heavy validation and
training run elsewhere.

Concrete command mapping:

| Isaac Assist action | Isaac Automator command |
| --- | --- |
| Provision a remote workstation | `./run ./deploy-aws`, `./run ./deploy-gcp`, `./run ./deploy-azure`, or `./run ./deploy-alicloud` |
| Resume paused capacity | `./run ./start <deployment-name>` |
| Pause to save cost | `./run ./stop <deployment-name>` |
| Open remote desktop | `./run ./novnc <deployment-name>` |
| Upload job bundle | `./run ./upload <deployment-name>` |
| Download artifacts | `./run ./download <deployment-name>` |
| Repair deployment | `./run ./repair <deployment-name>` |

IsaacAutomator uses `uploads/` and `results/` as the local exchange folders.
For Isaac Assist, generated job bundles should be staged under a provider-owned
workspace path and mirrored into IsaacAutomator's `uploads/` folder before
calling `upload`. Returned artifacts should be copied from `results/` into
`workspace/remote_runs/{job_id}` with an index file that the extension can show.

The deployment command supports choosing Isaac Sim and Isaac Lab Git refs, which
maps directly onto Isaac Assist's version scope:

```text
--isaacsim v6.0.0-dev2
--isaaclab v3.0.0-beta
--isaaclab-arena release/0.1.1
```

When a remote job is intended to validate Isaac Assist 5.1 behavior, the
provider should select the corresponding Isaac Sim ref and copy only the
`exts/isaac_5.1` harness. For Isaac Sim 6.0 / Isaac Lab 3 jobs, it should copy
`exts/isaac_6.0` and the matching runtime profile.

## Brev Placement

Brev is the elastic provider for temporary NVIDIA GPU capacity:

- Cosmos 3 Reasoner/Generator experiments,
- GR00T/OpenVLA fine-tuning,
- Isaac Lab training,
- SDG batches,
- long QA sweeps that would tie up the workstation.

The backend should persist enough provider state to resume or collect a run
after a service restart:

```json
{
  "job_id": "scale_20260605_001",
  "provider": "brev",
  "kind": "cosmos_reasoner",
  "status": "running",
  "endpoint": "https://...",
  "artifacts": [],
  "created_at": "2026-06-05T00:00:00Z"
}
```

## Implementation Phases

1. Add read-only provider settings and health checks.
2. Add local/DGX Spark Cosmos endpoint invocation.
3. Add Isaac Automator artifact collection using the existing deployment-state
   pattern in training handlers.
4. Add Brev job launch/teardown behind the same job API.
5. Add extension UI controls for provider selection and job monitoring.
