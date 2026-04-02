# 01 — Extension Shell + Dockable UI Pane

## Purpose

Provide the Isaac Sim extension skeleton that loads Isaac Assist into the Omniverse Kit runtime. This module owns the extension lifecycle, the dockable UI pane, the menu entry, and the communication bridge to the background service.

## Runtime

Extension (in-process, Isaac Sim)

## Phase

1 (Weeks 3–5)

## Dependencies

- Omniverse Kit SDK (`omni.ext`, `omni.ui`, `omni.kit.commands`)
- Background service process (started/managed by this module)

---

## Functional Requirements

### FR-01.1 Extension Lifecycle

- Implement `omni.ext.IExt` with `on_startup()` and `on_shutdown()` hooks.
- On startup: register menu item under `Window > Isaac Assist`, initialize the dockable UI pane, start or connect to the background service, trigger initial environment fingerprint collection.
- On shutdown: gracefully disconnect from the background service, persist any unsaved state, unregister all UI elements and callbacks.
- Support hot-reload without crashing the stage or losing conversation state.

### FR-01.2 Dockable UI Pane

- Create a dockable window using `omni.ui.Window` with title "Isaac Assist".
- Default dock position: right panel, below the Property panel.
- Pane layout (top to bottom):
  1. **Status bar:** Environment fingerprint summary (version badges, compatibility status indicator — green/yellow/red).
  2. **Chat area:** Scrollable conversation view with user messages, assistant responses, source citations, and inline action cards.
  3. **Input area:** Text input field with send button and attachment controls (for log files, screenshots).
  4. **Action bar:** Quick-action buttons — "Analyze Scene", "Inspect Selection", "Rollback", "Export Repro Bundle".
- Support light and dark themes (inherit from Kit theme).

### FR-01.3 Selection Observer

- Listen for `omni.usd.StageEventType.SELECTION_CHANGED` events.
- When selection changes, update a context sidebar or inline card showing:
  - Selected prim path(s)
  - Prim type and applied schemas
  - Quick links: "Diagnose this prim", "Find docs for this type"
- Debounce rapid selection changes (250ms).

### FR-01.4 Stage Event Listener

- Subscribe to stage open, close, and layer change events.
- On new stage: reset conversation context, re-trigger fingerprint if needed.
- On layer changes (sublayer add/remove): notify the stage analyzer module.

### FR-01.5 Commands Tool Integration

- Register Isaac Assist actions as Omniverse commands via `omni.kit.commands`.
- Minimum commands to register:
  - `IsaacAssist.AnalyzeScene`
  - `IsaacAssist.InspectSelection`
  - `IsaacAssist.CreateSnapshot`
  - `IsaacAssist.Rollback`
  - `IsaacAssist.ExportReproBundle`
- All commands must be undoable where applicable.

### FR-01.6 Background Service Bridge

- Manage a local background service process (start, health-check, restart, stop).
- Communicate via HTTP on localhost (configurable port, default 18515).
- Implement a client class with async request methods for each service API endpoint.
- Handle service unavailability gracefully: queue requests, show "reconnecting" status, retry with backoff.
- Timeout: 30s default for standard requests, 120s for long-running operations (retrieval, patch planning).

---

## Data Models

### ExtensionConfig

```python
@dataclass
class ExtensionConfig:
    service_host: str = "127.0.0.1"
    service_port: int = 18515
    service_auto_start: bool = True
    theme: str = "auto"  # "auto", "light", "dark"
    selection_debounce_ms: int = 250
    max_conversation_history: int = 200
    snapshot_dir: str = ""  # empty = default location
    log_level: str = "INFO"
```

### SelectionContext

```python
@dataclass
class SelectionContext:
    prim_paths: List[str]
    prim_types: List[str]
    applied_schemas: List[List[str]]
    timestamp: float
    stage_id: str
```

### ServiceStatus

```python
class ServiceStatus(Enum):
    CONNECTED = "connected"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"
```

---

## API Contract

This module is an API **consumer** for all background-service endpoints. It exposes no service API of its own.

### Internal Extension API (for other extension modules)

```python
class IsaacAssistExtension:
    def get_service_client(self) -> ServiceClient: ...
    def get_current_selection(self) -> SelectionContext: ...
    def get_stage_context(self) -> StageContext: ...
    def show_notification(self, message: str, severity: str) -> None: ...
    def append_chat_message(self, role: str, content: str, metadata: dict) -> None: ...
    def request_approval(self, action: PatchAction) -> asyncio.Future[bool]: ...
```

---

## File Structure

```
exts/
└── omni.isaac.assist/
    ├── config/
    │   └── extension.toml          # Kit extension manifest
    ├── omni/
    │   └── isaac/
    │       └── assist/
    │           ├── __init__.py
    │           ├── extension.py     # IExt implementation, lifecycle
    │           ├── ui/
    │           │   ├── __init__.py
    │           │   ├── main_window.py    # Dockable pane layout
    │           │   ├── chat_view.py      # Conversation renderer
    │           │   ├── status_bar.py     # Fingerprint + status badges
    │           │   ├── action_bar.py     # Quick-action buttons
    │           │   ├── input_area.py     # Text input + attachments
    │           │   ├── selection_card.py  # Selection context card
    │           │   └── approval_dialog.py # Patch approval modal
    │           ├── observers/
    │           │   ├── __init__.py
    │           │   ├── selection_observer.py
    │           │   └── stage_observer.py
    │           ├── commands/
    │           │   ├── __init__.py
    │           │   └── assist_commands.py
    │           ├── service/
    │           │   ├── __init__.py
    │           │   ├── client.py         # HTTP client to background service
    │           │   └── process_manager.py # Start/stop/health-check service
    │           └── config.py             # ExtensionConfig loader
    └── docs/
        └── README.md
```

---

## extension.toml

```toml
[package]
title = "Isaac Assist"
version = "0.1.0"
description = "Retrieval-first, scene-aware repair assistant for Isaac Sim"
category = "Simulation"
keywords = ["assistant", "debugging", "repair", "diagnostics"]

[dependencies]
"omni.ui" = {}
"omni.usd" = {}
"omni.kit.commands" = {}
"omni.kit.window.extensions" = {}

[[python.module]]
name = "omni.isaac.assist"

[settings]
exts."omni.isaac.assist".service_port = 18515
exts."omni.isaac.assist".service_auto_start = true
```

---

## Implementation Notes

- Use `omni.ui.Workspace.set_show_window_fn()` for deferred window creation.
- Use `omni.kit.app.get_app().get_update_event_stream()` for periodic service health checks (every 5s).
- Store conversation history in memory within the extension; persist to disk via the background service only on explicit save or shutdown.
- All UI updates must happen on the main thread; use `omni.kit.async_engine` for async bridging.
- For theme support, read `carb.settings.get_settings().get("/persistent/app/window/uiStyle")`.

---

## Acceptance Criteria

- [ ] Extension loads in Isaac Sim without errors.
- [ ] Dockable pane appears under `Window > Isaac Assist`.
- [ ] Selection changes update the context card within 300ms.
- [ ] Stage open/close events reset the conversation context.
- [ ] Background service starts automatically and reconnects on failure.
- [ ] All five Omniverse commands are registered and executable.
- [ ] Hot-reload does not crash the stage.
