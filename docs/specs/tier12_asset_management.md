# Tier 12 — Asset Management (5 atomic tools)

**Status:** Implemented
**Source:** `rev2/brainstorm_missing_atomic_tools.md` (Tier 12)

---

## Why these matter

USD scenes are built up from layered references and payloads — external `.usd`
files composed onto the stage. The LLM today can only blindly drop a single
reference (`add_reference` from PR #1 / USD basics). It cannot *inspect* what
references / payloads are already on a prim, *defer-load* heavy assets, or
*answer "where did this prim come from"* for provenance / debugging.

Tier 12 closes that read/write gap with three DATA introspection tools and
two CODE_GEN patch tools that wrap `Usd.References`, `Usd.Payloads`,
`Usd.PrimCompositionQuery` and the asset-resolver layer.

| # | Tool | Type | Implementation |
|---|------|------|----------------|
| T12.1 | `list_references(prim_path)` | DATA | `prim.GetReferences()` + `Usd.PrimCompositionQuery` to enumerate composed reference arcs |
| T12.2 | `add_usd_reference(prim_path, usd_url)` | CODE_GEN | `prim.GetReferences().AddReference(asset_path, prim_path?)` with optional override |
| T12.3 | `list_payloads(prim_path)` | DATA | `prim.GetPayloads()` + `Usd.PrimCompositionQuery` for payload arcs (deferred-loaded) |
| T12.4 | `load_payload(prim_path)` | CODE_GEN | `stage.LoadAndUnload({prim_path}, set())` to activate a payload subtree |
| T12.5 | `get_asset_info(prim_path)` | DATA | `prim.GetAssetInfo()` + `Sdf.Layer` resolution for origin file / version / hash |

---

## Naming choice — `add_usd_reference` vs `add_reference`

PR #1 (USD basics) already ships `add_reference(prim_path, reference_path)` —
the simple "drop a USD onto a prim" flow used by the scene-building demo. That
tool stays as-is so the existing flow keeps working.

Tier 12 introduces `add_usd_reference(prim_path, usd_url, ref_prim_path?,
layer_offset_seconds?, instanceable?)` — the **full** USD references surface
with the same backing call (`prim.GetReferences().AddReference(...)`) plus the
optional kwargs the LLM needs for advanced scenarios:

- `ref_prim_path` — reference a *specific* prim inside the composed file
  (`AddReference(asset, primPath)`), not the file's defaultPrim.
- `layer_offset_seconds` — `Sdf.LayerOffset(offset, scale)` for animation
  retiming.
- `instanceable` — set `prim.SetInstanceable(True)` after the reference is
  added (USD point-instancing for repeated assets).

Both tools coexist:

- `add_reference` — simple, default-prim only, no instancing. (PR #1, kept.)
- `add_usd_reference` — full surface, used when the user wants a specific
  internal prim, retiming, or instancing. (Tier 12, new.)

---

## DATA vs CODE_GEN split

| Tool | Why DATA / CODE |
|------|-----------------|
| `list_references` | Read-only enumeration of composition arcs — DATA, queues an introspection script. |
| `add_usd_reference` | Mutates the stage's reference list — CODE_GEN, requires user approval. |
| `list_payloads` | Read-only enumeration of payload arcs — DATA. |
| `load_payload` | Mutates the load-set on the stage — CODE_GEN (activating a 200-MB asset is a non-trivial side effect). |
| `get_asset_info` | Read-only — DATA. Reads `assetInfo` metadata + the introducing layer's identifier. |

---

## Schema descriptions

All five tool schemas use the WHAT / WHEN / RETURNS / CAVEATS template so the
LLM can disambiguate between:

- `add_reference` (PR #1) — simple drop, default prim, no kwargs.
- `add_usd_reference` (Tier 12) — full surface with `ref_prim_path` /
  `layer_offset_seconds` / `instanceable`.
- `list_references` vs `list_payloads` — references are *always* loaded;
  payloads are deferred until `stage.Load()` / `load_payload`.
- `get_asset_info` — origin / version / hash provenance, NOT runtime state.

---

## Test strategy

Each tool: L0 unit tests (mock Kit RPC, verify generated script compiles +
contains expected USD-API calls). Real Kit verification is L3 and runs against
an Isaac Sim instance with a known reference / payload structure on the stage
(`/NVIDIA/Assets/Isaac/5.1/Robots/Franka/franka.usd` is convenient — its
default prim is a payload).

L0 coverage:

- Schema validation: all 5 tools registered, exactly 5, no name clashes with
  PR #1 `add_reference` / PR #59 / PR #23, rich descriptions present.
- DATA handlers: response shape matches docs, generated script compiles,
  prim path round-trips through `repr()` even with special chars.
- CODE_GEN handlers: generated code compiles, contains expected USD calls,
  optional kwargs (`ref_prim_path`, `layer_offset_seconds`, `instanceable`)
  show up in the generated script when supplied and are absent when omitted.
