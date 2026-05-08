# 2D Floor Plan Tool — Comprehensive Implementation Spec

Authored 2026-05-08 in a research session. Eight Sonnet agents researched in parallel
(UX flows, CAD interaction patterns, technical architecture, domain modeling, visual
design, agent-floorplan protocol, edge cases/accessibility, orchestrator integration,
state-of-the-art surveys). This document synthesizes their outputs into a single
implementation-ready spec.

For evaluation by the next session before implementation begins.

---

## 0. Reading Guide

This spec has **13 sections**. Read in order; later sections reference earlier ones.

- §1-2: vision, principles, architecture decisions
- §3: data model (start of implementation contract)
- §4-5: UI layout, full button/control inventory with sizes
- §6: interaction patterns (selection, modify, snap, dimensions)
- §7: user flows + three end-to-end case studies
- §8-9: agent integration + orchestrator integration
- §10: edge cases, accessibility, failure handling
- §11: phasing & milestones
- §12: open questions for next session
- §13: file/path inventory

The author's position is conservative: **vault/Track A from the prior research is
parallel work**; this spec stands on its own. Vault is operational hardening; this
floor plan tool is a feature.

---

## 1. Vision and Design Principles

### 1.1 What this is

A 2D top-down spatial editor for robot cells, embedded in Isaac Assist. Users see
robot bases as scaled-footprint rectangles, robot reach as circles, conveyors and
bins as oriented rectangles, cubes as small squares. They can drag, snap, rotate,
constrain, annotate, and ultimately commit the layout to Isaac Sim via the existing
hard-instantiate canonical-template flow.

### 1.2 What it is not

- Not a USD authoring tool — USD prims are produced via existing tool_executor handlers
- Not a freehand sketcher — input is structured object placement, not pen-on-canvas
- Not a multi-user collaborative editor — single-user (multi-user reserved for future)
- Not a substitute for chat — chat remains primary entry point; floor plan augments

### 1.3 Core design principles

**P1 — Harness deterministic, LLM smart on bounded domain.** Same principle that
governs hard-instantiate. The floor plan tool itself is deterministic: drag, snap,
constraint resolution, validation are all deterministic primitives. The LLM enters
to (a) generate proposed layouts from natural language, (b) read the floor plan when
reasoning, (c) suggest changes. It never silently mutates committed state.

**P2 — User direct edits are committed; agent edits are proposed.** Asymmetric trust.
User intent in direct manipulation is unambiguous and applied immediately. Agent
mutations require explicit user confirmation via a UI mechanic (not chat-text dialog).

**P3 — Status visible at all times.** Anton's exact phrase: *"användaren vet vad som
pågår at all times"*. A persistent status bar communicates idle/thinking/syncing/
verifying/error. No silent operations.

**P4 — Smooth mode-switching.** Anton's exact phrase: *"extremt smidigt och smooth"*.
Chat ↔ Floor Plan ↔ 3D Scene transitions use motion that signals continuity, not jump.

**P5 — NVIDIA aesthetic + CAD ergonomics.** Anton: *"behålla Nvidia estetiken men
samtidigt det bästa från cad ritningsprogram"*. Dark canvas like Kit, NVIDIA green
as accent only, ISO-129-style dimension annotations, AutoCAD-conventional snap markers.

**P6 — Round-trip with canonicals.** Existing CP-01..CP-05 must be representable in
floor plan format and round-trip back to identical canonical execution. New floor
plans should be exportable as new canonicals.

---

## 2. Technical Architecture

### 2.1 Hosting model — Web SPA served by FastAPI

**Decision: browser-based SPA, served from existing FastAPI process.**

Three mounting alternatives were evaluated:
- **Embedded panel in chat_view.py**: rejected — `chat_view.py` is 440px wide, no
  room. omni.ui has no canvas drawing primitives.
- **Kit popup window with WebViewport**: rejected — Kit version-gated APIs differ
  between Isaac 5.1 and 6.0; touch/drag input is unreliable; debug is harder.
- **Web SPA via StaticFiles mount**: chosen — FastAPI already runs on port 8000, has
  `allow_origins=["*"]`, has SSE channel live. Adding `StaticFiles(directory=
  "web/floorplan-ui/dist", html=True)` mounts the SPA at `/floorplan`. Extension
  opens it via `webbrowser.open(...)` — three lines.

**Trigger from extension**: a "Floor Plan" button in `chat_view.py` (see §5.1.4) opens
`http://localhost:8000/floorplan?session={session_id}`. Browser tab; user resizes/moves
freely. Future: PWA install for desktop-app feel without bundling Electron.

### 2.2 Rendering — Konva.js

**Decision: Konva.js (canvas-backed scene graph).**

Evaluated: SVG (DOM bloat past ~300 elements), Canvas 2D (manual hit-testing, weeks
of framework work), WebGL via PIXI/Three (overkill for 2D, contributor barrier high),
HTML+CSS divs (limited beyond simple cases), Konva.js, Fabric.js.

Konva wins on:
- Layered canvas model (grid layer / geometry layer / annotation layer / overlay layer)
- Built-in `Transformer` widget for resize/rotate handles
- Per-shape event handlers (`dragmove`, `click`, `dblclick`)
- Compact JSON serialization (`stage.toJSON()`) maps to persistence format
- 60fps on integrated GPU at 1000+ shapes (typical robot cell: 20-80 objects)
- Better TypeScript types and 2026 maintenance vs Fabric.js

### 2.3 State management — Zustand + command pattern

Single in-memory store via Zustand (no Redux boilerplate). Mutations produce
`Command` objects with `apply()` / `undo()`. Commands pushed to a stack; redo via
forward stack. 100-step undo depth. Continuous drag = single command. Linear history
(no branching). Stack cleared on new action after undo.

Command types: `MoveObject`, `ResizeObject`, `RotateObject`, `AddObject`,
`DeleteObject`, `SetMeta`, `SetConstraint`, `BulkUpdate` (used by agent writes).

### 2.4 Persistence — JSON file per session

Path: `workspace/floor_plans/{session_id}.json`. One file per session.

Auto-save: debounced 500ms POST on every mutation to `/api/v1/floor_plan/{session_id}/patch`.
Server merges and writes to disk. SSE event `floor_plan_updated` broadcasts the merged
state back to all connected clients (single-client today, multi-client future-proofed).

Page load: `GET /api/v1/floor_plan/{session_id}` returns saved blob or blank plan.

### 2.5 Real-time sync — existing SSE channel

Reuses `/api/v1/chat/stream/{session_id}` (already live in `routes.py`). Two new
event types:

- **`floor_plan_updated`**: emitted when agent mutates state. Payload is delta
  (changed object IDs + new field values), not full state. Client merges.
- **`floor_plan_build_progress`**: emitted during canonical instantiation, one per
  tool call. Payload `{prim_path, tool, status: "started" | "done" | "error"}`. UI
  draws progress ring on corresponding shape.

User → server: `POST /api/v1/floor_plan/{session_id}/patch` with delta array. Returns
202 Accepted immediately (non-blocking; LLM not in this path).

**Protocol: deltas, not snapshots.** Full state for active scene ≈ 5-15 kB; deltas
for single drag ≈ 200 bytes. During active drag, client throttles to max 1 POST per
200ms.

**Conflict resolution**: last-writer-wins on `(id, field)` pairs, server timestamp
authoritative. SSE `floor_plan_updated` corrects client. No three-way merge.

### 2.6 Performance targets

| Metric | Target |
|---|---:|
| Smooth drag at N objects | N=500 |
| Drag-event latency (client-side) | <16ms |
| Snap-detection latency | <10ms |
| User-edit POST debounce | 200ms |
| POST patch round-trip | <100ms |
| Agent build-progress first event | <500ms |
| Agent LLM reply | 2-8s (existing chat baseline) |
| Max floor plan file size | ~50 kB (500 objects × ~100 bytes) |

### 2.7 Repo layout

```
service/
  isaac_assist_service/
    floor_plan/
      __init__.py
      routes.py          ← FastAPI router (mounted in main.py)
      models.py          ← Pydantic schemas
      translator.py      ← floor_plan_to_tool_sequence()
      validator.py       ← cross-object/constraint validation
      persistence.py     ← read/write workspace/floor_plans/*.json
web/
  floor-plan-ui/
    src/
      App.tsx
      canvas/
        FloorPlanCanvas.tsx       ← Konva stage wrapper
        layers/                   ← Grid, Geometry, Annotation, Overlay
        shapes/                   ← per-class shape components
        snap.ts                   ← grid + object snap geometry
      store/
        floorPlanStore.ts         ← Zustand
        commands.ts               ← Command pattern
        sync.ts                   ← SSE listener + POST patch
      ui/
        Toolbar.tsx
        ObjectPalette.tsx
        PropertiesInspector.tsx
        LayersPanel.tsx
        StatusBar.tsx
        ConfirmBar.tsx
        CommandPalette.tsx
      api/
        floorPlanApi.ts
    vite.config.ts
    package.json
workspace/
  floor_plans/                   ← runtime state (gitignored)
```

