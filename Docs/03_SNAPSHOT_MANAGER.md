# 03 — Snapshot Manager (State, Diff, Rollback)

## Purpose

Create immutable snapshots before any mutating action, enable diffing between states, and support one-click rollback to any prior known-good state. This is the safety foundation — no write operation should execute without a snapshot.

## Runtime

Both (Extension triggers snapshots; Background service persists and manages them)

## Phase

1 (Weeks 3–5)

## Dependencies

- Extension shell (01) for UI triggers
- Environment fingerprint (02) for fingerprint inclusion in snapshots

---

## Functional Requirements

### FR-03.1 Snapshot Creation

Before any mutating action (USD edit, Python file edit, settings change, package recommendation), create a snapshot containing:

| Component | Contents |
|-----------|----------|
| **USD layers** | Flattened or per-layer `.usda`/`.usdc` export of all modified sublayers |
| **Changed files** | Copy of any Python files, config files, or extension settings about to be modified |
| **Extension settings** | Current values of all Isaac Assist and relevant extension settings |
| **Environment fingerprint** | Current fingerprint at time of snapshot |
| **Validation baseline** | Results of the last validation run (if any) |
| **User note** | Optional description from the user or auto-generated from the action context |
| **Action context** | The patch plan or action that triggered this snapshot |

### FR-03.2 Snapshot Storage

- Store snapshots in a configurable local directory (default: `~/.isaac_assist/snapshots/`).
- Each snapshot is a directory named `{timestamp}_{short_id}/` containing:
  - `manifest.json` — metadata, fingerprint summary, action context
  - `layers/` — USD layer files
  - `files/` — copies of modified files with relative paths preserved
  - `settings.json` — extension settings dump
  - `validation.json` — baseline validation results
- Limit total snapshot storage with configurable max count (default: 50) and max age (default: 30 days). Auto-prune oldest beyond limits.

### FR-03.3 Diff Generation

Generate human-readable diffs between:
- Current state vs. a specific snapshot
- Two arbitrary snapshots
- Current state vs. last known-good state

Diff types:
- **USD diff:** Prim-level changes (added, removed, modified prims with property-level detail)
- **File diff:** Standard unified diff for text files
- **Settings diff:** Key-value change list

### FR-03.4 Rollback

Support three rollback modes:
1. **Full rollback:** Restore all components (USD layers, files, settings) to snapshot state.
2. **Selective rollback:** Restore only specific components (e.g., only USD, only files).
3. **Scene-only rollback:** Restore only USD layer changes, leave files and settings untouched.

Rollback must:
- Create a new snapshot of the *current* state before rolling back (so rollback itself is reversible).
- Validate the restored state after rollback.
- Report what was restored and what validation results look like post-rollback.

### FR-03.5 Known-Good State

- Maintain a pointer to the "last known-good state" — the most recent snapshot where validation passed.
- Expose a "Restore Last Known-Good" quick action in the UI.
- Update the known-good pointer after successful validation following an applied fix.

### FR-03.6 Snapshot Metadata and Querying

- List all snapshots with filters: date range, action type, validation status.
- Search snapshot notes and action context.
- Tag snapshots (manual and automatic: "pre-fix", "post-fix", "known-good", "manual").

---

## Data Models

### Snapshot

```python
@dataclass
class Snapshot:
    snapshot_id: str                  # Short unique ID (e.g., 8-char hex)
    created_at: datetime
    trigger: str                     # "pre_fix" | "manual" | "pre_rollback" | "auto"
    action_context: Optional[str]    # Description of the action that triggered this
    patch_plan_id: Optional[str]     # Reference to the patch plan if applicable
    user_note: Optional[str]
    tags: List[str]
    
    fingerprint_summary: Dict[str, str]  # Key version info subset
    
    layers: List[SnapshotLayer]
    files: List[SnapshotFile]
    settings: Dict[str, Any]
    validation_baseline: Optional[ValidationResult]
    
    storage_path: str
    size_bytes: int

@dataclass
class SnapshotLayer:
    layer_identifier: str            # USD layer identifier
    layer_path: str                  # Original path
    snapshot_path: str               # Path within snapshot directory
    format: str                      # "usda" | "usdc"

@dataclass
class SnapshotFile:
    original_path: str
    snapshot_path: str
    file_type: str                   # "python" | "config" | "toml" | "json" | "other"
    checksum: str                    # SHA-256

@dataclass
class SnapshotDiff:
    snapshot_a_id: str
    snapshot_b_id: str               # or "current"
    usd_changes: List[USDChange]
    file_changes: List[FileDiff]
    settings_changes: List[SettingChange]

@dataclass
class USDChange:
    prim_path: str
    change_type: str                 # "added" | "removed" | "modified"
    property_changes: List[PropertyChange]

@dataclass
class PropertyChange:
    property_name: str
    old_value: Optional[str]
    new_value: Optional[str]

@dataclass
class FileDiff:
    file_path: str
    change_type: str                 # "added" | "removed" | "modified"
    unified_diff: str                # Standard unified diff text

@dataclass
class SettingChange:
    key: str
    old_value: Any
    new_value: Any
```

