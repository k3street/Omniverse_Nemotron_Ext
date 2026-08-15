# Hydroelastic wrapper contract

## Supported asset shape

The current generator recognizes body names beginning with
`psyonic_left_` or `psyonic_right_`. It expects:

- 22 Psyonic rigid bodies: two bases plus two links for each of five fingers
  per hand;
- 68 original hand collision shapes to disable in the wrapper;
- 20 adjacent-link collision filters: base-to-L1 and L1-to-L2 per finger;
- 22 generated `NewtonHydroCollision` meshes.

Non-adjacent fingers remain eligible for self-contact. The full robot remains
52 bodies and 52 joints in the known canonical asset.

## Default SDF/contact parameters

| Parameter | Value |
|---|---:|
| `newton:sdfMaxResolution` | 64 |
| `newton:sdfNarrowBandInner` | -0.004 m |
| `newton:sdfNarrowBandOuter` | 0.004 m |
| `newton:sdfPadding` | 0.004 m |
| `newton:sdfTextureFormat` | `uint16` |
| `newton:hydroelasticStiffness` | `1.0e10` |
| `newton:contactMargin` | 0.0 m |
| `newton:contactGap` | 0.001 m |

## Required CPU validation

The generated manifest must show all of the following:

- canonical source SHA-256 recorded and source left unchanged;
- one relative reference to the canonical source;
- 22 watertight, outward-wound collision hulls with positive volume;
- all generated meshes carrying `PhysicsCollisionAPI` and
  `NewtonSDFCollisionAPI` with hydroelastic contact enabled;
- all 68 overlapping source hand colliders disabled in the wrapper;
- exactly 20 adjacent hand-body collision filters authored;
- wrapper re-import succeeds in Newton 1.5 with expected body/joint coverage;
- no authored source collider remains active for the replaced hands.

## Optional CUDA evidence

The smoke test builds all 22 hand SDFs, isolates one distal left-index mesh,
and overlaps it with a temporary hydroelastic sphere by 2 mm. Passing evidence
requires one isolated SDF/SDF pair and at least one contact for that pair. The
known GB10 run produced 64 reduced contacts; 64 is evidence from that run, not
a universal threshold.

## Bilateral object requirement

Newton hydroelastic contact requires both shapes in the pair to use SDF
collision and opt into hydroelastic contact. A normal triangle mesh, convex
collider, or PhysX-only collision API on the manipulated object will not turn
the hand/object pair into hydroelastic contact.

## Side-by-side boundary

The canonical USD remains the Isaac Lab/PhysX input. The generated wrapper is
the Newton 1.5 input. Do not make Isaac Lab load Newton-specific schemas as a
condition of normal operation, and do not install the standalone Newton pin
into Isaac Lab's environment.