### 2.8 Build pipeline

Vite + TypeScript SPA. Production output to `web/floor-plan-ui/dist/`. Mounted in
`main.py`:
```python
app.mount("/floorplan", StaticFiles(directory="web/floor-plan-ui/dist", html=True),
         name="floorplan")
```
Dev: `npm run dev` on Vite port 5173 with proxy to FastAPI 8000. Full HMR. No
coordination required.

---

## 3. Domain Data Model

### 3.1 Object taxonomy

| Class | Default footprint (m) | Reach radius (m) | Effective pick (m) | Rotation? |
|---|---|---:|---:|---|
| `franka_panda` | 0.12 × 0.12 | 0.855 | 0.700 | yes |
| `ur5e` | 0.13 × 0.13 | 0.850 | 0.700 | yes |
| `ur10e` | 0.19 × 0.19 | 1.300 | 1.060 | yes |
| `kinova_gen3` | 0.10 × 0.10 | 0.902 | 0.740 | yes |
| `iiwa` | 0.16 × 0.16 | 0.820 | 0.670 | yes |
| `jaco7` | 0.08 × 0.08 | 0.902 | 0.740 | yes |
| `nova_carter` | 0.69 × 0.96 | — | — | yes |
| `conveyor` | 3.0 × 0.4 (variable length) | — | — | yes |
| `bin` | 0.3 × 0.3 | — | — | yes |
| `cube` | 0.05 × 0.05 | — | — | locked |
| `table` | 2.0 × 1.0 | — | — | yes |
| `station_marker` | 0.06 × 0.06 (point) | — | — | n/a |
| `camera_sensor` | 0.05 × 0.05 | — | — | yes |
| `lidar_sensor` | 0.10 × 0.10 | — | — | yes |
| `ramp` | 0.4 × 0.3 | — | — | yes |
| `wall` / `boundary` | variable | — | — | yes |

The 82% effective-pick-radius heuristic comes from CP-01:
0.700 / 0.855 = 0.818, accounting for pick at non-zero z above robot base.

### 3.2 Per-object schema

```typescript
interface FloorPlanObject {
  id:         string;           // stable UUID, unique
  class:      ObjectClass;      // see taxonomy
  name:       string;           // /^[a-zA-Z][a-zA-Z0-9_]*$/, max 64 chars
                                // → USD prim path = `/World/${name}`
  position:   { x: number; y: number };  // meters, world coordinates, center
  rotation:   number;           // degrees, [0, 360), 0 = +X axis, CCW
  size:       { w: number; h: number };  // meters
  color?:     string;           // hex, optional override of class default
  notes:      string;           // free-text, max 4096 chars, agent-readable
  metadata:   {
    material?:        string;
    friction?:        number;
    weight_kg?:       number;
    surface_z?:       number;     // top surface height, for tables
    semantic_color?:  string;     // for color-routed cubes (CP-03)
    sleep_threshold?: number;
    scale_for_canonical?: [number, number, number];  // direct USD scale
    custom?:          Record<string, unknown>;
  };
  reach?:     {                 // robot arm classes only
    radius:                  number;
    effective_pick_radius:   number;
    display_both:            boolean;  // show both circles, default true
  };
  belt?:      {                 // conveyor only
    axis:                  "local_x" | "local_y";
    direction:             -1 | 1;
    surface_velocity_m_s:  number;
  };
  sensor?:    {                 // camera/lidar
    type:                "camera" | "lidar";
    scan_radius?:        number;
    frustum_angle_deg?:  number;
    resolution?:         string;
  };
  prim_path:  string | null;    // USD path; null until built in Isaac Sim
  locked:     boolean;          // user-locked from edit
  layer:      string;           // layer membership
}
```

### 3.3 Constraints

```typescript
type Constraint =
  | DistanceConstraint
  | AlignmentConstraint
  | AngleConstraint
  | BoundsConstraint
  | ReachConstraint;

interface DistanceConstraint {
  id:       string;
  type:     "distance";
  a:        { object_id: string; anchor: Anchor };
  b:        { object_id: string; anchor: Anchor };
  measure:  "center_to_center" | "edge_to_edge" | "bbox_clearance";
  min_m?:   number;
  max_m?:   number;
  description?: string;
  severity: "error" | "warning";
  enabled:  boolean;
}

type Anchor = "center" | "near_edge" | "far_edge"
            | "left_edge" | "right_edge" | "top_edge" | "bottom_edge";

interface AlignmentConstraint {
  id: string; type: "alignment";
  objects: string[];     // 2+ object ids
  axis: "x" | "y";
  anchor: Anchor;
  tolerance_m: number;
  severity: "error" | "warning"; enabled: boolean;
}

interface AngleConstraint {
  id: string; type: "angle";
  a: string; b: string;
  relation: "parallel" | "perpendicular" | "fixed";
  angle_deg?: number;     // for relation="fixed"
  tolerance_deg: number;
  severity: "error" | "warning"; enabled: boolean;
}

interface BoundsConstraint {
  id: string; type: "inside_bounds";
  objects: string[] | ["ALL"];
  bounds: { xmin: number; ymin: number; xmax: number; ymax: number };
  description?: string;
  severity: "error" | "warning"; enabled: boolean;
}

interface ReachConstraint {
  id: string; type: "reach";
  robot_id: string; target_id: string;
  zone: "full" | "effective_pick";
  description?: string;
  severity: "error" | "warning"; enabled: boolean;
}
```

### 3.4 Layer system

Default layers (each pre-populated at floor plan create time):

| Layer ID | Name | Default visible | Default locked | Default color |
|---|---|---|---|---|
| `background` | Background | yes | yes | `#888888` |
| `fixtures` | Fixtures | yes | no | `#4A90D9` |
| `robots` | Robots | yes | no | `#409CFF` |
| `conveyors` | Conveyors | yes | no | `#FFA800` |
| `workpieces` | Workpieces | yes | no | `#FF6450` |
| `sensors` | Sensors | yes | no | `#00C8B4` |
| `annotations` | Annotations | yes | no | `#C8CC80` |

Default layer assignments per class:
- `wall`, `boundary` → `background`
- `table`, `ramp` → `fixtures`
- robot arms, `nova_carter` → `robots`
- `conveyor` → `conveyors`
- `cube`, `bin` → `workpieces`
- `camera_sensor`, `lidar_sensor`, `station_marker` → `sensors`

Layer-level lock overrides object-level lock. Layer-level visibility hides all
objects in the layer.

### 3.5 Top-level document schema

```typescript
interface FloorPlan {
  floor_plan_version:  "1.0";
  plan_id:             string;
  name:                string;
  canonical_id:        string | null;     // CP-01..CP-05 if matched
  created_at:          string;            // ISO 8601
  modified_at:         string;
  session_id:          string;
  layers:              Layer[];
  objects:             FloorPlanObject[];
  constraints:         Constraint[];
  annotations:         Annotation[];
  parameters:          Record<string, unknown>;  // T2 param substitution
  verify_args_override?: VerifyArgs;
  viewport:            { pan: [number, number]; zoom: number };
  selection:           string[];          // selected object ids
  sim_state:           "unbuilt" | "building" | "live" | "error";
  metadata: {
    workspace_bounds: { xmin: number; ymin: number; xmax: number; ymax: number };
    grid_size_m:      number;
    snap_to_grid:     boolean;
    units:            "meters";
  };
}
```

### 3.6 Mapping to canonical-template format

| Floor plan field | Canonical field / tool call |
|---|---|
| `object.name` | USD prim path = `/World/${name}` |
| `object.position.{x,y}` | tool call `position=[x, y, surface_z_or_class_default]` |
| `object.rotation` (degrees CCW) | quaternion `[cos(θ/2), 0, 0, sin(θ/2)]` |
| `object.size.{w,h}` | `size=[w, h, depth]` for conveyor; `scale=[w/2, h/2, z/2]` for Cube |
| `object.metadata.surface_z` | z-offset for placed objects |
| `object.metadata.semantic_color` | `set_semantic_label(class_name=color, semantic_type='color')` |
| `object.belt.surface_velocity_m_s` | `surface_velocity=[v, 0, 0]` (post-rotation) |
| `constraint.inside_bounds.bounds` | `verify_args.footprint_bounds` |
| `object.notes`, `annotations[]` | injected into agent context, NOT into USD stage |
| `parameters` | passed as `param_overrides` to `execute_template_canonical` |

Round-trip guarantee: `open_floor_plan(template_id="CP-01")` → no edits → `apply_floor_plan_to_scene()`
must produce identical execution to current CP-01 hard-instantiate path.

---

