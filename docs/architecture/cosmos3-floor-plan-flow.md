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

The first runtime route is:

```text
POST /api/v1/canvas/{session_id}/cosmos/observe
POST /api/v1/canvas/{session_id}/cosmos/observe_viewport
```

It accepts `prompt`, optional `image_base64`, `mime_type`, and `input_kind`,
calls the configured OpenAI-compatible Cosmos 3 Reasoner endpoint, parses a
`CosmosSceneObservation`, then forwards through `cosmos/propose`.
`cosmos/observe_viewport` first captures the active Isaac Sim viewport through
Kit RPC `/capture`, then runs the same observation/proposal flow.

Configuration:

```text
COSMOS3_MODE=local
COSMOS3_REASONER_BASE_URL=http://dgx-spark.local:8081/v1
COSMOS3_REASONER_MODEL=Cosmos3-Nano
COSMOS3_API_KEY=...
```

## Why Not Direct Cosmos-to-Isaac?

Cosmos is probabilistic. Isaac Sim scene execution needs exact, version-aware
operations: prim paths, asset references, transforms, physics APIs, material
schemas, controllers, robot frames, and task roles. Keeping `LayoutSpec` in the
middle gives us a visible correction point before any USD mutation happens.

That also preserves version coexistence:

- Cosmos integration is version-agnostic.
- Floor-plan and backend validation are version-agnostic.
- Only the final execution harness selects Isaac Sim 5.1 or Isaac Sim 6.0 code.

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

1. Add a Cosmos runtime provider that calls a local/NIM/vLLM Cosmos 3 Reasoner
   service and returns `CosmosSceneObservation`.
2. Add floor-plan UI import action for screenshot/photo proposals.
3. Add asset resolver feedback in the properties panel so users can swap
   Cosmos guesses for available Isaac assets.
4. Add viewport screenshot capture from the Isaac extension and send it through
   the proposal route.
5. Add generator-mode workflows for visual references and synthetic-data
   augmentation after a `LayoutSpec` is approved.

For larger jobs, route Cosmos and Isaac validation through the shared remote
capacity contract in [Remote Scale Providers](remote-scale-providers.md). That
keeps the floor-plan proposal route stable while the runtime provider can be
local, DGX Spark, Brev, IsaacAutomator, vLLM, or NIM.
