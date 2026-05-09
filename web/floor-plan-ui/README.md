# Floor Plan UI

Multimodal canvas SPA for Isaac Assist — Block 1A.3 scaffold per
`docs/specs/2026-05-08-multimodal-foundation-spec.md` §9.5.

## Status: scaffold only

This is a Vite + React + Konva starter that wires against the canvas REST
API in `service/isaac_assist_service/multimodal/routes.py`. The skeleton
proves the plumbing works (loads `LayoutSpec`, renders objects with
agency-tier colors and reach circles, shows status bar). Interactive
editing — drag-drop palette, smart guides, snap markers, dimension lines,
persistent chat input ribbon — is the next session's work.

## Dev

```bash
cd web/floor-plan-ui
npm install
npm run dev    # → http://localhost:5173 with proxy to FastAPI :8000
```

Open the browser tab via the chat extension's `👁 Modes → Open canvas
editor`, or directly at `http://localhost:5173?session=default_session`
during development.

## Build

```bash
npm run build  # → dist/ — served by FastAPI StaticFiles mount in production
```

## Architecture

```
src/
  main.tsx              entry point — React DOM mount
  App.tsx               header + toolbar + canvas + right-dock + status bar
  api/
    types.ts            TypeScript mirror of LayoutSpec types
    floorPlanApi.ts     typed REST client; CanvasConflictError on 409
```

Visual tokens and class colors mirror
`service/isaac_assist_service/multimodal/render.py` so the SPA and the
Kit canvas-mirror panel render identically. Update both when the design
tokens change.

## What's pending (next session)

Per spec §11.3 (button/control inventory) — the components below need
implementation. The scaffold above renders STATIC layouts; nothing in the
UI mutates state yet.

- [ ] Object palette (drag from sidebar; spec §11.3.3)
- [ ] Multi-select + transformer handles (Konva `Transformer` widget)
- [ ] Smart guides + snap markers (spec §6.3 — five marker types)
- [ ] Dimension lines + constraint indicators (spec §6.4)
- [ ] Properties / Layers / Constraints right dock (spec §11.3.4)
- [ ] Floating confirm bar for agent-proposed states (spec §5.7)
- [ ] Custom robot silhouettes (32×32 SVG per robot class; spec §12.6)
- [ ] Motion vocabulary tokens (spec §12.7)
- [ ] Persistent chat input ribbon at bottom (spec §11.2)
- [ ] Zustand store with command-pattern undo/redo (spec §2.3)
- [ ] localStorage write-ahead log + sendBeacon on beforeunload (spec §13.4)
- [ ] SSE listener for `canvas/proposed` etc events from backend

## Backend dependencies

This SPA assumes the multimodal foundation is running:

- `service/isaac_assist_service/multimodal/` module
- `service/isaac_assist_service/multimodal/routes.py` mounted at
  `/api/v1/canvas` in `main.py`
- `service/isaac_assist_service/chat/tools/multimodal_handlers.py`
  registered into `tool_executor.py`

All landed in commits on `feat/multimodal-foundation` branch.