## 4. UI Layout — Top-Level Structure

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  HEADER (40px tall)                                                         │
│  [Logo] [Chat][Floor Plan][3D Scene]              [⚙][?][⛶]                 │
├──┬──────────────────────────────────────────────────────────┬───────────────┤
│T │                                                          │  PROPERTIES   │
│O │                                                          │  /            │
│O │                                                          │  LAYERS       │
│L │           CANVAS                                         │  /            │
│B │           (background #111214, grid)                     │  CONSTRAINTS  │
│A │                                                          │               │
│R │                                                          │  (right dock, │
│  │                                                          │   tabbed,     │
│4 │                                                          │   resizable)  │
│8 │                                                          │               │
│p │           [floating confirm bar appears here when needed]│  280px wide   │
│x │                                                          │   default     │
│  │                                                          │               │
│  │                                                          │               │
│  │      [Mini-map  120x120 px, bottom-right of canvas,      │               │
│  │       only when content > 3× viewport]                   │               │
├──┴──────────────────────────────────────────────────────────┴───────────────┤
│  STATUS BAR (24px tall)                                                     │
│  [● state] [text]            [zoom: 1.0×] [grid: on] [⊞ minimap] [coords]   │
└─────────────────────────────────────────────────────────────────────────────┘

  OBJECT PALETTE          (collapsible, 64px collapsed / 200px expanded)
  ┌───────────────┐       Floats over canvas left edge, toggle via toolbar
  │ Robots        │
  │ ┌───┐ ┌───┐   │
  │ │ Fr│ │UR │   │
  │ └───┘ └───┘   │
  │ Conveyors     │
  │ ┌───┐         │
  │ │Co │         │
  │ └───┘         │
  │ ...           │
  └───────────────┘
```

**Total minimum window size**: 1200×720 px. Below this, properties panel collapses
to icon strip; below 900×640 the tool shows a "best viewed at 1200×720+" notice
(read-only inspection still available).

---

## 5. Complete Button & Control Inventory

This is the explicit answer to *"vilka knappar som behöver finnas i interfacet och
vart de placeras, och storleken"*. Every interactive control has: location, size,
icon, label, behavior, keyboard shortcut, visibility rule.

All sizes in CSS pixels at 1× DPR. SVG icons scale automatically. Touch targets
respect WCAG 2.5.5 (minimum 44×44 px on touch-detected devices via `(hover: none)`
media query).

### 5.1 Header Bar (40 px tall, fixed top)

#### 5.1.1 Logo / Brand

- **Position**: top-left, 12px from left edge, vertically centered
- **Size**: 24×24 px (NVIDIA eye-mark) + 80×16 px wordmark "Isaac Assist"
- **Behavior**: clicking opens command palette (same as Ctrl+K)
- **Visibility**: always

#### 5.1.2 Mode tabs (Chat / Floor Plan / 3D Scene)

Pill-shaped button group, centered horizontally in header.

| Tab | Width | Active state |
|---|---:|---|
| Chat | 80 px | NVIDIA green underline 2px, text `#DDDDDD` |
| Floor Plan | 100 px | same |
| 3D Scene | 90 px | same |

- **Height**: 32 px (pill within 40px bar)
- **Spacing**: 8 px between pills
- **Inactive state**: text `#8A8E92`, no underline; on hover, text → `#DDDDDD`
- **Behavior**: switches view via 380ms cross-dissolve. Inactive tabs show a small
  green pulsing dot (6 px, top-right of pill) when their view has unread state
  (e.g., agent generated a layout while user was in Chat)
- **Keyboard**: `Ctrl+1` Chat, `Ctrl+2` Floor Plan, `Ctrl+3` 3D Scene
- **Visibility**: always

#### 5.1.3 Header right cluster

3 icon buttons, each 32×32 px, 4 px spacing, aligned to right edge with 12 px margin:

| Icon | Label (tooltip) | Shortcut | Behavior |
|---|---|---|---|
| ⚙ Gear | Settings | — | Opens settings drawer (right slide-in, 320 px wide) |
| ? Question | Help | F1 | Opens help drawer with keyboard shortcuts, glossary |
| ⛶ Expand | Toggle full-width | F11 | Hides chat panel, floor plan goes full window |

#### 5.1.4 Extension launcher button (lives in `chat_view.py`, not in floor plan)

- **Position**: in the existing chat extension UI, below "New Scene" button
- **Size**: 120×32 px button matching existing `chat_view.py` button style
- **Label**: "Open Floor Plan"
- **Icon**: 16×16 px grid-icon, `#76B900` accent
- **Behavior**: `webbrowser.open(f"http://localhost:8000/floorplan?session={session_id}")`
- **Visibility**: always when extension loaded

### 5.2 Left Toolbar (48 px wide, full-height, between palette toggle and canvas)

Fixed left edge of canvas area. Vertical icon strip, dark surface `#1A1C1F`, 1 px
right border `#2E3237`.

Icon button cell: 32×32 px (16 px icon centered), 8 px vertical gap between cells,
divider 1 px `#2E3237` between groups.

| Group / Icon | Tooltip | Shortcut | Cell pos (Y from top) |
|---|---|---|---:|
| **Group A: Modes** | | | |
| ↖ Cursor | Select | V | 8 px |
| ⊕ Plus | Place object | P | 48 px |
| ↔ Ruler | Annotate (dimension) | D | 88 px |
| 🔒 Pin | Lock/unlock object | L | 128 px |
| **Group B: History** (divider above, 1px line) | | | |
| ↩ Undo | Undo | Ctrl+Z | 184 px |
| ↪ Redo | Redo | Ctrl+Y | 224 px |
| **Group C: View** | | | |
| ⊞ Grid | Toggle grid | Ctrl+' | 280 px |
| ◎ Snap | Toggle snap | F9 | 320 px |
| 🎯 Fit all | Zoom to fit | Ctrl+Shift+H | 360 px |
| 🔍 Fit selected | Zoom to selection | F | 400 px |
| **Group D: Sync** | | | |
| ⬆ Sync to Sim | Push to Isaac Sim | Ctrl+S | 456 px |
| ⬡ View 3D | Switch to 3D scene tab | Ctrl+3 | 496 px |

Active mode highlight: 2 px solid `#76B900` left-border on the cell + 6% green fill
overlay. Inactive: no border. Hover: `#22262B` cell background.

Cell size **on touch devices** (`(hover: none)` media): 48×48 px (full toolbar width
becomes the touch target). Spacing increases to 4 px.

### 5.3 Object Palette (collapsible left panel)

**Position**: left-docked, slides over canvas. Below the toolbar.

**Collapsed state**: 64 px wide, shows class category icons only.
**Expanded state**: 200 px wide, shows icon + label + thumbnail.

**Toggle**: chevron button at top of palette, 24×24 px. Animation: 240 ms ease-out-cubic.

#### 5.3.1 Palette categories (expanded view)

```
┌──────────────────────────────────┐  ← 200 px wide
│  ⌄ ROBOTS                        │  ← category header, 24 px tall, 11px caps
│  ┌────────┬────────┐             │
│  │  Franka │  UR5e  │             │  ← item card, 88×72 px (icon + label)
│  │  ─────  │  ─────  │             │
│  │  thumb │  thumb │             │
│  └────────┴────────┘             │
│  ┌────────┬────────┐             │
│  │ UR10e  │ Kinova │             │
│  └────────┴────────┘             │
│  ┌────────┐                      │
│  │ Carter │                      │
│  └────────┘                      │
│                                  │
│  ⌄ CONVEYORS                     │
│  ┌────────┐                      │
│  │ Belt   │                      │
│  └────────┘                      │
│                                  │
│  ⌄ WORKPIECES                    │
│  ┌────────┬────────┐             │
│  │  Bin   │  Cube  │             │
│  └────────┴────────┘             │
│                                  │
│  ⌄ SENSORS                       │
│  ┌────────┬────────┬────────┐    │
│  │ Camera │ Lidar  │Station │    │
│  └────────┴────────┴────────┘    │
│                                  │
│  ⌄ FIXTURES                      │
│  ┌────────┬────────┬────────┐    │
│  │ Table  │ Ramp   │ Wall   │    │
│  └────────┴────────┴────────┘    │
└──────────────────────────────────┘
```

Item card sizing:
- **Card**: 88×72 px (or 64×64 in collapsed icon-only)
- **Padding**: 8 px
- **Icon**: 32×32 px isometric line illustration (Phosphor Icons-style)
- **Label**: 11 px, weight 500, `#DDDDDD`, 1 line, ellipsis
- **Spacing between cards**: 8 px

Drag behavior:
- Mouse-down on card → ghost rectangle appears under cursor (40% opacity, class fill)
- Drag onto canvas → ghost follows cursor, snap markers appear on grid points
- Release → place at snap point, label editor opens inline

Right-click on card → context menu:
- "Place at canvas center" (no drag)
- "Show docs" (opens robot/conveyor spec page)
- "Customize defaults..." (override default size, color, notes)

**Search bar at top of palette**: 184×28 px text input, magnifying-glass icon left,
"Search objects..." placeholder, 11 px font. Shortcut to focus: `/`. Filters palette
in real time.

### 5.4 Properties / Layers / Constraints (right dock)

**Position**: right-docked, full canvas height (between header and status bar).
**Default width**: 280 px. **Resizable**: drag left edge to resize, min 240 px, max
480 px.

#### 5.4.1 Tab strip

3 tabs at top of dock, each 32 px tall, equal-width:

| Tab | Width | Icon | Tooltip |
|---|---:|---|---|
| Properties | 33% | ☰ | Inspector for selected object |
| Layers | 33% | ⊟ | Layer visibility/lock/order |
| Constraints | 33% | ⛓ | Constraint list with status |

Active tab: 2 px bottom border `#76B900`, text `#DDDDDD`.
Inactive: no border, text `#8A8E92`.

#### 5.4.2 Properties tab content

Visible when ≥1 object selected. Empty state: "Select an object to inspect."

Layout (vertical sections, 12 px gap, 12 px section padding):

**Section 1: Identity (always shown)**
- Name: text input, full width minus 12px margin, 28 px tall, 12 px font
  ("Franka_1")
- Class: read-only label with class color swatch (8×8 px)
- ID: read-only monospace, 11 px, `#52575C`

**Section 2: Transform**
- Position X: numeric input, 100 px wide, monospace, with "m" suffix
- Position Y: numeric input, 100 px wide, monospace, with "m" suffix
- Rotation: numeric input, 100 px wide, with "°" suffix; rotate-handle button (24×24 px)
- Size W: numeric input, 100 px wide, with "m" suffix (locked for fixed-size classes)
- Size H: same
- All inputs: 28 px tall, 12 px font, monospace digits, right-aligned

**Section 3: Class-specific**
For robots: reach radius (read-only), effective pick radius (read-only), reach
visibility checkbox.
For conveyors: belt axis (radio: local_x / local_y), direction (radio: +1/-1),
surface velocity numeric.
For sensors: type, scan radius / frustum angle, resolution.

**Section 4: Material & physics (collapsible, default closed)**
- Material: dropdown (rubber / metal / plastic / glass / custom)
- Friction: numeric (0–10)
- Weight: numeric (kg)
- Sleep threshold: numeric (advanced)

**Section 5: Notes**
- Text area, full width, 80 px tall (resizable), 12 px font
- Placeholder: "Add notes for the agent (e.g., 'this robot is slower')..."
- Below text area: "Visible to agent ✓" toggle (default on)

**Section 6: Layer & lock**
- Layer dropdown
- Lock toggle (32×16 px iOS-style switch)

**Buttons at bottom of properties tab**: Apply (84×28 px, NVIDIA green), Reset
(84×28 px, ghost border). Apply commits any unsaved input changes; Reset reverts to
last saved state.

#### 5.4.3 Layers tab content

Vertical list, each row 32 px tall. Per row:
- 16×16 eye icon (visibility toggle), 8 px from left
- 16×16 lock icon, 8 px gap
- 12×12 color swatch, 8 px gap
- Layer name (12 px, `#DDDDDD`), flex-grow
- 16×16 chevron (collapse layer), 8 px from right

Drag-handle on far-left (20×32 px hit area) for reordering.

Bottom of panel:
- "+ New layer" button (full width minus margin, 28 px tall, 11 px text, ghost border)

#### 5.4.4 Constraints tab content

Vertical list of constraints. Per row:
- Status dot (8 px, left): green = satisfied, amber = warning, red = violated
- Type icon (16×16 px, e.g. ↔ for distance, ‖ for parallel)
- Description (one line, 12 px, ellipsis)
- Severity badge (24×16 px, "ERR" or "WARN")
- 3-dot menu (16×16 px, right): Edit / Disable / Delete

Bottom: "+ Add constraint" button (full width, 28 px tall).

### 5.5 Status Bar (24 px tall, fixed bottom)

Background `#181A1D` (matches `COL_BG_LIVE_STRIP` from chat_view.py). 1 px top border
`#2E3237`.

Layout (left to right):

#### 5.5.1 Left cluster — state

- **State dot**: 6×6 px filled circle, 12 px from left, vertically centered.
  Colors: idle `#52575C`, agent thinking `#76B900` pulsing, syncing `#409CFF` pulsing,
  verifying `#FFA800` pulsing, error `#FF4444` solid.
- **State text**: 11 px, `#DDDDDD` for active states, `#8A8E92` for idle. 8 px after
  dot. Ex: "Ready", "Planning…", "Syncing to Isaac Sim…", "Verifying…", "1 reach
  violation".
- **Object count**: 11 px, `#8A8E92`. Format: "· 5 objects · 2 constraints".

#### 5.5.2 Right cluster — view info & utilities

Right-aligned, 12 px from right edge, 16 px gaps between items:

- **Coordinates display**: "X: 1.23 m  Y: −0.45 m", 11 px monospace, `#8A8E92`.
  Updates on cursor move.
- **Zoom indicator**: "100%" or "1 cm = 8 px", 11 px monospace. Click opens zoom
  shortcut menu.
- **Mini-map toggle button**: 16×16 px, ⊞ icon. Toggles mini-map visibility.
  Auto-shown when content > 3× viewport.
- **Help button**: 16×16 px, ? icon. Opens help drawer.

### 5.6 Mini-map (floating, bottom-right corner of canvas)

- **Size**: 120×120 px
- **Position**: bottom-right of canvas, 16 px margin from canvas edge, above status bar
- **Background**: `#1A1C1F` with 1 px `#2E3237` border, 4 px border-radius
- **Content**: scaled-down rendering of all objects (dots colored by class), viewport
  rectangle outlined in `#76B900`
- **Behavior**: drag rectangle to pan camera. Double-click pans+zooms to that point.
- **Visibility**: auto when content > 3× viewport; toggleable via status-bar button.
  When content ≤ 3× viewport, hidden by default.

### 5.7 Floating Confirm Bar

Appears when agent has proposed mutations awaiting user confirmation.

- **Position**: floating at bottom-center of canvas, 24 px above status bar
- **Size**: auto-width based on content, max 600 px, 48 px tall
- **Background**: `#22262B` with 1 px `#76B900` border, 8 px border-radius, drop
  shadow `0 4px 16px rgba(0,0,0,0.4)`
- **Content layout**:
  - Left: 16×16 lightbulb icon `#76B900`
  - Reason text (12 px): "Agent proposed: add Bin_2 at (0.85, −0.5)"
  - Buttons (right cluster):
    - **Accept** (84×32 px, NVIDIA green fill `#76B900`, white text, weight 600)
    - **Reject** (84×32 px, ghost border `#FF4444` text)
    - **Refine…** (84×32 px, ghost border `#DDDDDD` text)

Animation: slide-up from below + fade-in over 240 ms (ease-out-cubic) on appear;
fade-out + slide-down 200 ms on dismiss.

Keyboard: Enter = Accept, Esc = Reject, R = Refine.

### 5.8 Toast Notifications

- **Position**: top-right of canvas, 16 px from edges, stacked top-down
- **Size**: 320 px wide, auto-height (typically 48 px), 8 px border-radius
- **Variants**:
  - Info: `#22262B` bg, blue accent line
  - Warning: amber accent
  - Error: red accent + persistent (no auto-dismiss)
- **Auto-dismiss**: success/info 2s, warning 4s, error never (manual close button
  16×16 px top-right of toast)
- **Animation**: slide-in from right 240 ms, fade-out 200 ms

### 5.9 Command Palette (modal)

Triggered by `Ctrl+K` or clicking logo.

- **Position**: centered horizontally, 120 px from top of viewport
- **Size**: 560 px wide × auto height (max 480 px)
- **Background**: `#1A1C1F` + drop shadow + 1 px `#2E3237` border
- **Content**:
  - Search input: 540 px × 40 px, monospace, "Type a command…" placeholder
  - Results list: each row 36 px tall, 11 px label + 11 px shortcut hint right-aligned
  - Recent commands shown at top when input empty

Examples of palette commands:
- "Add Franka robot"
- "Set reach override…"
- "Add distance constraint…"
- "Verify layout"
- "Open in Isaac Sim"
- "Export as canonical template"
- "Toggle layer: Annotations"

### 5.10 Context Menu (right-click)

- **Position**: at cursor location
- **Size**: 200 px wide, items 32 px tall each, 8 px padding
- **Background**: `#1A1C1F` + 1 px `#2E3237` border + drop shadow
- **Style**: NVIDIA dark menu — labels left, shortcut hints right

Right-click on **object** menu items:
- Rename
- Edit notes
- Set reach override (for non-Franka robots)
- Add distance constraint to…
- Lock / unlock
- Duplicate (Ctrl+D)
- Delete (Del)
- — divider —
- "Tell agent about this" (opens chat with this object pre-mentioned)

Right-click on **canvas** menu items:
- Paste (Ctrl+V) — if clipboard has object
- Add object… (opens command palette filtered to "Add")
- Zoom to fit (Ctrl+Shift+H)
- — divider —
- Toggle grid
- Toggle snap

### 5.11 Empty State Cards (initial canvas state)

When floor plan has zero objects:

- **Position**: centered on canvas
- **Layout**: row of 3 cards, 24 px gap
- **Card size**: 200×120 px, 12 px border-radius, `#1A1C1F` bg, 1 px `#2E3237` border
- **Card content**:
  - 32×32 px icon at top, `#76B900`
  - Title (13 px weight 600, `#DDDDDD`)
  - Description (11 px, `#8A8E92`, max 2 lines)

Cards:
1. **Add Robot** — "Place a robot to start. Drag from the palette or use Ctrl+K."
2. **Use Template** — "Start from a verified canonical template (CP-01..CP-05)."
3. **Ask Agent** — "Type a description and let the agent generate a layout."

Card hover: border → `#76B900`, icon opacity 100%, subtle scale 1.02 (160 ms).

Cards fade out individually with 60 ms stagger when first object placed.

### 5.12 Settings Drawer (right slide-in, 320 px wide)

Triggered by ⚙ in header.

Sections (each collapsible):

- **General**: theme (dark only in v1), default zoom on open, autosave interval
- **Grid & snap**: minor grid step (m), major grid step (m), snap threshold (m),
  show coordinate ruler
- **Verification**: realtime reach check (on/off), realtime overlap check (on/off),
  block commit on warning (on/off)
- **Agent integration**: auto-suggest canonical match (on/off), suggestion threshold
  (slider 0.45..0.85), show agent reasoning panel (on/off)
- **Keyboard shortcuts**: searchable list with edit (advanced)
- **Reset to defaults**: button

Each setting row: label left (12 px), control right. 36 px tall.

Footer: Save (84×32 px green) and Cancel (84×32 px ghost).

### 5.13 Help Drawer (right slide-in, 320 px wide, separate from Settings)

- Keyboard shortcuts (full table)
- Object reference (footprint sizes, reach radii per class)
- Color vocabulary
- Glossary
- Link to docs
- "What's new" changelog

---

## 6. Interaction Patterns

### 6.1 Selection

- **Single click**: select one object, deselect all others
- **Shift+click**: additive toggle (add/remove from selection)
- **Ctrl+click**: reserved for sub-element selection (future)
- **Marquee**: click empty canvas + drag
  - **Right-to-left** = crossing select (touches = selected) — AutoCAD convention
  - **Left-to-right** = window select (fully enclosed = selected)
- **Lasso**: omitted (not needed for rectangular footprint domain)

Visual: selected object outlined `#76B900` 2 px solid; multi-select 2 px dashed 8/4;
hover-pre-select 1 px `#3D8B00` + fill rgba(118,185,0,0.06).

### 6.2 Modify operations

| Op | Trigger | Behavior |
|---|---|---|
| Translate | Left-drag | Free move; arrow keys nudge by minor grid (0.1 m); Shift+arrow = major grid (1.0 m) |
| Translate (exact) | Tab while selected | Inline coord input "X, Y" or "@dx, @dy" relative; Enter commits |
| Rotate | Drag rotate-handle (top of bbox) | Free rotate; Shift snaps to 15° |
| Rotate (exact) | R key while selected | Inline degrees input |
| Scale (proportional) | Corner handle drag | Default proportional; Shift releases proportion |
| Scale (1-axis) | Edge midpoint drag | Resize on one axis only |
| Copy | Ctrl+C | Object → clipboard |
| Paste | Ctrl+V | At cursor or original coords (canvas-empty area = original) |
| Duplicate | Ctrl+D | In-place + offset (+0.1, +0.1 m) |
| Mirror | Edit menu | Mirror H or Mirror V along centroid |
| Array | Right-click → "Create array" | Linear (count + spacing) or circular (count + radius) |

### 6.3 Snap mechanics

**Grid snap**: always-on default, snaps to minor grid (0.1 m). Toggle F9. Visual: faint
dot at each minor grid intersection becomes amber on snap-trigger.

**Object snap (OSnap)** — five snap point types, color-coded markers:
- **Endpoint** (corner): cyan crosshair 10×10 px, 1.5 px stroke
- **Midpoint** (edge midpoint): amber open triangle 10 px tall
- **Edge** (closest point on edge): grey small rectangle 6×4 px
- **Center** (centroid): green open circle 8 px diameter
- **Nearest existing constraint anchor**: purple dot 6 px

Snap markers fade in 80 ms, fade out 120 ms.

**Polar tracking**: 0°, 45°, 90°, 135°, 180° (and negatives). Dashed alignment ray
extends from origin during drag. Shift forces 45° increments.

**Smart guides** (Figma-style): teal dashed lines when dragged object's edge or
center aligns with another object's. Equal-spacing indicators for 3+ collinear
objects with arrows + measurement.

**Snap precedence**: object > polar > grid (closest wins). 8 px screen-space
threshold at 100% zoom.

### 6.4 Dimension lines

- **Tool**: D key or toolbar dimension button
- **Workflow**: click first point → click second point → drag to place dimension line
- **Style**: extension line 1 px `#8A8E92` dashed 3/3; dimension line 1 px `#C8CC80`
  solid; 8 px filled architectural-tick arrowhead; 11 px `#DDDDDD` text centered above
  line, monospace numerals; 4 px gap from object to extension; 3 px overshoot past arrowhead
- **Persistence**: dimensions are first-class objects in the `annotations` array.
  They update live when measured objects move.
- **Distance constraint upgrade (v2)**: double-click dimension annotation → input
  field opens → typed value locks the constraint, badge turns yellow

### 6.5 Distance constraint workflow

- **Add via UI**: select two objects → toolbar "Add constraint" → modal with type
  selector (distance / alignment / angle / reach / bounds)
- **Add via right-click**: object → "Add distance constraint to..." → click second object
- **Edit**: double-click constraint in Constraints panel → inline edit of min/max
- **Remove**: select in panel → Delete key or 3-dot menu
- **Visual**: constraint indicator drawn between anchors (thin dashed `#76B900` 1px
  40% opacity), badge with value at midpoint

### 6.6 Pan / zoom

- **Zoom**: scroll wheel, cursor-anchored. Ctrl+= / Ctrl+- center-zoom. Ctrl+0 fit-all.
- **Pan**: middle-button-drag, Space+drag, two-finger trackpad
- **Limits**: zoom 0.1x..16x; below 0.3x minor grid hidden; above 3x sub-minor (0.01 m)
  grid appears

### 6.7 Undo/redo

- **Granularity**: each discrete action = 1 entry. Continuous drag (start to release) =
  1 entry even if many intermediate positions. Text input commits on focus loss.
- **Depth**: 100 entries
- **Linear only**: no branching; redo cleared on new action after undo
- **Visual**: undo toast appears on destructive actions (Delete) with prominent
  Undo button (4-second persistence)

### 6.8 Multi-select editing

Numeric fields in properties panel:
- Same value across selection → show value
- Different values → show "—" (mixed indicator)
- Enter new value → applied as absolute to all selected objects
- Arrow-key nudge always applies relative displacement to all selected

Selection bounding box: single outer box around all selected; rotate/scale handles
operate on combined box (Figma convention).

---

## 7. User Flows

### 7.1 Cold-start (empty canvas)

1. User opens Floor Plan tool from extension button (`webbrowser.open(...)`)
2. Canvas empty; three quick-start cards centered (§5.11)
3. User clicks "Add Robot" or drags Franka from palette
4. Robot appears on canvas; reach circle (0.855 m) renders; label editor pops auto-
   selected; user types name "Franka_1" and presses Enter
5. User drags Conveyor from palette; resize handles appear; user drags one end to
   set length to 0.9 m
6. Constraint hint: amber line between Franka centroid and conveyor near-end ("0.42m")
7. User drags Bin from palette to position right of Franka
8. Reach overlay shows bin within reach (green). All objects committed (full opacity).
9. User clicks ⬆ Sync to Sim (toolbar) or types "build it" in chat
10. `verify_pickplace_pipeline` runs; if pass, `apply_floor_plan_to_scene` triggers
    canonical or free-form build; status bar shows "Syncing to Isaac Sim…"
11. Build complete; floor plan elements gain green ✓ marker; status: "Scene ready."
12. User switches to 3D Scene tab to see the result

**Total: 7 drags + 2 clicks + 1 typed action = productive without hand-holding.**

### 7.2 Agent-prompt-driven

1. User in Chat view types: "Build me a sorting station with 2 cubes, 2 bins, 1 Franka."
2. Status bar: "● Planning…" (amber pulse)
3. Floor Plan tab pulses with green dot (background activity indicator)
4. Agent generates `FloorPlanSpec` JSON; floor plan view auto-switches via 380ms
   cross-dissolve; objects appear in **proposed state** (40% opacity, blue-tinted
   outlines)
5. Chat bubble shows agent reply: "Here's a sorting layout. Franka at origin,
   conveyor 1.2m along x-axis, red bin at (0.8, 0.4), blue bin at (0.8, −0.4). All
   items within reach."
6. **Floating confirm bar** appears at bottom of canvas: [Accept] [Reject] [Refine…]
7. User clicks **Accept** → ghost objects animate to full opacity (200 ms) → status:
   "Syncing to Isaac Sim…"
8. After ~8s, status: "Scene ready." User switches to 3D Scene tab.

Variant flow — user clicks **Refine…**:
- Ghost objects become draggable while staying ghost-styled
- User selects Franka_1, types "0.30" in X field → robot moves 30 cm right
- Reach circle redraws; both bins still inside reach
- User clicks "Accept layout" in floating bar → committed → sync proceeds

### 7.3 Hybrid (manual + agent assist)

1. User has manually placed Franka + conveyor + bin
2. User types in chat: "Add a second bin on the other side of the conveyor."
3. Agent reads floor plan via `read_floor_plan()`; generates mutation; calls
   `update_floor_plan(mutations=[{op: "add", element: "bin_2", position: [0.85, -0.5]}])`
4. New bin appears in **ghost state** (proposed); existing items stay full opacity
   (committed)
5. Floating confirm bar: "Add Bin_2?" [Accept] [Reject]
6. User clicks Accept → bin transitions to committed state

If agent proposes moving an existing item: that item gets **amber outline** (warning-
modified state), delta annotation "(was 0.3, 0.4) → (0.5, 0.4)".

### 7.4 Three case studies (full sequences)

#### Case A: Brand-new user, Franka pickplace from scratch

```
1. Opens Floor Plan. Sees empty canvas + 3 quick-start cards.
2. Clicks "Build from blank." Cards fade out.
3. Drags Franka from palette → drop at (0,0). Label editor: types "Franka_1".
4. Reach circle (0.855m dashed) renders.
5. Drags Conveyor → drop at (-0.4, 0.4). Length handle appears.
6. Drags handle until conveyor spans -0.8m to 0.1m. Live label: "0.9m".
7. Constraint hint: "Franka_1 ↔ Conveyor near-end: 0.42m (within 0.700m reach)".
8. Drags Bin → drop at (0.7, -0.4). Distance hint: "Franka_1 ↔ Bin: 0.81m (within reach)".
9. Types in chat: "Looks buildable?"
10. Agent reads, replies: "Conveyor in pick zone, bin in reach. Ready to build?"
    Confirm bar: [Build] [Adjust] [Discard]
11. Clicks Build. Status: "Syncing to Isaac Sim…"
12. After 8s: "Scene ready." Floor plan items get green ✓.
13. Switches to 3D Scene. Sees Franka + conveyor + bin + 0 cubes (no cubes specified).
14. Goes back to Floor Plan, drags 4 cubes onto conveyor, hits Sync to Sim again.
15. Cubes appear in scene. Done.
```

#### Case B: Agent builds, user adjusts

```
1. Chat: "Build sorting station, 2 cubes, 2 bins, 1 Franka."
2. Status: "Planning…" Floor plan tab pulses.
3. Auto-switch to floor plan. Ghost objects appear: Franka@(0,0), conveyor@(0,0.6),
   red bin@(0.8,0.4), blue bin@(0.8,-0.4), 2 cubes on conveyor.
4. Chat: "Sorting layout ready. All within reach. Build it?" Bar: [Build][Adjust][Discard]
5. User clicks Adjust. Bar closes; objects draggable, ghost-styled. Floating accept-bar
   appears: [Accept layout][Discard changes]
6. User selects Franka_1. Properties panel: position X = 0.00.
7. User types "0.30" in X field, Enter. Franka shifts 30cm right. Reach circle redraws.
8. Both bins still inside reach (green).
9. User clicks Accept layout. Objects animate to full opacity.
10. Status: "Syncing…" → "Scene ready."
```

#### Case C: Constraint tight, agent flags issues

```
1. Chat: "Build compact pickplace in 2x2m, 4 cubes, 1 Franka, 1 conveyor, 1 bin."
2. Agent detects CP-01 default (3m conveyor) won't fit 2x2.
3. Agent proposes: conveyor shortened to 1.0m, Franka@(0,0), bin@(0.7,0.3).
4. Ghost layout in floor plan. Chat: "Default conveyor won't fit. I shortened to
   1.0m. With this, 3 cubes stage reliably; 4th falls outside reach. 3 or attempt 4?"
   Bar: [3 cubes — Build][Try 4 cubes][Discard]
5. User clicks "Try 4 cubes."
6. Agent retries; cube_4 ends up at (0.91m from base) > 0.855m reach. Cube_4 gets
   red glow; reach-arc overlay shows the 0.855m boundary.
7. Chat: "Cube_4 at 0.91m exceeds Franka's reach (0.855m). Options: (a) remove cube_4,
   (b) shrink cubes 5cm→3cm, (c) shift Franka 6cm along X." Three inline buttons.
8. User clicks (a). Cube_4 fades out. Confirm bar: "3-cube layout valid. Build?"
9. User Build. verify_pickplace_pipeline: pipeline_ok=true. Status: "Scene ready."
```

---

## 8. Agent ↔ Floor Plan Protocol

### 8.1 Tool schemas (LLM-visible)

Each registered in `tool_schemas.py` and `tool_executor.py`. Placement in
`_ALWAYS_TOOLS` vs `ALLOWED_AFTER_INSTANTIATE`:

| Tool | Always | Post-instantiate |
|---|:---:|:---:|
| `open_floor_plan(prompt?, template_id?)` | ✓ | ✓ |
| `read_floor_plan(detail_level?)` | ✓ | ✓ |
| `update_floor_plan(mutations, reason)` | ✓ | — |
| `commit_floor_plan()` | ✓ | — |
| `apply_floor_plan_to_scene(template_id?, force_freeform?)` | ✓ | — |
| `query_floor_plan_metric(metric, args)` | ✓ | ✓ |

JSON Schema definitions: see Agent 6's report (full text appended to next-session
implementation context).

