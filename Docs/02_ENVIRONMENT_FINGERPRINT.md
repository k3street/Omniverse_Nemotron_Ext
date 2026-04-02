# 02 — Environment Discovery + Compatibility Matrix

## Purpose

Detect the full runtime environment on startup and on demand, produce a structured fingerprint, and resolve it against a tested compatibility matrix. Flag unsupported, degraded, or untested combinations before any diagnostic or repair action.

## Runtime

Background service (primary), Extension (trigger + display)

## Phase

1 (Weeks 3–5)

## Dependencies

- Background service framework (from 01)
- Access to local filesystem, Python environment, GPU drivers, and system info

---

## Functional Requirements

### FR-02.1 Fingerprint Collection

Collect the following on startup and on explicit refresh:

| Category | Fields |
|----------|--------|
| **Isaac Sim** | Version, build number, install path, install mode (workstation/pip/source/container), Kit SDK version |
| **Isaac Lab** | Version, install path, install mode, branch/commit if source build |
| **Python** | Version, executable path, virtual env name and path, `sys.prefix` |
| **OS** | Distribution, version, kernel version, architecture |
| **GPU** | Device name(s), VRAM, NVIDIA driver version, CUDA version, cuDNN version |
| **ROS** | ROS distro, ROS 2 version (if present), Isaac ROS packages installed |
| **Extensions** | List of enabled extensions with versions, extension search paths, registry URLs |
| **Packages** | Key pip packages with versions (`isaacsim`, `isaaclab`, `torch`, `numpy`, `pxr`, `omni-*`) |
| **Container** | Container runtime (if any), image tag, base image |
| **Workspace** | Working directory, USD stage path (if loaded), project config files found |

### FR-02.2 Install Provenance

Beyond version strings, capture *how* each component was installed:
- Source build: Git remote URL, branch, commit SHA, build date
- Pip install: Package index URL, installed-from wheel/sdist
- Container: Image tag, registry, build timestamp
- Extension registry: Registry URL, extension ID, install date

### FR-02.3 Compatibility Matrix

Maintain a local compatibility matrix (JSON/YAML file shipped with the extension, updatable):

```yaml
compatibility:
  - name: "Isaac Sim 4.5 + Isaac Lab 2.1"
    isaac_sim: ">=4.5.0,<5.0.0"
    isaac_lab: ">=2.1.0,<3.0.0"
    python: ">=3.10,<3.12"
    cuda: ">=12.1"
    driver: ">=535"
    status: "ga"  # ga | preview | deprecated | unsupported
    notes: "Stable production combination"

  - name: "Isaac Sim 6.0 + Isaac Lab 3.0"
    isaac_sim: ">=6.0.0,<7.0.0"
    isaac_lab: ">=3.0.0,<4.0.0"
    python: ">=3.10,<3.13"
    cuda: ">=12.4"
    driver: ">=550"
    status: "preview"
    notes: "Early Developer Release — not all features validated"
```

### FR-02.4 Compatibility Resolution

Given a fingerprint, resolve against the matrix and produce:

- **Match status:** `supported`, `preview`, `deprecated`, `unsupported`, `unknown`
- **Blocking issues:** Hard incompatibilities that will cause failures (list with evidence)
- **Warnings:** Soft issues that may cause problems (list with evidence)
- **Informational:** Notable but non-blocking observations

### FR-02.5 Product Mode Gating

Support two operational modes based on compatibility resolution:

- **GA Mode (default):** Only propose fixes validated against GA combinations. Block auto-apply for preview/unsupported stacks. Show explicit warnings on every action.
- **Experimental Mode:** Allow all fixes with clear labeling. Require per-action acknowledgment for unvalidated operations.

---

## Data Models

### EnvironmentFingerprint