---

## API Contract

### Service Endpoints

```
POST /api/v1/snapshots
  Request: {
    "trigger": str,
    "action_context": str,
    "patch_plan_id": str | null,
    "user_note": str | null,
    "layer_identifiers": [str],       # Which USD layers to capture
    "file_paths": [str],              # Which files to capture
    "include_settings": bool,
    "include_validation": bool
  }
  Response: Snapshot

GET /api/v1/snapshots
  Query params: limit, offset, tag, trigger, date_from, date_to, search
  Response: { "snapshots": [Snapshot], "total": int }

GET /api/v1/snapshots/{snapshot_id}
  Response: Snapshot

DELETE /api/v1/snapshots/{snapshot_id}
  Response: { "deleted": bool }

POST /api/v1/snapshots/{snapshot_id}/diff
  Request: { "compare_to": str }      # snapshot_id or "current"
  Response: SnapshotDiff

POST /api/v1/snapshots/{snapshot_id}/rollback
  Request: {
    "mode": str,                       # "full" | "selective" | "scene_only"
    "components": [str] | null         # For selective: ["usd", "files", "settings"]
  }
  Response: {
    "rollback_snapshot_id": str,       # Snapshot of state before rollback
    "restored_components": [str],
    "validation_result": ValidationResult | null
  }

GET /api/v1/snapshots/known-good
  Response: Snapshot | null

POST /api/v1/snapshots/{snapshot_id}/tag
  Request: { "tags": [str] }
  Response: Snapshot
```

### Extension-Side Interface

```python
class SnapshotManagerUI:
    async def create_snapshot(self, trigger: str, context: str) -> Snapshot: ...
    async def show_diff(self, snapshot_id: str) -> None: ...  # Opens diff viewer
    async def rollback(self, snapshot_id: str, mode: str) -> None: ...
    async def restore_known_good(self) -> None: ...
    async def list_snapshots(self) -> List[Snapshot]: ...
```

---

## File Structure

```
service/
└── isaac_assist_service/
    └── snapshots/
        ├── __init__.py
        ├── manager.py             # Core snapshot create/list/delete/prune logic
        ├── usd_capture.py         # USD layer export and restore
        ├── file_capture.py        # File copy and restore
        ├── settings_capture.py    # Extension settings capture and restore
        ├── differ.py              # Diff generation (USD, file, settings)
        ├── rollback.py            # Rollback execution engine
        ├── storage.py             # Disk storage, pruning, size tracking
        └── routes.py              # FastAPI route handlers
```

---

## Implementation Notes

- **USD layer capture:** Use `pxr.Usd.Stage.Export()` or `pxr.Sdf.Layer.Export()` to save layers. For large stages, consider exporting only dirty/modified sublayers rather than the entire flattened stage.
- **File capture:** Use `shutil.copy2` to preserve metadata. Store files under `files/` with their relative path from the workspace root.
- **Diff generation for USD:** Compare prim-by-prim using `pxr.Sdf.Layer` APIs. For a simpler MVP, export both states as `.usda` text and diff the text.
- **Atomic rollback:** Perform all restores in a transaction-like pattern — if any component fails to restore, abort and report without leaving a partial state.
- **Secret redaction:** Before persisting settings snapshots, redact any values matching known secret patterns (API keys, tokens).
- **Pruning:** Run pruning check after every new snapshot creation. Prune by count first, then by age.

---

## Acceptance Criteria

- [ ] Snapshot is created before every mutating action (USD edit, file edit, settings change).
- [ ] Snapshot contains all specified components (layers, files, settings, fingerprint, validation baseline).
- [ ] Diff viewer shows prim-level USD changes and unified file diffs.
- [ ] Full rollback restores USD layers, files, and settings correctly.
- [ ] Rollback creates its own pre-rollback snapshot.
- [ ] "Restore Last Known-Good" works from the UI action bar.
- [ ] Auto-pruning respects max count and max age limits.
- [ ] Secrets are redacted from persisted settings snapshots.