### 8.2 Spec generation (agent → floor plan)

Format: structured `FloorPlanSpec` JSON (matches §3.5 schema), **all-at-once**, not
streamed. Agent streams chat reasoning while spec is computed server-side; spec
delivered as single payload at end of turn. Avoids partially-rendered floor plan
flicker.

Auto-commit thresholds (skip confirm bar):
- User explicit single-object instruction ("move robot_1 0.5m right")
- Single-object attribute change ("set conveyor velocity to 0.3")

Always-propose (require confirmation):
- Full-scene generation from prompt
- Multi-object structural changes
- Template hard-instantiate path

### 8.3 State injection into agent context

**On-demand only.** Agent calls `read_floor_plan()` when needed. Orchestrator does
NOT auto-inject per turn. Avoids token bloat (which the Track-C 9.2 analyzer
already showed is rarely the actual 503 driver, but principle holds for general
context discipline).

**Compact summary format**:
```
FloorPlan (committed, 5 objects):
  robot_1: franka_panda @ (0.0, 0.0) rot=90° | note: "the slow one"
  robot_2: franka_panda @ (1.2, 0.0) rot=90°
  conveyor_1: conveyor @ (0.0, 0.6) 3.0×0.4m vel=(0.2,0,0)
  bin_1: bin @ (0.0, -0.4) 0.3×0.3×0.15m
  bin_2: bin @ (1.2, -0.4) 0.3×0.3×0.15m
Constraints: footprint [(-1.5,-1.5)→(3.0,1.5)]
Validation: OK
```

