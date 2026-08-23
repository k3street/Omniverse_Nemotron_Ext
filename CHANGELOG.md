# Changelog

This project uses a rolling changelog until the first tagged public release.

## Unreleased

### Added

- Installable Python package and `isaac-assist-service` command.
- Automatic tiered backend/frontend CI and opt-in live-runtime CI.
- Server-held approval decisions for governed plan application.
- Reproducible core-service container build and exported CI image artifact.
- Resumable long-running CP benchmarks with bounded execution and checkpoints.
- Deterministic gripper-force, high-rate sensor, contact-pose, and contact-
  telemetry kernels.
- Typed robot-subassembly registry with runtime and mount compatibility checks.

### Changed

- Release builds now fail when wheel or executable artifacts are missing.
- Hosted Linux builds report only the architecture they actually execute.
- SQLite CAS writes no longer depend on an executor future completing.

### Fixed

- Successful plan outcomes now require a matching approval and real snapshot.
- Mutable Pydantic collection defaults in core request/governance models.
- USD-dependent visual tests skip cleanly when `pxr` is unavailable.
- Legacy Phase 77, 87, and 88 scaffold modules now resolve to their canonical
  landed implementations instead of reporting contradictory status.
- The legacy Phase 76 real-vision module now resolves lazily to the concrete
  async Gemini provider already used by vision handlers.
