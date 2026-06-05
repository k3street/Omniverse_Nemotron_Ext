# Sketch Tool UX Research — Best Practices 2026

**Date:** 2026-05-14
**Researcher:** Opus agent
**Scope:** UX patterns for the robot-workcell 2D canvas (drag-drop
17-60 object classes → LayoutSpec)

## Executive Summary

Strongest pattern blend: **Figma's discoverability + Blender's modal
speed + Linear's command palette + AutoCAD's annotation conventions**.

Concrete recommendations:

1. **Single-key left-hand shortcuts** (Q/W/E/R/A/S/D/F/Z/X/C/V) per
   Photoshop/Figma convention — every primary verb on a no-modifier
   key reachable by the left hand without leaving home row.
2. **Cmd+K command palette as universal escape hatch** — solves
   "60 classes + dozens of actions" without bloating toolbar.
   Industry-standard in Linear, Figma, Notion, Vercel, Raycast.
3. **Hover-hint shortcut labels everywhere** (Figma) + `?`
   cheat-sheet overlay + toast-style "Tip: press R" after Nth
   UI-click of same action.
4. **Primary toolbar = 7±2 buttons** (Miller's law). Long tail goes
   to palette/command-K. Group related tools into stickies cycling
   on repeated keypress (Photoshop Shift+key).
5. **60-class palette: searchable + categorized + recents.** Never
   flat 60-item list. Collapsed category sidebar + always-on search
   + "recent + favorites" strip at top.
6. **Numbered callouts with auto-legend** (AutoCAD/NCS standard) —
   circle-with-number markers on canvas, auto-numbered, sidebar
   legend updates live.
7. **Radial/pie menus ONLY for one specific power-user gesture**
   (e.g. hold Z → view pie). Not primary affordance — research shows
   pie menus are slower to learn than linear menus.

**Top 3 references to study:**
- **Figma** — shortcut discoverability via hover hints + command palette
- **Blender** — left-hand-only modal workflow + pie menus + remappable
- **tldraw** — open-source SDK with clean tool/action/shortcut separation

---

## 1. Left-hand keyboard shortcut conventions

### Major tools

- **Photoshop**: V/M/L/W/C/I/B/E/G/T/P/H/Z — distributed across left
  half for stylus users. Shift+letter cycles tool variants.
- **Figma**: V/K/F/R/O/L/P/T/H/C/S. Shortcut overlay Ctrl+Shift+?,
  hover shows shortcut on every tool.
- **Illustrator**: V/A/P/T/L/M/B/N/R/S/E/W/Z/H — almost all left half.
- **Blender**: G/R/S (grab/rotate/scale), E (extrude), X (delete),
  A (select all), Tab (mode toggle), context-sensitive pie menus.
  Most committed to left-hand-only operation.
- **Sketch**: V/P/R/O/L/A/T. Shortcut hints in inspector + tooltips.
- **Excalidraw**: R/D/O/A/L/P/T/I/E/H/V. Numeric 0-9 in parallel.

### Recommended cluster for robot-workcell tool

Audience is Isaac Sim users → likely Blender muscle memory →
**Blender semantics for transform** (G/R/S):

| Key | Verb | Family |
|---|---|---|
| Q | Hand / pan | Navigation |
| W | Pointer / select | Selection |
| E | Rotate | Transform |
| R | Scale | Transform |
| T | Text / annotation | Annotation |
| A | Align | Transform |
| S | Snap toggle | Modifier |
| D | Distribute | Transform |
| F | Fit to view | Navigation |
| G | Group | Structure |
| Z | Undo (Ctrl+Z) | History |
| X | Delete | Edit |
| C | Copy (Ctrl+D) | Edit |
| V | Paste | Edit |
| Space | Hold=pan; tap=command palette | Universal |
| Tab | Cycle selected / toggle sidebar | Navigation |
| 1-9 | Quick-add favorite class | Add-object |
| Cmd/Ctrl+K | Command palette | Universal |
| ? | Cheatsheet overlay | Help |
| L | Lock / unlock | Structure |
| H | Hide / show | Structure |

Provide **Figma-keymap preset** for users who want it. Make keymap
**fully remappable** in preferences.

### Modifier discipline
- Ctrl/Cmd = system (save, undo, copy, find)
- Alt = modify gesture (Alt-drag duplicate, Alt-click lasso)
- Shift = extend selection / axis constraint
- Single-letter unmodified = precious; only most-used tools

---

## 2. Radial / chord menu patterns

### When radial menus work
- 4-8 options at a level (cardinal + diagonal directions)
- Used frequently enough to build muscle memory for angle
- Activation via press-and-hold-key (release direction selects)

### Patterns
- **Maya marking menus** — multi-level gesture stacking (flick NE
  then SE), more expressive but harder to teach
- **Blender pie menus** — flatter, easier
- **Houdini Tab context menu** — scoped command palette via Tab,
  type-to-filter, easier than pie, almost as fast

### Recommended pattern: three tiers
1. **Cmd+K command palette** — universal action surface
2. **Tab opens contextual "add object" palette** (Houdini-style)
   filtered to what's compatible with current selection
3. **One pie menu for view navigation** — hold Z → top/iso/fit-to-
   selection/fit-all/front/side/1:1 zoom. Expert reward feature.

Don't make radial menus the primary affordance — new users bounce.

---

## 3. Shortcut discoverability

### Patterns
- **Figma hover hints** — tooltip on tool icons after 500ms shows
  single-letter shortcut. Most copied pattern in modern design tools.
- **VSCode/Linear/Notion command palette** — every row shows shortcut
  in right column. Palette doubles as cheat sheet.
- **Blender tooltips** — show shortcut + what operator does + Python ID.
- **Coachmarks / toast tips** — NN/Group says limit to 3-4 in sequence;
  toast-style "Tip: press R" is less annoying alternative.
- **Cheat sheet overlay** — `?` opens full-screen translucent overlay
  with all shortcuts grouped. Used by Figma, Slack, Notion, Linear,
  GitHub.

### Recommended combo
1. Tooltip-on-hover shortcut hints on every button. Mandatory.
2. `?` opens cheatsheet overlay grouped by category.
3. Command palette rows show shortcuts in right-aligned monospace.
4. Just-in-time toast after Nth same-action click. Dismissible,
   never repeats per action.
5. **No mandatory onboarding tour.** Coachmarks opt-in via "Show me
   around" button.

---

## 4. Toolbar button-count guidance

### Research
- **Miller's law** — working memory 7±2 items
- **Hick's law** — decision time grows with log of choices
- **Fitts's law** — smaller/closer-packed buttons slower to click

### Pattern: primary + secondary surface
- **Primary toolbar: 5-9 buttons** — most-used verbs only
- **Secondary surface** — command palette, right-click context,
  collapsible "more tools" tray

Figma: 8 primary. Sketch: 7. Excalidraw: ~12 grouped. tldraw: 11
default. Blender: 10-15 mode-dependent.

### Recommended: 7 buttons for workcell tool
1. Select / pointer (W)
2. Hand / pan (Q)
3. Add object (palette, key 1 or Tab)
4. Annotation / text (T)
5. Measure / dimension
6. Group / lock
7. View pie / fit (F)

Everything else (rotate/scale/align/distribute/snap) lives in a
**floating contextual toolbar near selected object** (Figma/Notion
pattern).

---

## 5. Palette organization for 60+ classes

### Patterns from comparable tools
- **Figma Community plugin browser** — categorized grid + persistent
  search + recently-used row + infinite scroll
- **Fusion 360 Place Component** — search/filter, library scope
  switch, thumbnails
- **SketchUp ComponentFinder** — filters across all open tabs as you
  type
- **Autodesk Factory Design Utilities** — categorized side panel,
  drag from panel
- **DELMIA 3DEXPERIENCE Robotics** — parametric catalog, category
  trees + search, 1700+ robot models
- **Notion slash command (`/`)** — "I know what I want, let me type it"
- **Linear Cmd+K** — both browse (no input) and search (with input)
  over hundreds of items

### Recommended: three discovery modes in one panel

1. **Always-on search bar at top** (Cmd+/ to focus). Most-used mode
   for power users.
2. **Categorized accordion below** with ~5-7 collapsible groups:
   - Robots (arms, AMRs, AGVs)
   - Conveyors & transfer (belts, rollers, gravity, recirculation)
   - Containers (bins, totes, trays, kits)
   - Sensors (cameras, lidar, proximity, force-torque, barcode)
   - Fixtures (walls, floors, pallets, tables)
   - Lighting / environment
   - Operator / human / safety
3. **Top strip with "Recent" + "Favorites"** (8-12 thumbnails)
   pinned across sessions per user. 80/20 rule.

Each item:
- Visual thumbnail (top-down silhouette matches canvas)
- Name + 1-line description
- Drag handle for drag-to-place + click for "place mode"
  (ghost preview cursor, click drops, Esc cancels). Support both.

**Cmd+K should also surface object classes** — typing "ur10" → "Add
UR10 robot" + "Find existing UR10 on canvas." Skip palette entirely.

---

## 6. Annotation / reference label patterns

### CAD conventions (US National CAD Standard, AutoCAD, Revit)
Distinct shapes per type:
- Circle with letter/number — section / detail marker
- Diamond — keynote / spec reference
- Hexagon — finish or material code
- Triangle with leader line — revision marker

Each marker references row in **legend table elsewhere on sheet**.
Marker carries minimal info (ID); legend carries description.
Separation reduces canvas clutter.

Revit calls them "callouts" with `_Nr`, `_Sht`, `_Ref` parameters
that auto-update.

### Diagram-tool patterns
- **Excalidraw** — text labels + handwriting style
- **Miro/FigJam** — sticky notes; auto-numbering is open feature
  request (suggests real user need not well-served)
- **draw.io** — inline labels + floating notes with leader lines
- **First In Architecture** technical drawing guide:
  - Labels aligned + slightly away from drawing
  - Leader arrows at vertical/horizontal/45° only
  - Consistency > choice (inline OR numbered-with-legend)

### Recommended: two annotation primitives

1. **Numbered callout marker** — small circle (or hexagon for
   "spec ref", diamond for "issue") with auto-incrementing number.
   Drag onto canvas, type one-line description in sidebar legend.
   Marker small, description in legend panel. Numbers auto-renumber
   on delete (or stay stable — your call; AutoCAD lets you choose).
2. **Free-text sticky note** with optional leader line for inline
   comments.

Legend panel = collapsible sidebar listing all callouts with number,
type, description, click-to-pan. Export both inline markers + legend
table in LayoutSpec.

Constrain leader lines to horizontal/vertical/45°.

---

## 7. Prize-winning examples and lessons

### Apple Design Awards 2025 — Feather (Sketchsoft)
Stylus-pressure 3D drawing on iPad, canvas is 3D but input feels
2D-natural. **Lesson:** make spatial input feel like sketching, not
CAD constraint solving. Sane defaults that adjust after, not modal
"first pick plane, then click coordinates."

### Figma
Web-based, real-time collab, Cmd+K, hover-tooltip shortcuts, plugin
marketplace. **Lesson:** universal command palette + tooltip
discoverability is gold standard. Borrow both directly.

### Blender
Open-source, fully keyboard-driven, pie menus, customizable keymap.
**Lesson:** assume right hand on mouse forever, design every shortcut
around that. Provide "left-hand-only" keymap preset.

### tldraw
Open-source canvas SDK, clean tool/action/shortcut architecture,
React-based. **Lesson:** tldraw's
[Custom Keyboard Shortcuts](https://tldraw.dev/examples/keyboard-shortcuts)
shows clean separation between **actions** (verbs), **tools** (modal
state machines), **shortcuts** (key bindings). Copy this directly.

### Excalidraw
Hand-drawn aesthetic, playful. 122k GitHub stars vs tldraw's ~47k.
**Lesson:** aesthetic identity matters. Robot-workcell editor doesn't
need to look technical-and-cold; slight sketch-aesthetic for
annotations feels approachable.

### Linear
Cmd+K is defining feature — every action in 2-3 keystrokes.
**Lesson:** make command palette opinionated and curated, not dump
of every action. Ranked by recency and context.

### Raycast
Native macOS launcher, every action Cmd+K, extensions ship their
own commands. **Lesson:** treat tool as "platform for actions" —
plugins/extensions register commands into palette.

### Notion
Slash command (`/`) for inline insertion. **Lesson:** in a canvas
where dropping objects is dominant gesture, slash-command-on-canvas
lets users press `/`, type `ur10`, robot drops at cursor. Fast,
discoverable, no toolbar interaction.

### Autodesk Factory Design Utilities
Industry-standard factory layouts. Drag-from-library, 2D/3D round-
trip, parametric assets. **Lesson:** expect 2D top-down ↔ 3D preview
seamlessly. Plan from day one.

### DELMIA Robotics
1700+ robot models, parametric catalog. **Lesson:** asset thumbnails
should be top-down silhouettes matching canvas appearance, not 3D
renders. Visual consistency reduces drop-and-rotate-to-orient errors.

---

## 8. Concrete recommendations per user's 7 questions

### 8.1 Left-hand shortcut recommendation
Blender semantics for transform (G/R/S), Q/W for hand/select,
A/S/D for align/snap/distribute, Z/X/C/V for undo/delete/copy/paste,
F for fit, Tab for contextual-add, T for text, 1-9 for favorite-
object quick-drop. Provide **Figma-keymap preset**. Fully
remappable.

### 8.2 Cross/menu activation
**Cmd+K palette** primary. **Tab contextual-add palette**
(Houdini). **One pie menu for view** (hold Z). Document
spacebar=hold-to-pan from Figma.

### 8.3 Shortcut pedagogy
Three stacked surfaces:
- Hover tooltip with shortcut on every button (Figma)
- `?` opens cheatsheet overlay grouped by category
- Just-in-time toast after Nth UI-click — dismissible per action,
  never repeats
No coachmark tour. Onboarding opt-in via "Show me around" button.

### 8.4 Button count
**Primary toolbar: 7 buttons.** Select, Hand, Add-object,
Annotate, Measure, Group, View/Fit. Everything else in floating
contextual toolbar + Cmd+K + right-click context.

### 8.5 60-palette UX
Three discovery modes in one panel:
- Cmd+/ search bar at top
- Categorized accordion (5-7 groups)
- "Recent + Favorites" strip (8-12 pinned thumbnails)
Also expose via Cmd+K palette + Notion-style slash command on
canvas (`/ur10`). Top-down silhouette thumbnails.

### 8.6 Annotation
CAD-style numbered callout markers + collapsible legend sidebar
(ID, type, description, click-to-pan). Free-text sticky notes
with optional leader lines. Leader lines constrained to
horizontal/vertical/45°. Export both in LayoutSpec.

### 8.7 Top 3 references to study
1. **Figma** — hover hints + command palette
2. **Blender** — left-hand modal workflow + remappable keymap
3. **tldraw** — modern canvas-SDK architecture

Honorable mentions: Linear (Cmd+K curation), Notion (slash),
Excalidraw (sketch aesthetic), AutoCAD/Revit (callouts),
Autodesk Factory Design Utilities (drag-from-library + 2D/3D
round-trip — same problem domain).

---

## 9. Open questions / unknowns

- **Empirical user testing needed.** Patterns are best-practice;
  right specific keymap for Isaac Sim user needs A/B testing.
- **Pie-menu adoption rates in 2026.** Recent NN/G coverage still
  cites 2010 study showing pie menus slower to learn. No fresh
  2024-2026 contradicting study found.
- **Slash-command-on-canvas vs Tab-on-canvas.** Both work. Notion
  uses `/`, Houdini uses Tab. Depends on what your canvas already
  uses for shape-constraint or focus-switching. Test with users.
- **Apple Design Awards 2025** had one canvas-adjacent winner
  (Feather 3D). Awwwards and IxDA 2025 weren't searched deeply.
- **Auto-numbering UX for callouts.** AutoCAD/Revit differ on
  what happens when you delete callout #3 — does #4 become #3, or
  stay #4? Industry not unified. Pick one and document.
- **60 vs 17 classes.** If starts at 17, flat searchable list fine.
  If 60+, mandatory categorization. Above assumes 60-class endgame.
- **Mobile / tablet input.** Recommendations assume desktop
  mouse+keyboard. Touch (Apple Pencil iPad, Surface Pen) shifts
  toward Feather/Procreate gesture patterns — out of scope.

---

## Sources
- [Blender 5.1 Manual — Menus](https://docs.blender.org/manual/en/latest/interface/controls/buttons/menus.html)
- [Blender Default Keymap](https://docs.blender.org/manual/en/latest/interface/keymap/blender_default.html)
- [Figma Keyboard Shortcuts](https://help.figma.com/hc/en-us/articles/360040328653-Keyboard-shortcuts-in-Figma)
- [Figma — Fitts's Law](https://www.figma.com/resource-library/fitts-law/)
- [Adobe Photoshop default shortcuts](https://helpx.adobe.com/photoshop/using/default-keyboard-shortcuts.html)
- [Adobe Illustrator default shortcuts](https://helpx.adobe.com/illustrator/using/default-keyboard-shortcuts.html)
- [NN/G — Expandable Menus: Pull-Down, Square, or Pie?](https://www.nngroup.com/articles/expandable-menus/)
- [NN/G — Fitts's Law](https://www.nngroup.com/articles/fitts-law/)
- [NN/G — Instructional Overlays and Coach Marks](https://www.nngroup.com/articles/mobile-instructional-overlay/)
- [tldraw — User Interface](https://tldraw.dev/docs/user-interface)
- [tldraw — Custom Keyboard Shortcuts](https://tldraw.dev/examples/keyboard-shortcuts)
- [Excalidraw — How to start drawing](https://plus.excalidraw.com/how-to-start)
- [First in Architecture — Technical Drawing](https://www.firstinarchitecture.co.uk/technical-drawing-labelling-and-annotation/)
- [US National CAD Standard](https://www.nationalcadstandard.org/ncs6/faqs.php)
- [Augi — AutoCAD Architecture annotations](https://www.augi.com/articles/detail/annotations-in-autocad-architecture1)
- [Apple Design Awards 2025](https://www.apple.com/newsroom/2025/06/apple-unveils-winners-and-finalists-of-the-2025-apple-design-awards/)
- [Autodesk Factory Design Utilities](https://www.autodesk.com/products/factory-design-utilities/overview)
- [DELMIA 3DEXPERIENCE Robotics](https://www.goengineer.com/3dexperience/manufacturing/robotics)
- [Mobbin — Command Palette UI Design](https://mobbin.com/glossary/command-palette)
- [Laws of UX — Fitts's Law](https://lawsofux.com/fittss-law/)