~200 chars/object × 10 objects = ~2 kB. Safe within measured 200 kB tool-result
budget.

**Recent mutations log** (returned with `read_floor_plan()`):
```json
"recent_mutations": [
  {"type": "move", "id": "robot_1", "from": [0.0, 0.0], "to": [0.3, 0.0],
   "source": "user_drag"}
]
```

Cleared on read.

### 8.4 Confirmation loop

After agent mutation: spec status → `proposed`. UI shows ghost overlay + floating
confirm bar (§5.7). User actions:
- **Accept** → commit_floor_plan() → status `committed`, mutations applied
- **Reject** → revert to last committed; agent receives "User rejected" in next turn
- **Refine…** → text field opens; user types refinement; goes as next user turn,
  status remains `proposed`

Agent does NOT ask "are you satisfied?" in chat text. UI mechanic handles it. Chat
reply ends with factual summary: "I've placed 2 Frankas facing a shared conveyor."

### 8.5 Object notes — agent behaviors driven by them

| Note | Effect |
|---|---|
| `"this robot is the slow one"` | Agent uses for low-throughput routes in multi-robot plans |
| `"must be reachable from above only"` | Agent treats with overhead-approach IK constraint |
| `"DO NOT MOVE — fixed anchor"` | Agent treats position as immutable |
| `"priority 1 — process first"` | Agent configures source_paths ordering |
| `"high friction substrate"` | Agent applies rubber physics to adjacent objects |

