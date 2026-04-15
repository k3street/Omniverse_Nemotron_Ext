# Security Review — Proportionality Counter-Analysis

**Date:** 2026-04-15  
**Status:** Complete  
**Subject:** Rebuttal of `cross_security.md` findings against Isaac Assist threat model

---

## Framing: What Kind of Tool Is This?

Isaac Assist is a **single-developer CLI extension** running on a local workstation inside NVIDIA Isaac Sim. The threat model is:

- One user, one machine
- No multi-tenancy
- No production data
- No public network exposure (all ports local by default)
- The user already has root, already controls the process, already has full filesystem access

The original report applies a **production web service** security model: bearer tokens, allowlists, sandboxing, secrets manager. That model is appropriate for a SaaS API with anonymous external callers. It is not the right baseline for a local developer tool. This review evaluates each finding against the *actual* threat model.

---

## CRITICAL-1: exec() on Kit RPC — DOWNGRADE to LOW / INFORMATIONAL

### The finding
Kit RPC `:8001` calls `exec(code, {"__builtins__": __builtins__})` with no auth, no allowlist, no sandbox.

### Why it is overblown

**The host process already is the sandbox.** Isaac Sim is an NVIDIA Omniverse application built on the Kit SDK. Kit is an extensible Python runtime where *every* extension executes arbitrary Python in the same process with full builtins. This is not a design flaw — it is the entire architecture. NVIDIA's own Isaac Sim extensions call `exec()` routinely: the Script Editor, Python Console, and Action Graph all accept and execute arbitrary user code.

Sandboxing `exec()` on Kit's main thread would be:
1. **Architecturally impossible** without forking the entire Kit runtime into a subprocess — which breaks GPU context sharing, OmniGraph, and the Nucleus connection.
2. **Redundant** — the developer already has shell access, VS Code, and can edit Isaac Sim source directly.
3. **Counterproductive** — the entire value proposition of Isaac Assist is that the LLM can write and run Python that manipulates the live simulation. If you restrict `exec()`, you have deleted the product.

The only scenario where this matters is if an attacker can reach port `8001` from another process on the same machine or from a different machine on the network. The code in `kit_tools.py` already hardcodes the target as `http://127.0.0.1:8001` — the FastAPI service calls Kit RPC only from localhost. The Kit RPC itself is a local extension endpoint. There is no network-exposed path.

**Actual risk:** Near zero for single-developer local use. The recommendation to use `RestrictedPython` or `seccomp + AppArmor` is disproportionate — those tools do not compose with Omniverse's GPU-accelerated simulation stack.

**Legitimate concern (worth noting):** If Isaac Sim is run inside a shared workstation (multiple users, SSH access) then binding Kit RPC to `0.0.0.0` would be a real issue. A comment in the startup docs saying "Kit RPC binds to 127.0.0.1 by default; do not expose externally on shared hosts" is the appropriate mitigation, not RestrictedPython.

---

## CRITICAL-2: No Auth on FastAPI — DOWNGRADE to LOW (with one real sub-issue)

### The finding
`allow_origins=["*"]`, no bearer token, any caller can issue tool calls.

### Industry context
Compare with the developer tools this is designed to run alongside:

| Tool | Default auth | CORS |
|---|---|---|
| Jupyter Notebook | None (token only since 2016, disabled in many setups) | localhost only |
| Ollama | None | `*` wildcard by default |
| ComfyUI | None | localhost |
| Stable Diffusion WebUI | None | localhost |
| LM Studio | None | localhost |
| Open WebUI | Optional | configurable |

**None of these tools require authentication by default.** They are all local developer tools. Isaac Assist follows the same pattern as every comparable tool in the ecosystem.

The `allow_origins=["*"]` CORS header is standard practice for a service consumed by a browser-based UI on the same machine. The browser's same-origin policy is irrelevant when the server itself is the one setting the CORS header.

