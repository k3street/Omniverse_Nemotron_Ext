# Roadmap

This is the short product-facing queue. Detailed experiments and measured
asset-pipeline history remain in `BACKLOG.md`; phase-level implementation
status remains in `docs/spec_coverage.md`.

## Current release gates

1. Keep packaging, L0/L1/L2, frontend, and artifact CI green.
2. Run the live `l3` workflow on an Isaac Sim self-hosted runner and retain
   evidence for chat-to-Kit, diagnosis, approval, snapshot, apply, and verify.
3. Replace remaining user-facing voice and viewport placeholders.
4. Promote high-value scaffolds only when their operational body and runtime
   verification both exist.

There are currently 2 modules that explicitly report scaffold status, both
covering the intentionally deferred macOS/Windows release path. Phases 79,
99, and 100 now have executable acceptance evaluators but remain
`implemented_unvalidated` until their live runtime evidence passes. The older
118-scaffold figure in `docs/spec_coverage.md` is retained as a dated
historical audit, not a current metric.

## Product workflows

- Chat/service health: hermetic boundary covered; live provider smoke pending.
- Scene diagnosis: deterministic coverage landed; live stage capture pending.
- Governed patching: approval and snapshot proof enforced; live rollback pending.
- SimReady assets: rigid/visual pipelines landed; liquids and granular missing.
- Laundry/deformables: generated garments and passive verification landed;
  actuated two-hand folding remains the primary manipulation milestone.

## Maintenance

- Treat lowercase `docs/` as canonical. Migrate useful material from legacy
  `Docs/` before removing duplicates.
- Extract shared extension code only after 5.1/6.0 behavior-equivalence tests
  identify the intentional runtime differences.
- Add native aarch64 packaging only with a real ARM runner or a tested
  cross-compilation toolchain.
