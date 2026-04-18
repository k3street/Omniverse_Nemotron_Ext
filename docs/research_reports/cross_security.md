# Cross-Cutting: Security Review

**Agent:** Security Review  
**Date:** 2026-04-15  
**Status:** Complete

## Threat Model

```
[Browser/Chat UI]
      |  HTTP (no auth, wildcard CORS)
[FastAPI :8000] ─── [LLM API (Cloud/Ollama)]
      |  HTTP (no auth, localhost only — but bound to 0.0.0.0)
[Kit RPC :8001]
      | exec() on Kit main thread
[Isaac Sim process — full Python, full filesystem]
      |
[Host OS — GPU, files, network, env vars]
```

External agents via MCP SSE server at `:8002`.

---

## CRITICAL-1: Unauthenticated `exec()` on Kit's Main Thread

Kit RPC `:8001` accepts POST to `/exec_sync` and `/exec_patch`, calls `exec(code, {"__builtins__": __builtins__})`. No auth, no allowlist, no sandbox. Full Python builtins including `__import__`, `open`, `os`, `subprocess`.

**Mitigations:**
1. Bind Kit RPC to Unix domain socket or enforce pre-shared secret header
2. Replace raw `exec()` with `RestrictedPython` or structured command dispatcher
3. Run in subprocess with seccomp + AppArmor

## CRITICAL-2: Zero Authentication on FastAPI

```python
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, ...)
```

No API key, no token check. Any caller can issue tool calls, read/write `.env` (including API keys), enable `AUTO_APPROVE`.

**Mitigations:**
1. Bearer token middleware
2. Explicit CORS allowlist
3. Bind to `127.0.0.1` by default

## CRITICAL-3: `AUTO_APPROVE` Governance Bypass

MCP `update_settings` can set `AUTO_APPROVE=true` with no auth, permanently disabling approval.

**Fix:** Remove `AUTO_APPROVE` from MCP-settable keys.

## HIGH-1: `run_usd_script` — Inadequate Filtering

Code goes through `patch_validator.py` which only checks Isaac Sim API correctness, not dangerous Python. Policy engine's check skipped for this path.

Bypasses: `importlib.import_module('os')`, `__import__('subprocess')`, etc.

**Fix:** AST-based dangerous-import check before all Kit exec paths.

## HIGH-2: ZMQ Bridge (7F) — No Authentication

ZeroMQ default = no auth. Same pattern as CVE-2025-30165 (vLLM), CVE-2025-23254 (TensorRT-LLM).

**Fix:** Mandate ZMQ CURVE authentication. Never deserialize with pickle.

## HIGH-3: Cloud Deployment (7H) — Credential Theft

`cloud_launch(script)` = RCE on cloud instances. No approval gate if AUTO_APPROVE=true.

**Fix:** Always require human approval. Script param restricted to allowlist. Credentials in secrets manager.

## HIGH-4: MCP SSE Server — Unauthenticated

Exposes all 80+ tools plus `get_settings` (returns API keys in plaintext) and `update_settings`.

**Fix:** Token auth. Remove `get_settings`/`update_settings` from MCP surface.

## HIGH-5: Image Upload (6B) — Malicious Files

USD files can contain embedded Python scripts executed on import. GLB can reference external URLs (SSRF). OBJ/MTL can reference arbitrary filesystem paths.

**Fix:** Validate magic bytes. Size limits. Isolated subprocess. Disable script evaluation for USD uploads.

## MEDIUM-1: Path Traversal in `download_scene_file`

`allowed_dir` computed relative to CWD, not absolute. **Fix:** Module-level constant from `__file__`.

## MEDIUM-2: Prompt Injection via Scene Context

Prim names and console logs injected verbatim into LLM context. Prim named `"/World/IgnorePreviousInstructions"` gets included.

**Fix:** Sanitize all USD content in LLM context. Use delimited sections.

## MEDIUM-3: Policy Engine — Trivially Bypassed

Only checks `os.environ` and `subprocess` via string match. `importlib.import_module('os')` not caught.

**Fix:** AST-based analysis instead of string matching.

## MEDIUM-5: API Keys in `get_settings`

Returns `OPENAI_API_KEY`, `API_KEY_GEMINI` in plaintext.

**Fix:** Return only last 4 chars.

## LOW-1: Code Injection via f-string Prim Paths

All `_gen_*` functions interpolate paths directly: `f"stage.GetPrimAtPath('{prim_path}')"`. Single quote in path = code injection.

**Fix:** Validate paths against USD path spec before interpolation.

## LOW-2: CVE-2025-32210 (CVSS 9.0)

Isaac Lab deserialization RCE. Patched in v2.3.0 (December 2025). Verify version.

---

## Top 5 Actions (This Sprint)

1. **Bearer token on FastAPI** — one `Depends()` middleware
2. **Unix socket or HMAC on Kit RPC** — eliminates unauthenticated exec
3. **Remove AUTO_APPROVE and API keys from MCP tools** — one-line changes
4. **AST-based dangerous-import check** before Kit exec
5. **Upgrade to Isaac Sim ≥2.3.0**

## Sources
- [CVE-2025-32210 — NVIDIA Isaac Lab](https://nvidia.custhelp.com/app/answers/detail/a_id/5733)
- [ShadowMQ — AI Inference RCE via ZMQ](https://cyberwarzone.com/2025/11/16/shadowmq-flaw-exposes-ai-inference-engines-to-remote-code-execution/)
- [MCP Attack Vectors — Palo Alto Unit42](https://unit42.paloaltonetworks.com/model-context-protocol-attack-vectors/)
