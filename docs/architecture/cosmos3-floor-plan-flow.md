# Cosmos 3 to Floor-Plan Flow

This integration treats NVIDIA Cosmos 3 as a world-understanding and world-
generation layer that feeds Isaac Assist's deterministic scene pipeline. Cosmos
does not directly mutate Isaac Sim. It proposes structured scene intent that the
floor-plan canvas can review, correct, and commit.

## Roles

| Layer | Responsibility |
| --- | --- |
| Cosmos 3 Reasoner | Interpret photos, screenshots, rendered frames, or prompts into objects, roles, spatial relations, and physical plausibility notes. |
| Cosmos 3 Generator | Create reference images/videos, future-state rollouts, and synthetic-data variants for review or training. |
| Floor-plan canvas | Human-checkable 2D/semantic edit surface for placement, roles, constraints, and asset choices. |
| Isaac Assist backend | Validates `LayoutSpec`, resolves canonical templates, and converts approved specs into scene blueprints/tool calls. |
| Isaac Sim harness | Applies version-specific USD, physics, robot, material, and controller operations for Isaac Sim 5.1 or 6.0. |

## Preferred Runtime Path

```text
photo / screenshot / prompt / video frame
        |
Cosmos 3 Reasoner
        |
CosmosSceneObservation JSON
        |
POST /api/v1/canvas/{session_id}/cosmos/propose
        |
LayoutSpec proposal
        |
floor-plan UI review + correction
        |
canvas commit/build
        |
scenario campaign plan/materialize
        |
Isaac Sim 5.1 or 6.0 harness
```

The first committed adapter lives in
`service/isaac_assist_service/multimodal/cosmos3_adapter.py`. It accepts a
structured `CosmosSceneObservation` and converts it into a `LayoutSpec` with:

- normalized object classes from the local object palette,
- role bindings such as `primary_robot`, `workpiece`, and `target`,
- image-derived or world-space 2D positions,
- relation constraints preserved as `cosmos_relation` entries,
- provenance metadata marking the source as `provider=cosmos3`.

The backend route is:

```text
POST /api/v1/canvas/{session_id}/cosmos/propose
```

It uses the same compare-and-swap revision store as the normal canvas patch
route, so Cosmos proposals behave like ordinary floor-plan edits.

For asset placement, Cosmos Reasoner should be used before campaign
materialization. It proposes object classes, asset hints, support/containment
relations, and physical plausibility notes from prompts, photos, renders, or the
live viewport. The floor-plan UI then acts as the correction surface for those
probabilistic guesses. Once the user or agent accepts the placement semantics,
the deterministic campaign planner expands the reviewed `LayoutSpec` into
variant jobs and the materializer writes local `.usda` stages plus setup scripts.
This keeps "Cosmos reasons about where things are" separate from "Isaac Sim
executes exact USD/API mutations."

The first runtime route is:

```text
POST /api/v1/canvas/{session_id}/cosmos/observe
POST /api/v1/canvas/{session_id}/cosmos/observe_viewport
POST /api/v1/canvas/{session_id}/cosmos/generate
```

It accepts `prompt`, optional `image_base64`, `mime_type`, and `input_kind`,
calls the configured OpenAI-compatible Cosmos 3 Reasoner endpoint, parses a
`CosmosSceneObservation`, then forwards through `cosmos/propose`.
`cosmos/observe_viewport` first captures the active Isaac Sim viewport through
Kit RPC `/capture`, then runs the same observation/proposal flow.

`cosmos/generate` is the Generator route. It calls a vLLM-Omni-style Cosmos 3
Omni endpoint and persists the output under
`workspace/multimodal/cosmos3_generations/` without mutating Isaac Sim. It is
for visual references, future-state rollout clips, synthetic-data seed clips,
and action-policy experiments. Supported `mode` values:

- `text_to_image`
- `text_to_video`
- `image_to_video`
- `video_to_video`
- `text_to_video_with_sound`
- `image_to_video_with_sound`
- `video_to_video_with_sound`
- `policy`
- `inverse_dynamics`
- `forward_dynamics`

The action modes pass Cosmos embodiment controls through `extra_params`:
`action_mode`, `domain_name`, `raw_action_dim`, `action_chunk_size`, and
`action_path` when provided. `policy` accepts either `image_base64` or
`video_base64` as observation context. Treat the returned actions as proposed
policy chunks for review/evaluation; do not stream them directly to hardware
without the existing controller, safety, and sim-validation layers.

Configuration:

```text
COSMOS3_MODE=local
COSMOS3_REASONER_BASE_URL=http://dgx-spark.local:8081/v1
COSMOS3_REASONER_MODEL=nvidia/cosmos3-nano-reasoner
COSMOS3_GENERATOR_BASE_URL=http://dgx-spark.local:8082/v1
COSMOS3_GENERATOR_MODEL=nvidia/Cosmos3-Nano
COSMOS3_API_KEY=...
NIM_MAX_MODEL_LEN=32768
```

Gemini Robotics-ER can be enabled as the cloud backup for this exact contract:

