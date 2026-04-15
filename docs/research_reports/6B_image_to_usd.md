# Phase 6B — Image-to-USD Model Generation: Critique

**Agent:** Research 6B Image-to-USD  
**Date:** 2026-04-15  
**Status:** Complete

## 1. The Model Lineup Is Already Outdated

The spec names TripoSR, InstantMesh, and Trellis (v1). As of early 2026:

- **TripoSR** — Still fast but quality is poor. ~120k chaotic triangles. Keep only for quick-preview tier.
- **InstantMesh** — Needs ~10 GB VRAM. No longer "higher quality" — now mid-tier. No updates since April 2024.
- **TRELLIS v1** — Requires 16 GB VRAM minimum, spikes to 30 GB. Already superseded.

**Missing from spec:**

| Model | VRAM | License | Key advantage |
|---|---|---|---|
| **TRELLIS.2** (microsoft/TRELLIS.2-4B) | 24 GB recommended (12 GB at 512³) | MIT | PBR materials out of the box |
| **Hunyuan3D-2.1** (Tencent) | 6–12 GB shape; mini variant = 5 GB | Apache 2.0 | Turbo model, PBR pipeline, separate shape/texture stages |
| **Tripo v2.5** | API-only ($0.20–0.40/model) | Commercial | Production-grade |
| **Rodin Gen-2** | API-only | Commercial | 10B parameter model, highest raw quality |

**Recommendation:** Restructure backends as:
- `triposr` → "preview" tier
- `hunyuan3d` → "local quality" tier (replace InstantMesh)
- `trellis2` → "local premium" tier (replace TRELLIS v1)
- `api` → name specifically (Tripo or Meshy)

---

## 2. GPU Memory — This Cannot Work As Specced

Isaac Sim: 12–16 GB VRAM. TripoSR: 6–8 GB. TRELLIS: 16–30 GB. **Running any of these simultaneously with Isaac Sim on the same GPU is not viable below RTX 4090.**

**The spec has no mention of this constraint at all.**

**Fixes:**
1. Unload/reload approach: signal Kit to release GPU resources during generation
2. CPU offload: Hunyuan3D-2GP runs on 6 GB by offloading to CPU
3. Separate process with `CUDA_VISIBLE_DEVICES` for second GPU
4. **API-first default**: default to API backend, local opt-in with explicit VRAM warning

---

## 3. GLB → USD via omni.kit.asset_converter — Known Bugs

**Issue A:** Unit scale defaults to centimeters instead of meters. Fix: always use `use_meter_as_world_unit=True`.

**Issue B:** Blend shapes and morph targets are dropped.

**Issue C:** Y-up (glTF) vs Z-up (Isaac Sim) coordinate system issues.

**Fix for 6B.5:** Add post-conversion validator checking `UsdGeom.GetStageUpAxis()`, `UsdGeom.GetStageMetersPerUnit()`, and bounding box sanity.

---

## 4. Background Removal — rembg Is the Wrong Default

**rembg/U2Net:** Fails on hair, transparent objects, complex backgrounds.

**What to use in 2026:** **BiRefNet** (available via rembg model selector) — handles hair, transparency, complex scenes dramatically better. Drop-in replacement.

---

## 5. Texture Quality — The Spec Ignores This

- TripoSR/InstantMesh: single-view texture, blurry back half
- **TRELLIS.2:** outputs PBR maps natively (base color + metallic + roughness + opacity)
- **Hunyuan3D-2.1:** dedicated texture generation stage with PBR output

**New task 6B.7:** Multi-view input support as first-class path.

---

## 6. Architecture Issues

- **Process isolation missing:** If TRELLIS.2 OOMs, it takes down FastAPI
- **Generation is not synchronous:** 15s–5min. Need async job-ID + polling endpoint
- **Base64 image payload:** 12 MP photo = 16+ MB. Need multipart/form-data or pre-scale

---

## Summary of Required Changes

| Area | Severity | Change |
|---|---|---|
| Model lineup | High | Replace InstantMesh→Hunyuan3D-2.1; TRELLIS v1→TRELLIS.2; add Tripo API as default |
| GPU coexistence | Critical | Document VRAM constraints; default to API; add subprocess isolation |
| GLB→USD units | High | Always set `use_meter_as_world_unit=True`; add bbox sanity check |
| Background removal | Medium | Default to BiRefNet |
| Texture quality | High | Distinguish PBR-capable backends from diffuse-only |
| Generation UX | High | Make endpoint async with job ID + polling |
| Image upload size | Medium | Enforce multipart/form-data or pre-scale |
| Multi-view input | Medium | Promote to first-class path |

## Sources
- [TRELLIS.2 — GitHub](https://github.com/microsoft/TRELLIS.2)
- [Hunyuan3D-2.1 — GitHub](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1)
- [Asset Converter GLB→USD unit issue (NVIDIA Dev Forums)](https://forums.developer.nvidia.com/t/asset-converter-usd-stage-units-incorrect-after-glb-to-usd-conversion)
- [BiRefNet vs rembg vs U2Net — DEV Community](https://dev.to/om_prakash_3311f8a4576605/birefnet-vs-rembg-vs-u2net)
- [Isaac Sim GPU Requirements](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html)