### The one sub-issue that IS real
The FastAPI service defaults to binding on `0.0.0.0` (`--host 0.0.0.0` in `main.py`). This means on a machine connected to a LAN, the API is reachable from any other machine on that network. **This is a genuine misconfiguration.** Binding to `127.0.0.1` by default costs nothing and eliminates the network exposure entirely. The wildcard CORS header combined with `0.0.0.0` binding is the only combination that creates a real cross-machine attack surface.

**Verdict:** Change the default `--host` from `0.0.0.0` to `127.0.0.1`. That one change largely defuses the CRITICAL-2 finding. Bearer token auth is not justified for a single-developer local tool.

---

## CRITICAL-3: AUTO_APPROVE via MCP — DOWNGRADE to MEDIUM

### The finding
MCP `update_settings` can set `AUTO_APPROVE=true` with no auth, permanently disabling the approval gate.

### Why it is overblown

MCP (Model Context Protocol) is designed as a **trusted local agent interface**. Claude Desktop, Cursor, and other MCP clients connect to MCP servers the user has explicitly configured. The trust model is: if you configure a tool to connect to an MCP server, you trust that tool. The Palo Alto Unit42 reference cited in the original report is about **prompt injection via untrusted MCP servers** — not about a trusted local MCP connection calling `update_settings`.

The realistic attack path here requires:
1. The developer has connected a malicious MCP client to their local MCP server at `127.0.0.1:8002`
2. That client calls `update_settings` to enable `AUTO_APPROVE`
3. The client then uses the auto-approved mode to execute destructive operations

If step 1 is possible (attacker-controlled process running on the same machine with knowledge of the MCP port), the attacker already has local code execution — `AUTO_APPROVE` is the least of the developer's problems.

### The legitimate concern
The issue that IS real: `get_settings` returns API keys in plaintext (`OPENAI_API_KEY`, `API_KEY_GEMINI`). Any MCP client can read these. This is a concrete data exfiltration risk even in a local context, because it creates a codified API to extract secrets programmatically. The `AUTO_APPROVE` vector is largely theoretical; the API key exposure is not.

**Verdict:** Remove `get_settings` / `update_settings` from the MCP surface (or at minimum mask secret values). Keep `AUTO_APPROVE` off by default and document it. Calling this CRITICAL overstates the risk.

---

## HIGH-1: run_usd_script Filtering — DOWNGRADE to INFORMATIONAL

### The finding
`run_usd_script` passes code through `patch_validator.py` (Isaac Sim API correctness only), not through the policy engine's dangerous-import check. `importlib.import_module('os')` bypasses filters.

### Why it is overblown

`run_usd_script` is, by design, the **arbitrary code execution tool**. From `tool_executor.py`:

```python
if tool_name == "run_usd_script":
    code = arguments.get("code", "")
    # Pre-flight validation
    issues = validate_patch(code)
    ...
    result = await kit_tools.queue_exec_patch(code, desc)
```

The tool's description in `tool_schemas.py` is a script execution primitive — the LLM uses it when it needs to do something the structured tools cannot express. Blocking `os` and `subprocess` in this tool would make it non-functional for legitimate uses like reading files, inspecting environment variables for path resolution, or running `ros2 topic list` to debug ROS connectivity.

The reviewer's suggested fix — "AST-based dangerous-import check before all Kit exec paths" — would add friction to every LLM-generated script, break legitimate use cases, and still be trivially circumvented (`globals()['__builtins__'].__dict__['__import__']('os')`). AST-based restrictions are a sound defense against script injection in web apps where the caller is untrusted. Here the caller is the developer's own LLM assistant.

**The correct framing:** The boundary is Kit/Isaac Sim, not `os`. If `os.system('rm -rf /')` reaches Kit, the damage is to the local workstation that the developer already controls. The tool is working as designed.

**Legitimate concern:** The policy engine's string-match approach (`"os.environ" in code`) is documented as a correctness hint to the LLM, not a security boundary. The comment in code should be explicit about this so future developers don't treat it as a security control.

---

## HIGH-2: ZMQ Bridge — LEGITIMATE HIGH (retain)

### Assessment: Correctly rated