Notes are plain strings — no schema enforcement. Agent reads as natural language.
Appears in compact summary per turn.

### 8.6 Canonical-template suggestion

**When**: floor plan reaches "complete enough" (≥1 robot + ≥1 pick source + ≥1 bin,
no overlap), user commits a change, cooldown ≥3 user actions since last suggestion.

**Match score**: combined embedding similarity (text query auto-generated from floor
plan) × 0.7 + structural similarity (count-match of object types) × 0.3.

**Threshold to surface suggestion**: 0.72 (lower than hard-instantiate gate at 0.85).

**UX**: non-intrusive banner below canvas: "87% match to CP-02 (multi-robot
assembly line) — Use this template?" Buttons: [Use template] [Tell me more]
[Dismiss].

- **Use template**: hard-instantiate path fires. Floor plan locks (no further edits)
  during build.
- **Tell me more**: agent explains differences in chat
- **Dismiss**: suppresses suggestions for this session

### 8.7 Failure modes

| Failure | Handling |
|---|---|
| Agent generates invalid layout (overlap, out-of-reach) | `update_floor_plan` validates synchronously before setting `proposed`. Returns `{valid: false, issues: [...]}`. Agent revises. Floor plan never renders proposed state it knows is invalid. |
| User makes invalid direct edit | Applied immediately (user intent trusted), red highlight + tooltip. No block. Validation advisory for direct manipulation, hard-blocking only for agent-proposed. |
| Agent + user simultaneous edit | Not possible by protocol. While proposed overlay showing, direct manipulation frozen with tooltip "Accept or reject agent proposal first". |
| Agent produces wrong proposal, user rejects | Recorded in history as `{type: "floor_plan_rejected", reason: "user rejected"}`. Agent sees on next turn. No auto-retry; next turn is fresh user message. |
| Kit RPC build fails after commit | `apply_floor_plan_to_scene()` returns error. Spec status reverts `building` → `committed`. Toast with specific failure. Floor plan unchanged. |

