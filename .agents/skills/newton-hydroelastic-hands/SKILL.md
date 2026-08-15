---
name: newton-hydroelastic-hands
description: Build, inspect, or validate a non-destructive Newton 1.5 hydroelastic-contact wrapper for the Amber Revan robot with Psyonic left/right hands. Use when a request mentions hydroelastic hand/finger contact, Newton SDF collision on this robot USD, the 22 Psyonic hand bodies, bilateral hydro contact, the generated wrapper/manifest, or side-by-side Isaac Lab and Newton operation.
---

# Newton Hydroelastic Hands

Create a versioned companion USD for the Psyonic hands while preserving the
canonical robot asset and Isaac Lab runtime. Use the repository's existing
generator and validators; do not reimplement the USD authoring logic.

## Non-negotiable contract

- Treat the input robot USD as immutable. Never pass the same path as source
  and output, flatten over it, or author into its layer.
- Run the workflow with `.venv-newton`, pinned by `requirements-newton.txt`.
  Do not install Newton 1.5 into Isaac Lab's Python environment.
- Keep GPU validation optional. The generator and contract tests are CPU-only;
  defer the CUDA smoke if the GPU is occupied.
- Hydroelastic contact is bilateral. A grasped object must also have an SDF
  collision shape with `newton:hydroelasticEnabled = true`.
- Do not describe this as soft fingertip deformation. It is rigid
  distributed-pressure contact generated from overlapping SDFs.

Read [references/contract.md](references/contract.md) before changing generator
parameters or judging a generated wrapper.

## Workflow

1. Resolve the repository root and confirm these files exist:
   `scripts/make_newton_hydro_hands.py`,
   `scripts/smoke_newton_hydro_hands.py`, and `requirements-newton.txt`.
2. Resolve source, output, and manifest paths. Reject source/output equality.
   Prefer an output name ending in `_newton_hydro.usda` beside a generated
   artifacts directory, not beside or over the canonical source.
3. Record the source SHA-256 before generation. Do not continue if the source
   is missing or not a USD file.
4. Run the deterministic generator:

   ```bash
   WARP_CACHE_PATH=/tmp/newton-warp-cache \
     .venv-newton/bin/python scripts/make_newton_hydro_hands.py \
     SOURCE.usda OUTPUT_newton_hydro.usda
   ```

5. Inspect the manifest and require every CPU gate in the reference contract.
   Re-hash the source and require the hash to be unchanged.
6. Run the pure contract tests:

   ```bash
   pytest -q tests/test_newton_hydro_hands.py
   ```

7. Only when a CUDA device is available and not busy, run:

   ```bash
   WARP_CACHE_PATH=/tmp/newton-warp-cache \
     .venv-newton/bin/python scripts/smoke_newton_hydro_hands.py \
     OUTPUT_newton_hydro.usda --write-manifest
   ```

8. Report source, wrapper, manifest, source-integrity result, CPU gates, and
   GPU gate separately. Say `GPU deferred` rather than weakening the CPU gate
   when the host is busy.

## Change control

Use `--force` only when intentionally regenerating an existing derivative.
Preserve valid GPU evidence only when the generator confirms the wrapper hash
still matches. Any change to SDF resolution, narrow bands, padding, stiffness,
gap, collider selection, or self-collision filters requires a new manifest and
the full CPU gate; changes affecting contact generation also require a fresh
GPU smoke before being called validated.

This skill is specialized to the current 22-body Psyonic naming/layout. Stop
with a clear compatibility finding if the generator reports a different body
or source-collider count; do not broaden name matching on the fly.