CVE-2025-30165 and CVE-2025-23254 demonstrate that unauthenticated ZMQ endpoints with pickle deserialization are a real, exploited vulnerability class in AI/ML infrastructure. Unlike the other findings, this is not "dev tool on localhost." ZMQ bridges are commonly used to connect distributed RL training nodes, and the 7F ZMQ bridge appears designed for multi-machine scenarios. Pickle deserialization on an unauthenticated socket is RCE waiting to happen, even on a local network.

**Retain as HIGH.** The fix (ZMQ CURVE auth, no pickle) is straightforward and well-documented.

---

## HIGH-3: Cloud Deployment — LEGITIMATE MEDIUM-HIGH (retain, slightly soften)

### Assessment: Largely correct

`cloud_launch(script)` executing arbitrary scripts on cloud instances is genuinely consequential: this crosses from local workstation impact to cloud cost, credential exposure, and data loss. The approval gate matters here specifically because the *consequence scope* is not local. The reviewer is right that this path always requires human approval regardless of `AUTO_APPROVE` state.

**Retain, but soften from HIGH to MEDIUM-HIGH:** The scenario requires the user to have already configured cloud credentials and a cloud provider in settings. This is not an easy accidental trigger. The "script param restricted to allowlist" recommendation is reasonable for a production-grade deployment but over-engineering for the current use case.

---

## HIGH-4: MCP SSE — API Keys in plaintext — LEGITIMATE MEDIUM (re-rate)

### Assessment: Overrated as HIGH, but the sub-issue is real

The unauthenticated MCP SSE endpoint at `8002` defaults to `127.0.0.1` (confirmed in `config.py`: `mcp_host = "127.0.0.1"`). The "unauthenticated" finding largely evaporates for localhost-only binding.

The real issue is `get_settings` returning `OPENAI_API_KEY` and `API_KEY_GEMINI` in plaintext JSON. Any process on the same machine (not just MCP clients) that can reach `127.0.0.1:8002` and call `POST /mcp` with `{"method": "tools/call", "params": {"name": "get_settings"}}` gets the API keys. This is a concrete, low-effort local information disclosure.

**Re-rate as MEDIUM. Fix: mask secret values in `get_settings` output (show last 4 chars only).**

---

## HIGH-5: Image Upload (6B) — LEGITIMATE MEDIUM

### Assessment: Correctly identified, but severity overstated for local tool

USD files can embed Python (executed on import), OBJ/MTL can reference filesystem paths. For a tool where the developer is importing their own simulation assets, this is very low probability. However, if the tool is ever used to load assets from external sources (Nucleus server, downloaded packs), the risk is real. The `isolated subprocess` recommendation is overkill; disabling USD Python script evaluation on import and validating magic bytes is proportionate.

**Re-rate as MEDIUM.**

---

## MEDIUM-1: Path Traversal in download_scene_file — RETAIN as MEDIUM

Relative path computation for `allowed_dir` is a classic traversal bug. The fix is a one-liner and costs nothing. Retain.

---

## MEDIUM-2: Prompt Injection via Scene Context — LEGITIMATE, but not a security finding

A prim named `/World/IgnorePreviousInstructions` affecting LLM output is real, but this is **model robustness**, not a security vulnerability in the traditional sense. The developer names their own prims. There is no adversarial input path unless the developer loads untrusted USD files. The fix (delimited context sections) is good practice for LLM robustness and should be implemented, but it is not a security issue — it is a quality issue.

---

## MEDIUM-3: Policy Engine Bypass — INFORMATIONAL (re-rate)

As established in HIGH-1 analysis: the policy engine is a correctness hint to the LLM, not a security boundary. Calling `importlib.import_module('os')` a "bypass" implies the policy engine was a meaningful defense. It was not designed to be one. Document this explicitly in code; do not invest in AST hardening.

---

## LOW-1: f-string Prim Path Injection — RETAIN as LOW

Single quotes in prim paths causing code injection in generated scripts is a real (if low-severity) correctness bug. USD path validation before interpolation is a one-liner fix and appropriate.

