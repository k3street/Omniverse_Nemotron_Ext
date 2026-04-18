# Community & Remote Access Addendum

**Enhances:** Onboarding + general UX  
**Source:** Personas P08 (Alex), P04 (Sarah), P06 (Alessandro)

---

## C.1 — Hardware-Tagged Scene Templates

Every template and example tagged with minimum hardware requirements:

```json
{
  "template": "pick_and_place_franka",
  "min_vram_gb": 8,
  "recommended_vram_gb": 12,
  "estimated_fps": {"8gb": 30, "12gb": 60, "24gb": 120},
  "tags": ["works_on_12gb", "beginner_friendly"]
}
```

**Filter in UI:** "Show only templates that work on my GPU" — auto-detected from `HydraEngineStats.get_device_info()`.

---

## C.2 — Scene Template Sharing

**Export:** `export_template(scene_path, name, description)` → packages USD + config + metadata as `.isaa` file (zip with manifest)

**Import:** `import_template(file_path)` → loads template into local library

**No central server needed** — file-based sharing (email, GitHub, Discord). Central registry is a future platform feature.

---

## C.3 — GPU VRAM Headroom Warning

**Proactive check before expensive operations:**

```
User: "Clone this robot 1024 times for RL training"

⚠ This will need approximately 20 GB additional VRAM.
Your GPU: RTX 4070 (12 GB), currently using 7 GB.
Available: 5 GB — not enough for 1024 environments.

Suggestions:
→ Reduce to 128 environments (fits in ~4 GB)
→ Use headless mode to free ~2 GB
→ Use cloud compute (Phase 7H)
```

**Implementation:** Estimate VRAM from: `num_envs × per_env_estimate` (from articulation complexity + sensor count). Compare against `get_device_info()` available VRAM.

---

## C.4 — Async Task Dispatch

**For long-running operations (training, SDG, benchmarks):**

```
User: "Run 500 SDG frames with full domain randomization"

Starting SDG pipeline in background...
You can keep working. I'll notify you when done.

[15 minutes later]
✓ SDG complete: 500 frames, 3 annotation issues found.
Results at: workspace/sdg_output/run_042/
Want me to show a summary?
```

**Implementation:** Run long tasks in background thread/process. SSE notification to chat panel on completion. User can query status: "how's the SDG run going?"

---

## C.5 — Force Visualization in Viewport

**Tool:** `visualize_forces(articulation_path, scale)`

Show per-joint torques as colored arrows in viewport:
- Green = within normal range
- Yellow = >70% of limit
- Red = >90% of limit (near saturation)

Uses `debug_draw.draw_lines()` for arrows (draw line + two short lines for arrowhead).

**Value:** Visually compelling (Alex's YouTube content) + practically useful (Erik's debugging). Combines entertainment and engineering.

---

## C.6 — Rendered Video Output

**Tool:** `render_video(duration, camera, quality, output_path)`

Not screen capture — path-traced rendered video via Isaac Sim's RTX renderer:

```python
# Use omni.kit.capture or Movie Capture extension
from omni.kit.capture import CaptureOptions
options = CaptureOptions()
options.fps = 30
options.resolution = (1920, 1080)
options.renderer = "PathTracing"  # or "RayTracing"
options.output_path = output_path
```

**Quality presets:**
| Preset | Renderer | Resolution | SPP | Use |
|--------|---------|-----------|-----|-----|
| `preview` | RayTracing | 1280×720 | 1 | Quick check |
| `presentation` | PathTracing | 1920×1080 | 64 | Investor demo |
| `production` | PathTracing | 3840×2160 | 256 | Marketing |

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Hardware tag filtering | L0 | Mock device_info → correct filter |
| VRAM estimation | L0 | Known env count + complexity → correct estimate |
| Template export/import | L0 | Round-trip manifest validation |
| Async task lifecycle | L1 | Start → poll → complete notification |
| Force visualization | L3 | Requires Kit + articulation |
| Rendered video | L3 | Requires Kit + RTX renderer |
