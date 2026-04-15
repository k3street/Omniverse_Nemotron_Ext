# Phase 6B — 3D Asset Acquisition for Simulation: Evaluation

**Status:** Not implemented  
**Depends on:** Phase 1A (add_reference)  
**Nothing depends on 6B** — safe to deprioritize  
**Research:** `research_reports/6B_image_to_usd.md`, `rev2/counter_6B_gpu.md`

---

## The Core Question

The original spec describes image-to-3D mesh generation (photo → TripoSR/TRELLIS → USD → place in scene). But for **simulation**, mesh geometry must be physically accurate — correct dimensions, contact surfaces, mass properties. Image-to-3D models produce visually plausible but physically approximate geometry.

This document evaluates whether image-to-3D is the right tool, and what alternatives exist.

---

## Use Case Analysis

| Use Case | Geometry Requirement | Image-to-3D Suitable? |
|----------|---------------------|----------------------|
| Visual scene dressing (background objects) | Low — visual only | Yes |
| Layout prototyping ("roughly like this") | Low — approximate shape | Yes |
| Pick-and-place (grasping) | High — contact surfaces must be mm-precise | No |
| Assembly simulation | Very high — exact tolerances | No |
| Sim-to-real transfer | Very high — geometry mismatch = policy failure | No |
| Collision avoidance planning | Medium — convex hull sufficient | Marginal |

**Verdict:** Image-to-3D is useful for visual prototyping. For physics-critical simulation, it produces unreliable results.

---

## Alternative Approaches for Simulation-Quality Assets

### 1. CAD Model Import (Recommended for precision)
- **Source:** SolidWorks, Onshape, Fusion 360 → export STEP/OBJ/USD
- **Quality:** Exact geometry, correct mass properties
- **Isaac Sim support:** Native USD import, URDF with mesh references
- **Already in scope:** Phase 3 `import_robot` handles URDF/MJCF/USD

### 2. 3D Scanning / Photogrammetry (Recommended for real objects)
- **Source:** iPhone LiDAR, structured light scanners, multi-view photogrammetry
- **Quality:** Sub-mm accuracy for good scans, real textures
- **Pipeline:** Point cloud → mesh (Meshlab/Open3D) → USD
- **Advantage:** Captures real-world objects that have no CAD model

### 3. NeRF/Gaussian Splatting → Mesh (Emerging)
- **Source:** Multi-view photos or video → NeRF/3DGS → mesh extraction
- **Quality:** Better than single-image-to-3D, worse than scanning
- **Pipeline:** nerfstudio/gsplat → marching cubes → USD

### 4. Image-to-3D (Current spec — for visual prototyping only)
- **Best models (2026):** Hunyuan3D-2.1 (Apache 2.0, PBR, 5 GB mini), TRELLIS.2 (MIT, PBR native)
- **Quality:** Approximate geometry, hallucinated back surfaces, no mass properties
- **Use:** Quick visual mock-up before replacing with real CAD/scan

---

## Recommendation

**Reframe Phase 6B as "Asset Acquisition Pipeline"** with three tiers:

### Tier 1 — CAD/Scan Import (Priority)
- Accept `.step`, `.obj`, `.stl`, `.ply`, `.usd` uploads via chat 📎 button
- Convert to USD via `omni.kit.asset_converter` or `trimesh`
- Apply physics properties (collision mesh, mass from volume × density)
- This is mostly covered by existing Phase 3 import tools — extend, don't reinvent

### Tier 2 — Image-to-3D Visual Prototype (Optional)
- Single-image upload → Hunyuan3D-2.1 or TRELLIS.2 → USD
- **Clearly marked as "visual prototype — not physics-accurate"**
- User warned before placing in physics-critical simulation
- Sequential GPU execution (pause renderer → generate → resume)

### Tier 3 — Multi-View Reconstruction (Future)
- Multi-image upload → photogrammetry pipeline → USD
- Higher quality than Tier 2, closer to scan quality
- Requires more compute and user effort (multiple photos)

---

## If Implementing Tier 2 (Image-to-3D)

### Backend Selection

| Backend | VRAM | Quality | License | Default? |
|---------|------|---------|---------|----------|
| Hunyuan3D-2.1 mini | 5 GB | Good + PBR | Apache 2.0 | Yes (local) |
| TRELLIS.2 | 12 GB FP16 | Best + PBR | MIT | Optional (high-end) |
| TripoSR | 4 GB FP16 | Low (preview only) | MIT | Fast preview |
| Tripo API | 0 (cloud) | Good | Commercial | Opt-in |

### Technical Requirements
- Async job endpoint: `POST /api/v1/generate/image_to_3d` → returns job_id
- Poll endpoint: `GET /api/v1/generate/jobs/{job_id}`
- Background removal: BiRefNet (via rembg model selector)
- GLB→USD: always set `use_meter_as_world_unit=True`
- Post-conversion: verify `GetStageUpAxis()`, `GetStageMetersPerUnit()`, bbox sanity
- Subprocess isolation: generation runs in separate process to avoid OOM killing FastAPI
- Image pre-scaling: normalize to model input size (512×512 or 1024×1024) before processing

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| File type validation | L0 | Magic bytes check, size limits |
| GLB→USD unit conversion | L0 | Verify `use_meter_as_world_unit` flag |
| Backend abstraction | L0 | FakeBackend returns known GLB, verify pipeline |
| Async job lifecycle | L1 | Create job → poll → complete |
| Actual generation | L3 | Requires GPU |