```text
GEMINI_API_KEY=...
GEMINI_ROBOTICS_ER_FALLBACK=true
GEMINI_ROBOTICS_ER_MODEL=gemini-robotics-er-1.6-preview
```

The fallback is deliberately provider-compatible with Cosmos output. It may
infer objects and relations from text/images, but it still returns
`CosmosSceneObservation` and still routes through the floor-plan review/build
pipeline before Isaac Sim is mutated.

When using NVIDIA Cosmos 3 Reasoner NIM, keep the NIM service on a GPU that is
not also responsible for the interactive Isaac Sim viewport whenever possible.
Cosmos 3 Nano Reasoner can run on a local RTX 5090, but the NIM default context
window (`262144`) requires more KV cache than a 32 GiB workstation GPU can
provide after the model is loaded. Set `NIM_MAX_MODEL_LEN=32768` for the first
stable local/Spark deployment; reduce to `16384` or `8192` if Isaac Sim must
share the same GPU. On a same-LAN DGX Spark, expose the NIM endpoint on
`8081` and point `COSMOS3_REASONER_BASE_URL` at that host so the Isaac Sim
workstation keeps its VRAM for rendering and live scene mutation.

The repo includes a helper for starting the NIM container on a local machine or
DGX Spark:

```bash
export NGC_API_KEY=nvapi-...
COSMOS_NIM_CACHE=$HOME/nim-cache/cosmos3-reasoner \
  COSMOS_NIM_PORT=8081 \
  NIM_MAX_MODEL_LEN=32768 \
  ./scripts/start_cosmos3_reasoner_nim.sh
```

Verify from the Isaac Assist workstation:

```bash
curl http://dgx-spark.local:8081/v1/health/ready
curl http://dgx-spark.local:8081/v1/models
```

Start a Cosmos 3 Generator server for image/video/action output with:

```bash
COSMOS_GENERATOR_PORT=8082 \
COSMOS_GENERATOR_MODEL=nvidia/Cosmos3-Nano \
  ./scripts/start_cosmos3_generator_vllm_omni.sh
```

Smoke-test the backend route:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/canvas/demo/cosmos/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "mode": "text_to_video",
    "prompt": "a gripper grabs a red cube and slowly lifts it",
    "size": "320x192",
    "num_frames": 24,
    "fps": 12
  }'
```

## Why Not Direct Cosmos-to-Isaac?

Cosmos is probabilistic. Isaac Sim scene execution needs exact, version-aware
operations: prim paths, asset references, transforms, physics APIs, material
schemas, controllers, robot frames, and task roles. Keeping `LayoutSpec` in the
middle gives us a visible correction point before any USD mutation happens.

That also preserves version coexistence:

- Cosmos integration is version-agnostic.
- Floor-plan and backend validation are version-agnostic.
- The build route resolves reviewed object classes to USD references or
  primitive fallbacks, then returns generated Kit code with `dry_run=true`.
- Live mutation is opt-in with `dry_run=false`, so the final execution harness
  can still select Isaac Sim 5.1 or Isaac Sim 6.0 code deliberately.

## Suggested Cosmos Reasoner Prompt Contract

Ask the Reasoner to return JSON matching this shape:

```json
{
  "input_kind": "screenshot",
  "prompt": "Recreate this pick-and-place cell",
  "summary": "A robot arm, table, cube, and target bin are visible.",
  "pattern_hint": "pick_place",
  "workspace_size_xy_m": [4.0, 4.0],
  "confidence": 0.75,
  "objects": [
    {
      "label": "Franka robot arm",
      "role": "robot",
      "asset_hint": "franka_panda",
      "confidence": 0.9,
      "bbox_xyxy_norm": [0.1, 0.2, 0.3, 0.7]
    },
    {
      "label": "red cube",
      "role": "pick",
      "confidence": 0.82,
      "position_xy_m": [0.4, -0.2],
      "color": "#ff0000"
    }
  ],
  "relations": [
    {
      "subject": "red cube",
      "predicate": "on",
      "object": "table",
      "confidence": 0.7
    }
  ]
}
```

## Follow-On Implementation Phases

1. Done: add a Cosmos runtime provider that calls a local/NIM/vLLM Cosmos 3 Reasoner
   service and returns `CosmosSceneObservation`.
2. Done: add floor-plan UI import action for screenshot/photo proposals.
3. Done: add asset resolver feedback in the properties panel so users can swap
   Cosmos guesses for available Isaac assets, and carry reviewed classes into
   build-time USD reference / primitive fallback resolution.
4. Done: add viewport screenshot capture from the Isaac extension and send it through
   the proposal route.
5. Done: add Gemini Robotics-ER as a cloud fallback for the Cosmos scene
   observation contract.
6. Done: add generator-mode backend workflows for visual references,
   synthetic-data clips, sound-enabled video, and action rollout artifacts.
7. Add floor-plan UI controls for reviewing and comparing generated clips
   alongside approved `LayoutSpec` scenarios.

For larger jobs, route Cosmos and Isaac validation through the shared remote
capacity contract in [Remote Scale Providers](remote-scale-providers.md). That
keeps the floor-plan proposal route stable while the runtime provider can be
local, DGX Spark, Brev, IsaacAutomator, vLLM, or NIM.
