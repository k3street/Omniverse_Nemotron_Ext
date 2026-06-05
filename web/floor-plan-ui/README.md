# Floor Plan UI

Multimodal canvas SPA for Isaac Assist — Block 1A.3 scaffold per
`docs/specs/2026-05-08-multimodal-foundation-spec.md` §9.5.

## Status

This is a Vite + React + Konva canvas UI that wires against the canvas REST
API in `service/isaac_assist_service/multimodal/routes.py`. It now renders
the full work surface: tool rail, palette, Konva canvas viewport, properties
panel, confirm bar, chat ribbon, status bar, keyboard affordances, WAL restore,
debounced patch sync, and SSE agent-update handling.

The GUI remains a development surface for the multimodal canvas, not the
primary Isaac Sim viewport. Use it to inspect and edit layout specs before
handoff to the Isaac Assist extension/Kit RPC path.

## Dev

```bash
cd web/floor-plan-ui
npm install
npm run dev    # → http://localhost:5173 with proxy to FastAPI :8000
```

Open the browser tab via the chat extension's `👁 Modes → Open canvas
editor`, or directly at `http://localhost:5173?session=default_session`
during development.

## GUI smoke checklist

After large merges, run the app and visually confirm these surfaces:

- Header: `Isaac Assist · Floor Plan` and `multimodal canvas v1.0`
- Left toolbar and object palette
- Konva viewport with grid, layout objects, reach/agency overlays, and guide support
- Properties/layers panel
- Agent confirmation bar
- Bottom chat ribbon and revision/session/save status bar

Then run the non-visual gates:

```bash
npm run build
npm test
```

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

## What's pending

Per spec §11.3, these richer editing controls still need completion or deeper
runtime wiring:

- [ ] Dimension lines + constraint indicators (spec §6.4)
- [ ] Custom robot silhouettes (32×32 SVG per robot class; spec §12.6)
- [ ] Motion vocabulary tokens (spec §12.7)
- [ ] Deeper Properties / Layers / Constraints editing coverage (spec §11.3.4)
- [ ] Full backend round-trip hardening for `canvas/proposed` and conflict resolution
- [ ] Playwright screenshot regression suite for fixed-seed layouts

## Backend dependencies

This SPA assumes the multimodal foundation is running:

- `service/isaac_assist_service/multimodal/` module
- `service/isaac_assist_service/multimodal/routes.py` mounted at
  `/api/v1/canvas` in `main.py`
- `service/isaac_assist_service/chat/tools/multimodal_handlers.py`
  registered into `tool_executor.py`

These modules are now on `master`.
