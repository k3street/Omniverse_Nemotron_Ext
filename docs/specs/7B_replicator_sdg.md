# Phase 7B — Enhanced Replicator / Synthetic Data Generation

**Status:** Not implemented (extends Phase 4A basic SDG)  
**Depends on:** Phase 4A (basic configure_sdg, run_sdg)  
**Research:** `research_reports/7B_replicator_sdg.md`

---

## Overview

Full domain-randomization authoring, dataset export, and distributed rendering. Extends Phase 4A with production-grade SDG pipeline control.

**Key corrections from research:**
- API is `omni.replicator.core` (import as `rep`), NOT "OmniReplicator"
- Reference: Isaac Sim Replicator docs, NOT `OmniIsaacGymEnvs` (archived)
- Chat IRO exists in Isaac Sim 6.0 for NL→IRO YAML but is limited in scope

---

## Tools

### 7B.1 `create_sdg_pipeline(annotators, randomizers, output_format, num_frames)`

**Type:** CODE_GEN handler (generates `omni.replicator.core` Python code)

```python
import omni.replicator.core as rep

with rep.new_layer():
    camera = rep.create.camera(position=(0, 0, 5), look_at=(0, 0, 0))
    rp = rep.create.render_product(camera, (1280, 720))

    writer = rep.WriterRegistry.get("CocoWriter")  # or "BasicWriter", "KittiWriter"
    writer.initialize(output_dir=output_dir, ...)
    writer.attach([rp])

    with rep.trigger.on_frame(num_frames=num_frames):
        # annotators attached via writer config
        pass

    rep.orchestrator.run()
```

**Supported annotators:** `bounding_box_2d`, `bounding_box_3d`, `semantic_segmentation`, `instance_segmentation`, `depth`, `normals`, `occlusion`

**Note:** `keypoints` is NOT a generic annotator — requires IRA extension or custom USD landmark setup. Remove from default list.

**Output formats:**
| Format | Writer | Status |
|--------|--------|--------|
| COCO | `CocoWriter` | Native, works correctly |
| KITTI | `KittiWriter` | Native but `alpha`, `dimensions`, `location`, `rotation_y` zeroed — needs custom subclass |
| Raw NumPy | `PytorchWriter` → `.numpy()` | Works |
| **TFRecord** | **Does not exist** | Must implement custom writer + TF dependency — defer or drop |

### 7B.2 `add_domain_randomizer(target, randomizer_type, params)`

**Type:** CODE_GEN handler

**Randomizer types:** pose, texture, lighting, material_properties, distractors

```python
with rep.trigger.on_frame():
    rep.randomizer.scatter_2d(rep.get.prims(semantics=[("class", "object")]))
    rep.randomizer.rotation(min_angle=-180, max_angle=180)
    rep.randomizer.color(colors=rep.distribution.uniform((0,0,0), (1,1,1)))
```

**Note:** Lux is NOT a directly settable unit — Replicator uses `intensity` (nits/cd/m²). Map user-facing "lux" to intensity based on light type and area.

### 7B.3 `preview_sdg(num_samples)`

**Type:** DATA handler (returns base64 images)

Generate a few sample frames and return annotated images. Use `rep.orchestrator.step()` (not `run_until_complete()` which blocks UI).

### 7B.4 `export_dataset(pipeline_id, output_dir, cloud_upload)`

**Type:** CODE_GEN handler

Run full generation. For N > 100 frames, use step-loop with periodic yield to avoid freezing Kit UI.

### 7B.5 Omniverse Farm Integration

**Status:** Available but not turnkey. Requires:
- Docker container with Isaac Sim headless
- Farm Queue + Farm Agent (self-hosted or Kubernetes)
- No native "submit SDG to Farm" SDK exists — build a job submission wrapper calling Farm Queue REST API

**Note:** NVIDIA Omniverse Launcher deprecated October 2025. Reference direct Kubernetes/Docker deployment.

---

## Missing Features to Consider

- **Cosmos SDG** — new in Isaac Sim 6.0, uses NVIDIA's world foundation model for photorealistic DR. Evaluate for inclusion.
- **`isaacsim.replicator.grasping`** — mentioned in PLAN.md dependency table but absent from task list. Include or explicitly defer.

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Pipeline code generation | L0 | compile(), verify `rep.` namespace, writer names |
| Randomizer code | L0 | compile(), verify randomizer types |
| Output format validation | L0 | Verify correct writer class per format |
| SDG execution | L3 | Requires Kit + GPU |

## Known Limitations

- `KittiWriter` has zeroed 3D fields — custom subclass needed for 3D object detection
- TFRecord requires external TF dependency — defer to future
- Domain randomization is CPU-bound — high randomizer count bottlenecks regardless of GPU
- `run_until_complete()` blocks Kit UI — use step-loop for large N
