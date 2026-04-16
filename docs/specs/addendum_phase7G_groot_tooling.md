# Phase 7G Addendum — GR00T N1 Tooling

**For:** The session building Phase 7G (GR00T N1 foundation-policy evaluation)
**Priority:** Add before `load_groot_policy` ships — these are L0 utilities that
prevent wasted cloud compute and wasted download bandwidth.
**Effort:** Small — four tool handlers, no new Kit RPC endpoint.

---

## Motivation

The Phase 7G tools (`load_groot_policy`, `evaluate_groot`, `finetune_groot`)
are all heavy-weight subprocess orchestrators:

- `load_groot_policy` downloads ~6 GB of checkpoint data from HuggingFace and
  spawns a GR00T server (24 GB+ VRAM).
- `evaluate_groot` runs closed-loop rollouts in IsaacLabEvalTasks.
- `finetune_groot` requires 25-48 GB VRAM and LeRobot v2-format demos.

Each of those will fail loudly on an RTX 5070 (12 GB). Before the LLM suggests
any of them, it needs lightweight tools that:

1. Tell it whether the hardware will work at all.
2. Tell it which embodiment config to pass (user asks "can GR00T drive my
   Franka?" — we need a lookup table).
3. Emit a reproducible deployment script the user can commit and re-run, rather
   than relying on the Kit process lifetime.
4. Convert the Phase 7C teleop HDF5 demos into LeRobot v2 format ahead of
   time, so fine-tune attempts don't waste GPU minutes failing on schema.

All four are pure data / code-gen — no Kit RPC, no subprocess, no network
(beyond the HuggingFace probe).

---

## Tools

### 7G-A.1 `check_groot_hardware(required_vram_gb=24.0)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Probe local GPU(s) via `torch.cuda.get_device_properties`. If torch or CUDA
   is unavailable, fall back to `nvidia-smi --query-gpu=memory.total` parsing.
   If neither is available, return `available=False`.
2. Pick the largest GPU's total memory (bytes → GB).
3. Compare against the three known GR00T gates:
   - `inference_min_gb = 24` — `load_groot_policy` needs this
   - `finetune_lora_gb = 24` — LoRA-style fine-tune per GPU (×2 for 48 GB
     total, see spec)
   - `finetune_full_gb = 48` — full fine-tune
4. Return per-gate verdicts + a recommendation (`"local_ok"`,
   `"cloud_required_for_inference"`, `"cloud_required_for_finetune"`).

**Returns:**
```python
{
    "available": bool,
    "gpus": [{"name": "...", "vram_gb": 11.7}, ...],
    "max_vram_gb": 11.7,
    "inference_ok": bool,
    "lora_finetune_ok": bool,
    "full_finetune_ok": bool,
    "recommendation": "cloud_required_for_inference",
    "cloud_hint": "Phase 7H (cloud_launch) or remote A6000/H100",
}
```

**Why DATA, not CODE_GEN:** the LLM needs to see the numbers before it decides
whether to chain into `load_groot_policy`. It must be a live lookup, not
something the user has to approve.

### 7G-A.2 `lookup_groot_embodiment(robot_name)`

**Type:** DATA handler (no code gen).

**Logic:** Fuzzy-match a robot name (e.g. "franka", "widowx", "g1", "unitree",
"panda arm") against the GR00T N1.6 pre-registered embodiment configs:

| Robot            | Config key    | Notes                               |
|------------------|---------------|-------------------------------------|
| Franka / Panda   | `LIBERO_PANDA` | LIBERO benchmark, 7-DoF arm         |
| WidowX           | `OXE_WIDOWX`   | Open X-Embodiment, 6-DoF arm        |
| Unitree G1       | `UNITREE_G1`   | Humanoid, 23-DoF                    |
| SO-100           | `OXE_SO100`    | Open X-Embodiment tabletop arm      |
| Other            | `CUSTOM`       | User must define EmbodimentTag      |

**Returns:**
```python
{
    "found": bool,
    "robot_name": "franka",
    "embodiment_config": "LIBERO_PANDA",
    "observation_type": "rgb+proprio+language",
    "action_dim": 7,
    "note": "...",
    "alternatives": [...],  # if fuzzy-match produced several candidates
}
```

### 7G-A.3 `generate_groot_deploy_script(model_id, embodiment_config, host, port)`

**Type:** CODE_GEN handler (returns a Python script).

**Output:** A standalone Python script the user can run from the Isaac-GR00T
repo root (or via `exec()` in Script Editor). The script:

1. Imports `huggingface_hub.snapshot_download` and downloads the checkpoint.
2. Resolves the local path.
3. Launches `scripts/deployment/policy_server.py` as a subprocess with the
   chosen embodiment config, host, and port.
4. Prints the PID and waits for `SIGTERM` to forward to the child.

Defaults: `model_id = "nvidia/GR00T-N1.6-3B"`, `embodiment_config =
"LIBERO_PANDA"`, `host = "0.0.0.0"`, `port = 5555`.

**Why CODE_GEN, not DATA:** the user needs the file on disk for reproducibility;
the LLM asking the Kit process to fork a GR00T server is architecturally wrong
(Kit dies → server orphaned). The generated script is user-owned.

### 7G-A.4 `convert_demos_to_lerobot(hdf5_dir, output_dir, task_name, fps=30)`

**Type:** CODE_GEN handler (returns a Python script).

**Output:** A script that:

1. Iterates every `*.hdf5` file under `hdf5_dir` (Phase 7C.3 teleop format).
2. Reads: joint positions, joint velocities, actions, per-step images,
   episode length.
3. Writes a LeRobot v2 dataset layout:
   - `output_dir/data/chunk-000/episode_000000.parquet` etc.
   - `output_dir/meta/info.json` with `codebase_version`, `fps`, `features`.
   - `output_dir/meta/episodes.jsonl`.
   - `output_dir/meta/tasks.jsonl` (single `task_name` row).

The script must be self-contained — the user can commit it — and must
`print(f"Converted {n} episodes → {output_dir}")` at the end so the LLM's
tool-result loop can confirm success.

**Why CODE_GEN:** conversion is a batch job on the user's filesystem. It has
no place inside the Kit process and should leave an artifact that the
fine-tune step in 7G.3 can point at.

---

## Code patterns

- `check_groot_hardware` reads GPU info via `torch.cuda` if importable,
  else via `subprocess.run(["nvidia-smi", ...])` with a 2-second timeout. No
  hard dependency on torch.
- `lookup_groot_embodiment` lives alongside the other lookup handlers
  (`_handle_lookup_product_spec`, `_handle_lookup_knowledge`). Embodiment
  table is a module-level dict; no knowledge-base file needed.
- `generate_groot_deploy_script` / `convert_demos_to_lerobot` follow the
  existing code-gen pattern (`_gen_*` returning `str` of Python source).
  Use `repr()` for any user-supplied path / string literal that ends up in
  the generated file to avoid injection issues.
- Register all four under `DATA_HANDLERS` / `CODE_GEN_HANDLERS` at the end
  of `tool_executor.py`, mirroring the Phase 7A addendum layout.

---

## Schemas (tool_schemas.py)

Four entries appended to `ISAAC_SIM_TOOLS`, under a header comment:

```python
# ─── Phase 7G Addendum: GR00T N1 Tooling ─────────────────────────────────
```

See `tool_schemas.py` for the JSONSchema shape. All four are `type:
function` entries with required-args enforcement.

---

## Test Strategy

| Test                                                    | Level | What                                                      |
|--------------------------------------------------------|-------|-----------------------------------------------------------|
| `check_groot_hardware` — torch available, ≥ 24 GB      | L0    | Mock `torch.cuda`, verify inference_ok=True               |
| `check_groot_hardware` — torch available, 12 GB 5070   | L0    | Mock `torch.cuda`, verify inference_ok=False + hint       |
| `check_groot_hardware` — no torch, nvidia-smi fallback | L0    | Monkeypatch subprocess.run to return `12288 MiB`          |
| `check_groot_hardware` — no torch, no nvidia-smi       | L0    | Monkeypatch to raise FileNotFoundError                    |
| `lookup_groot_embodiment` — exact match                | L0    | "franka" → `LIBERO_PANDA`                                 |
| `lookup_groot_embodiment` — fuzzy match                | L0    | "panda arm" → `LIBERO_PANDA` with alternatives            |
| `lookup_groot_embodiment` — unknown                    | L0    | "xarm7" → `CUSTOM` with warning                           |
| `generate_groot_deploy_script` — compiles              | L0    | `compile()` success + imports snapshot_download           |
| `generate_groot_deploy_script` — custom port & host    | L0    | port / host appear in generated code                      |
| `convert_demos_to_lerobot` — compiles                  | L0    | `compile()` success + writes `info.json`                  |
| `convert_demos_to_lerobot` — paths safely quoted       | L0    | `repr()` used, quote in path doesn't break syntax          |

All eleven tests are L0 — no Kit, no GPU, no network.

---

## Known Limitations

- `check_groot_hardware` does not detect multi-GPU availability for the 2×4090
  LoRA case — it only reports the largest single GPU. A follow-up can sum
  `gpus` for `lora_finetune_ok`.
- `lookup_groot_embodiment` table is hand-maintained. NVIDIA may add more
  pre-registered configs in future GR00T releases.
- `convert_demos_to_lerobot` generates the skeleton; the exact image encoding
  (mp4 vs per-frame png) depends on how Phase 7C.3 writes HDF5. The generated
  script handles the common case (uint8 RGB frames under `observations/rgb`).