---

## 9. Orchestrator Integration

### 9.1 Where floor plan data enters

**Primary mode**: pre-prompt. User builds layout; floor plan IS the spec; "Build
Scene" button generates a synthesized prompt + `context["floor_plan"]` blob in the
existing `ChatMessageRequest.context`.

Injection point: `orchestrator.py:795` (after `is_kit_rpc_alive()` check). New branch:
```python
if context.get("floor_plan"):
    floor_plan_context_text = _format_floor_plan_for_llm(context["floor_plan"])
    scene_context_text += "\n\n" + floor_plan_context_text
```

`_format_floor_plan_for_llm` produces compact summary (§8.3). Then `distill_context`
(orchestrator.py:1033) handles unchanged.

### 9.2 Auto-query for canonical retrieval

Floor plan's structure is mapped to a goal-text query:
```
{n_robots=1, n_conveyors=1, n_bins=1}
  → "pick-and-place cell with Franka robot, conveyor belt, destination bin"
{n_robots=2, n_conveyors=3, n_bins=1}
  → "two-robot assembly line with three conveyor stages and single output bin"
```

This query feeds `retrieve_templates_with_scores(query)` at `orchestrator.py:881`.
No change to `template_retriever.py`. Existing `_canonical_min_sim=0.45` and
`_canonical_min_margin=0.20` thresholds apply.

### 9.3 Param overrides via T2 substitution

When user accepts canonical, `param_overrides` passed to
`execute_template_canonical(top["template"], param_overrides=floor_plan_params)` at
`canonical_instantiator.py:364`. Existing `substitute_template_params` (line 326)
handles `{{name}}` substitution.

Example for CP-01:
```python
floor_plan_params = {
    "robot_position": "[0, 0, 0.75]",
    "n_cubes": 4,
    "conveyor_position": "[0.0, 0.4, 0.78]",
}
```

### 9.4 verify_args path patching

If user renamed objects (e.g., Franka → FrankaLeft), the template's hardcoded paths
(`/World/Franka`) must be patched. New helper: `_patch_verify_args(verify_args,
floor_plan_state)` rewrites `robot_path`, `pick_path`, `place_path` from the floor
plan's name assignments.

### 9.5 Verify integration — real-time vs commit

**Realtime (client-side, no Kit RPC)**:
- `footprint_within_bounds`: pure AABB. <1 ms. Red highlight on violation.
- `reach_check`: Euclidean distance vs robot reach. Real-time during drag. Red ring
  when out of reach.

**On-commit (Kit RPC, expensive)**:
- `verify_pickplace_pipeline` (form gate): conveyor_active, controller_installed,
  cube_source_bridged. ~2 s. Pre-build validation.
- `simulate_traversal_check` (function gate): 60 s simulation. Post-build only.

### 9.6 New HTTP routes (in `service/isaac_assist_service/floor_plan/routes.py`)

```
GET    /api/v1/floor_plan/{session_id}
       → FloorPlan JSON or blank
POST   /api/v1/floor_plan/{session_id}/patch
       body: {ops: [{id, field, value}, ...]}
       → 202, merges to disk, emits SSE floor_plan_updated
POST   /api/v1/floor_plan/{session_id}/build
       body: {dry_run?, template_override?}
       → triggers canonical or freeform build
POST   /api/v1/floor_plan/{session_id}/sync_from_stage
       → reads Kit RPC stage, updates floor plan from world transforms
DELETE /api/v1/floor_plan/{session_id}
       → reset to blank
GET    /api/v1/floor_plan/templates
       → list canonical templates with floor-plan-loadable positions
```

### 9.7 Layout-intent detection

When user prompt indicates layout intent ("build", "layout", "station", "assembly
line", "2D", "footprint", combined with `intent="patch_request"` AND
`complexity="complex"` AND `multi_step=True`), emit SSE `floor_plan_suggested` event.
Frontend shows banner: "Open Floor Plan?" User opts in.

Detection function `detect_layout_intent(message)`: keyword regex first (cheap),
embedding match as fallback. Isolated from intent classifier.

---

## 10. Edge Cases, Accessibility, Failure Handling

### 10.1 Severity / frequency / handling matrix