```python
@dataclass
class EnvironmentFingerprint:
    fingerprint_id: str              # UUID
    collected_at: datetime
    
    # Isaac ecosystem
    isaac_sim_version: str
    isaac_sim_build: str
    isaac_sim_install_path: str
    isaac_sim_install_mode: str      # "workstation" | "pip" | "source" | "container"
    kit_sdk_version: str
    isaac_lab_version: Optional[str]
    isaac_lab_install_path: Optional[str]
    isaac_lab_install_mode: Optional[str]
    isaac_lab_commit: Optional[str]
    
    # Python
    python_version: str
    python_executable: str
    venv_name: Optional[str]
    venv_path: Optional[str]
    key_packages: Dict[str, str]     # package_name -> version
    
    # System
    os_distribution: str
    os_version: str
    kernel_version: str
    architecture: str
    
    # GPU
    gpu_devices: List[GPUDevice]
    nvidia_driver_version: str
    cuda_version: str
    cudnn_version: Optional[str]
    
    # ROS
    ros_distro: Optional[str]
    ros2_version: Optional[str]
    isaac_ros_packages: List[str]
    
    # Extensions
    enabled_extensions: List[ExtensionInfo]
    extension_search_paths: List[str]
    extension_registries: List[str]
    
    # Container
    container_runtime: Optional[str]
    container_image_tag: Optional[str]
    
    # Workspace
    working_directory: str
    stage_path: Optional[str]

@dataclass
class GPUDevice:
    name: str
    vram_mb: int
    device_index: int

@dataclass
class ExtensionInfo:
    extension_id: str
    version: str
    enabled: bool
    path: str
```

### CompatibilityResult

```python
@dataclass
class CompatibilityResult:
    match_name: Optional[str]
    status: str                      # "supported" | "preview" | "deprecated" | "unsupported" | "unknown"
    mode: str                        # "ga" | "experimental"
    blocking: List[CompatibilityIssue]
    warnings: List[CompatibilityIssue]
    informational: List[CompatibilityIssue]

@dataclass
class CompatibilityIssue:
    component: str
    field: str
    expected: str
    actual: str
    severity: str                    # "blocking" | "warning" | "info"
    message: str
```

---

## API Contract

### Service Endpoints

```
POST /api/v1/fingerprint/collect
  Request: { "force_refresh": bool }
  Response: EnvironmentFingerprint

GET /api/v1/fingerprint/current
  Response: EnvironmentFingerprint | null

POST /api/v1/fingerprint/resolve
  Request: EnvironmentFingerprint
  Response: CompatibilityResult

GET /api/v1/compatibility/matrix
  Response: { "entries": [...], "last_updated": datetime }

PUT /api/v1/compatibility/matrix
  Request: { "entries": [...] }
  Response: { "updated": bool }
```

---

## File Structure

```
service/
└── isaac_assist_service/
    └── fingerprint/
        ├── __init__.py
        ├── collector.py           # Fingerprint collection logic
        ├── gpu_detector.py        # nvidia-smi / pynvml GPU info
        ├── package_scanner.py     # pip / importlib package detection
        ├── extension_scanner.py   # Omniverse extension enumeration
        ├── ros_detector.py        # ROS environment detection
        ├── container_detector.py  # Container runtime detection
        ├── provenance.py          # Install-provenance resolution
        ├── compatibility.py       # Matrix resolution engine
        ├── matrix.yaml            # Shipped compatibility matrix
        └── routes.py              # FastAPI route handlers
```

---

## Implementation Notes

- GPU detection: Use `pynvml` first (reliable), fall back to parsing `nvidia-smi --query-gpu` output.
- Python packages: Use `importlib.metadata.distributions()` for installed packages; do not shell out to `pip list`.
- Extensions: If running in-process, use `omni.kit.app.get_app().get_extension_manager()`; from the background service, parse extension `.toml` files from known search paths.
- Compatibility matrix: Ship a default YAML; support user overrides in a local config directory. Allow updates from a remote URL (opt-in).
- Cache the fingerprint for the session duration; invalidate on stage change or explicit refresh.
- Fingerprint collection must complete within 5 seconds on a standard workstation.

---

## Acceptance Criteria

- [ ] Fingerprint collects all fields listed in FR-02.1 on startup.
- [ ] Install provenance is captured for source builds and pip installs.
- [ ] Compatibility resolution correctly identifies at least three known combinations (supported, preview, unsupported).
- [ ] Blocking issues prevent auto-apply in GA mode.
- [ ] Fingerprint refresh completes in under 5 seconds.
- [ ] Status bar in the extension displays correct compatibility badges.
