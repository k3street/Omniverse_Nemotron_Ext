# Phase 23, 24, 29 — Canvas SPA (TypeScript/Vue)

These phases land in `web/floor-plan-ui/src/...` (TypeScript) and have
no Python deliverable. Listed here for spec coverage; the actual work
is daytime TypeScript editing.

## Phase 23 — Snap engine hardening

**Target file:** `web/floor-plan-ui/src/canvas/snap.ts:123-145`

**Goal:** dense-cluster snapping, wall-proximity offsets, 15° rotation
snap, sub-mm precision via float preservation.

**Test file:** `web/floor-plan-ui/src/canvas/snap.test.ts` (20 cases).

## Phase 24 — Agent confirm bar

**Target file:** `web/floor-plan-ui/src/components/ConfirmBar.vue`

**Goal:** when LLM calls `update_layout_spec` with a proposed mutation,
SPA shows confirm bar with approve/reject actions. Wires into the
workflow lifecycle (Phase 33+).

## Phase 29 — Canvas mirror panel

**Target file:** `web/floor-plan-ui/src/components/MirrorPanel.vue`

**Goal:** live state-sync from Kit. WebSocket subscription to Kit
events, repaints the canvas when prims mutate in Kit.

---

These phases are marked spec-represented but not implemented in this
session. See `docs/2026-05-12-night-1-progress.md` for the
Python-side scope coverage.