(Excerpted; full matrix in Agent 7's report.)

| Case | Sev | Freq | Handling |
|---|---|---|---|
| Object overlap during drag | M | often | Warn; red highlight; allow |
| Object overlap on commit-to-3D | H | sometimes | Block; modal listing offending pair |
| Robot reach not covering pick zone | H | often | Warn realtime; block at commit |
| Inconsistent constraints | H | rare | Detect on edit; highlight all in cycle; block new constraints until resolved |
| Constraint violated by manual drag | M | sometimes | Auto-snap if within 0.3 m; revert otherwise; toast |
| 50+ objects | M | sometimes | 100 ms debounce on overlap checks; mini-map auto-shows |
| Backend down (port 8000) | H | sometimes | Persistent red banner; canvas works offline; chat disabled |
| Kit RPC unreachable | H | sometimes | Specific failure messages; commit-to-3D blocked |
| Stale state after idle | M | sometimes | Heartbeat on focus-regain; offer reload-from-server vs keep-local |
| Autosave / recovery | H | often | localStorage every 30s; recovery banner on reload if newer than last save |
| Wrong-window focus shortcuts | M | sometimes | Shortcuts consumed only when canvas-container has focus |
| Constraint cycle | H | rare | Topological-sort detection; block + visualize cycle path |
| Agent loops on infeasible | H | rare | Hard 6-turn limit; surface partial layout for manual continuation |
| Agent timeout (Gemini 503) | H | sometimes | One retry after 2s; surface error; log to provider_incidents.jsonl |
| Concurrent tabs | H | rare | localStorage tab-lock with timestamp; second tab read-only |

### 10.2 Accessibility requirements

- **Keyboard-only navigation**: tab order toolbar → palette → canvas → properties.
  Arrow keys move selection 0.1 m (Shift+arrow = 1.0 m). Focus indicators 2 px solid
  outline, ≥3:1 contrast.
- **Screen reader**: canvas has `role="application"`. Live region (`aria-live="polite"`)
  announces add/move/delete and constraint violations. Sidebar list is primary
  screen-reader interface (each object has `aria-label`).
- **High-contrast mode**: `prefers-contrast: more` switches palette; objects use 3 px
  white-on-black borders, error states use pattern fills (not color alone).
- **Color-blind safe**: object outlines use shape + label, not color alone. Reach
  status uses stroke-weight in addition to color. Errors use hatched overlay.
- **Reduced motion**: `prefers-reduced-motion: reduce` disables drag-snap animations,
  constraint pulse, toast slides. Replace with instant state changes.
- **Touch targets**: 44×44 px minimum on `(hover: none)` devices.

### 10.3 Browser support

- **Primary**: Chrome latest on Linux (Anton's environment)
- **Tested**: Firefox, Safari (with Konva polyfills as needed), Edge (Chromium)
- **Mobile**: read-only view; placement disabled; banner: "Editing requires tablet/desktop"

---

## 11. Implementation Phasing

Suggested phases for next session(s) to evaluate. Phase boundaries chosen to land
working software at each step.

### Phase 1 — Skeleton (3-5 days)

- Vite + Konva SPA stub at `web/floor-plan-ui/`
- FastAPI mount at `/floorplan`
- Extension button in `chat_view.py` opens browser
- Empty canvas + grid + pan/zoom
- Object palette (drag from sidebar drops static rect)
- Local Zustand store, no persistence yet
- Status bar (idle only)

**Done condition**: drag a Franka rectangle onto canvas, see it persist in browser
session, no backend integration yet.

### Phase 2 — Backend + persistence (2-3 days)

- `service/isaac_assist_service/floor_plan/routes.py` with GET/POST/DELETE
- `workspace/floor_plans/{session_id}.json` persistence
- SSE `floor_plan_updated` events
- Frontend sync.ts wired to backend
- Reload restores state

**Done**: floor plan persists across reload, two browser tabs see changes via SSE.

### Phase 3 — Domain model + validation (3-4 days)

- Full object schema (§3.2) on client
- Per-class default sizes, reach radii
- Realtime overlap + reach checks
- Properties panel inspector
- Layers panel
- Constraint creation UI

**Done**: place a robot + conveyor + bin; reach-circle visualization; overlap
warnings; layer toggle.

### Phase 4 — Canonical translation (2-3 days)

- `floor_plan_to_tool_sequence(plan)` translator
- `apply_floor_plan_to_scene` route + tool
- Hard-instantiate path integration (`canonical_instantiator` param_overrides)
- verify_args path patching helper
- Build button triggers Kit RPC; SSE progress events

**Done**: build CP-01 from floor plan; identical execution to current CP-01.

### Phase 5 — Agent integration (4-5 days)

- 6 new tool schemas (§8.1)
- `read_floor_plan`, `update_floor_plan`, `commit_floor_plan`, `apply_floor_plan_to_scene`,
  `query_floor_plan_metric`, `open_floor_plan` handlers
- `_format_floor_plan_for_llm` for compact summary
- Layout-intent detection in orchestrator
- Floating confirm bar UI
- Canonical-match suggestion banner
- "Refine" flow for agent-proposed states

**Done**: type "build sorting station" → agent generates layout in floor plan →
user accepts → CP-03 builds.

### Phase 6 — UX polish (3-4 days)

- Smart guides (Figma-style)
- Polar tracking
- Snap markers (5 types, color-coded)
- Dimension lines + annotations
- Mini-map
- Command palette (Ctrl+K)
- Keyboard shortcuts full set
- Empty state + onboarding

**Done**: matches the visual language §5 in full.

### Phase 7 — Edge cases + accessibility (2-3 days)

- Constraint cycle detection
- Autosave + recovery banner
- Focus management
- Screen reader live region
- Reduced motion
- High-contrast mode
- Touch optimization

**Done**: full §10 spec covered.

### Phase 8 — Round-trip + canonical export (2-3 days)

- Load CP-01..CP-05 into floor plan
- Save floor plan as new canonical (FP-* templates)
- T2 parameter substitution wired
- Round-trip validation tests

**Done**: open CP-01, save with no changes, build → identical to current CP-01.

**Total estimate**: ~21-30 working days for full spec implementation. Phases 1-4
deliver a useful tool standalone (~10-15 days); Phases 5-8 add agent integration
and polish.

---

## 12. Open Questions for Next Session

1. **Hosting confirm**: web SPA at port 8000 vs Kit popup with omni.ui. Web SPA
   recommended; final call rests with implementer. Tradeoff: web is more flexible
   but breaks "everything inside Kit" feel slightly.

2. **Konva.js vs alternatives**: Konva recommended; Fabric.js viable backup. Not a
   reversible choice late in implementation — decide before Phase 1.

3. **Touch input parity**: full feature parity on tablet desired but doubles UX
   work. v1 may ship desktop-only with read-only mobile view.

4. **Multi-user collaboration**: not in v1. CRDT considered for state sync but
   rejected as premature. If multi-user becomes a feature, switching from
   last-writer-wins to CRDT is a non-trivial refactor.

5. **Light mode**: rejected for v1 (Kit is dark-only). If floor plan exposed
   standalone outside Kit, light mode can be added by inverting surface tokens.

6. **Sketch-input bridge to MULTIMODAL-01**: deferred MULTIMODAL-01 task could feed
   parsed sketches into floor plan as initial state. Not in this spec, but the data
   model in §3 is compatible.

7. **Verify-pickplace-pipeline real-time hook**: client-side reach + overlap is
   cheap and recommended. Full verify pipeline runs only on commit. Confirm
   threshold for what's "cheap enough" client-side.

8. **Robot reach 3D projection**: §3.3 keeps reach as flat circle. If future use
   cases need 3D workspace silhouettes, the schema reserves
   `reach.projection_mode: "circle" | "3d_iso"` (default "circle").

9. **Template export format**: round-trip with CP-01..CP-05 specified, but new
   floor-plan-derived templates need a format. FP-{hash}.json proposed; naming
   convention final call.

10. **Agent confirmation for chat-driven mutations**: should "move robot 0.5m" via
    chat auto-commit (since user explicitly requested) or always require visual
    confirm? Spec recommends auto-commit for single-object explicit instructions;
    confirm for multi-object structural changes.

11. **Performance ceiling**: 500-object target is conservative. Should we test at
    1000+ before deciding final ceiling? Konva supports it; UX may not.

12. **Settings persistence**: per-user settings stored where? Backend per-session,
    localStorage, or Kit user-prefs? Spec defers; recommend localStorage for v1.

---

## 13. File / Path Inventory

For next session reference:

### New files

```
service/isaac_assist_service/floor_plan/__init__.py
service/isaac_assist_service/floor_plan/routes.py
service/isaac_assist_service/floor_plan/models.py
service/isaac_assist_service/floor_plan/translator.py
service/isaac_assist_service/floor_plan/validator.py
service/isaac_assist_service/floor_plan/persistence.py

web/floor-plan-ui/                     ← whole new SPA, ~15-30 source files
web/floor-plan-ui/src/App.tsx
web/floor-plan-ui/src/canvas/FloorPlanCanvas.tsx
web/floor-plan-ui/src/canvas/layers/ (Grid, Geometry, Annotation, Overlay)
web/floor-plan-ui/src/canvas/shapes/ (per-class shape components)
web/floor-plan-ui/src/canvas/snap.ts
web/floor-plan-ui/src/store/floorPlanStore.ts
web/floor-plan-ui/src/store/commands.ts
web/floor-plan-ui/src/store/sync.ts
web/floor-plan-ui/src/ui/Toolbar.tsx
web/floor-plan-ui/src/ui/ObjectPalette.tsx
web/floor-plan-ui/src/ui/PropertiesInspector.tsx
web/floor-plan-ui/src/ui/LayersPanel.tsx
web/floor-plan-ui/src/ui/StatusBar.tsx
web/floor-plan-ui/src/ui/ConfirmBar.tsx
web/floor-plan-ui/src/ui/CommandPalette.tsx
web/floor-plan-ui/src/api/floorPlanApi.ts
web/floor-plan-ui/vite.config.ts
web/floor-plan-ui/package.json

workspace/floor_plans/                 ← runtime state, gitignored
```

### Modified files

```
service/isaac_assist_service/main.py                 ← StaticFiles mount
service/isaac_assist_service/chat/orchestrator.py   ← lines 704, 795, 881, 907,
                                                       1148 (5 hooks)
service/isaac_assist_service/chat/canonical_instantiator.py
                                                     ← line 73 (ALLOWED_AFTER_INSTANTIATE
                                                       additions), 281 (verify_args
                                                       patching), 364 (param_overrides)
service/isaac_assist_service/chat/tools/tool_executor.py
                                                     ← +6 handlers
service/isaac_assist_service/chat/tools/tool_schemas.py
                                                     ← +6 tool schemas
exts/isaac_5.1/omni.isaac.assist/ui/chat_view.py    ← +Open Floor Plan button
exts/isaac_6.0/omni.isaac.assist/ui/chat_view.py    ← +Open Floor Plan button
.gitignore                                           ← workspace/floor_plans/
requirements.txt                                     ← (no new Python deps;
                                                       Vite/Konva are JS-side only)
```

### Reference files (existing, read for context)

```
service/isaac_assist_service/chat/orchestrator.py
service/isaac_assist_service/chat/canonical_instantiator.py
service/isaac_assist_service/chat/tools/template_retriever.py
service/isaac_assist_service/chat/routes.py
workspace/templates/CP-01.json (and CP-02..CP-05)
docs/specs/2026-05-08-session-summary-and-handoff.md
docs/specs/2026-05-08-canonical-task-gap-analysis.md
docs/qa/tasks/MULTIMODAL-01.md (deferred — floor plan is parallel/replacement work)
```

---

## End of Spec

Estimated total length: ~6800 words / ~32 KB. Implementation effort: 21-30 days as
phased above.

**Author position**: this spec is comprehensive enough to begin implementation
phasing immediately. Open questions in §12 should be resolved in a brief follow-up
session before Phase 1 begins, but none of them are blockers — they are decisions
that affect specific implementation choices, not the overall architecture.

The next session evaluates this spec and decides go/no-go on each phase.