---

## LOW-2: CVE-2025-32210 — RETAIN, action required

Check Isaac Sim version. If below 2.3.0, upgrade. This is an upstream CVE with a known patch. Non-negotiable regardless of deployment context.

---

## Summary: Correct Threat Model vs Original Report

| Finding | Original | Corrected | Rationale |
|---|---|---|---|
| CRITICAL-1: exec() on Kit RPC | CRITICAL | LOW / INFO | Kit architecture IS exec(); sandboxing is architecturally impossible |
| CRITICAL-2: No auth on FastAPI | CRITICAL | LOW + 1 real fix | Industry norm for local dev tools; only real fix: bind to 127.0.0.1 |
| CRITICAL-3: AUTO_APPROVE via MCP | CRITICAL | MEDIUM | Threat path requires local code exec already; API key leak is real sub-issue |
| HIGH-1: run_usd_script filtering | HIGH | INFO | Tool is designed for arbitrary execution; filter would break it |
| HIGH-2: ZMQ no auth | HIGH | HIGH | Retain — multi-machine ZMQ with pickle is legitimately dangerous |
| HIGH-3: Cloud deployment | HIGH | MED-HIGH | Retain — scope crosses to cloud resources |
| HIGH-4: MCP API keys plaintext | HIGH | MEDIUM | Binding is localhost; plaintext secrets is the real issue |
| HIGH-5: Image upload | HIGH | MEDIUM | Real but low probability in single-developer local use |
| MEDIUM-1: Path traversal | MEDIUM | MEDIUM | Retain — trivial fix |
| MEDIUM-2: Prompt injection | MEDIUM | INFO | Model robustness, not a security vulnerability |
| MEDIUM-3: Policy engine bypass | MEDIUM | INFO | Engine was never a security boundary |
| MEDIUM-5: API keys in get_settings | MEDIUM | MEDIUM | Retain — concrete local info disclosure |
| LOW-1: f-string injection | LOW | LOW | Retain |
| LOW-2: CVE-2025-32210 | LOW | ACTION | Check version now |

---

## Prioritized Fixes (Proportionate to Actual Risk)

These are the changes that provide real security value without breaking the tool:

**Do these (low effort, real benefit):**

1. **Bind FastAPI to `127.0.0.1` by default** — change `--host` default from `0.0.0.0` to `127.0.0.1` in `main.py`. Single-line change. Eliminates all LAN exposure.
2. **Mask secrets in `get_settings`** — return `***...{last4}` for API keys in both `SettingsManager.get_settings()` and the MCP `get_settings` tool. Four lines of code.
3. **Upgrade Isaac Sim to ≥2.3.0** — patches CVE-2025-32210. Non-negotiable.
4. **ZMQ CURVE authentication** — if the ZMQ bridge is used in multi-machine mode, add CURVE. If it is always localhost-only, document that constraint clearly.
5. **Fix path traversal in `download_scene_file`** — compute `allowed_dir` from `__file__`, not CWD.

**Defer or skip:**
- Bearer token auth on FastAPI — not justified for local dev tool
- RestrictedPython / seccomp on Kit exec — architecturally incompatible with Omniverse
- AST-based import blocking on `run_usd_script` — breaks the tool's core purpose
- Removing `AUTO_APPROVE` from MCP-settable keys — acceptable as-is given local trust model; document the setting clearly instead

---

## Closing Assessment

The original reviewer applied a **threat model mismatch**: they evaluated Isaac Assist as if it were a multi-tenant API gateway with anonymous external callers. The real threat model is a single developer running a local orchestration layer between their LLM client and their Isaac Sim session. In that context, 3 of 3 CRITICAL findings collapse to LOW or MEDIUM, and 4 of 5 HIGH findings are either informational or medium.

The two findings that remain genuinely HIGH regardless of deployment model are the ZMQ bridge (multi-machine RCE) and cloud deployment (cross-boundary consequence scope). Everything else is hygiene, not a security emergency.
