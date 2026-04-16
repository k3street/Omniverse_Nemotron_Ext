# Community & Remote Addendum

**For:** The session adding Isaac Assist's collaboration / sharing surface.
**Priority:** Add after the scene-export pipeline so packages can flow out
to the community and back.
**Effort:** Small — five tool handlers, no new Kit RPC endpoint.

---

## Motivation

Isaac Assist now produces high-quality scene packages, RL tasks, SDG
configurations and safety reports. None of that travels:

- Users have no first-class way to publish a finished workcell so a
  colleague can pull and replay it.
- There is no discovery surface — users cannot ask "find me a Franka
  pick-and-place starter" without leaving the chat.
- Pair-debugging across machines requires Discord screen-share; there is
  no in-tool invite for a remote Kit session.
- Bridging to a remote Kit RPC (lab GPU, cloud instance) is hand-rolled
  every time.
- Repeated multi-step sequences (apply collider → tune mass → add IMU)
  cannot be packaged as a reusable "skill recipe" so the LLM can
  one-shot them next session.

All five gaps are pure data / code-gen. None of them needs the running
Kit process — they manipulate workspace files, registry indexes and
remote URLs.

---

## Tools

### CR.1 `share_scene_to_community(scene_name, author, description, tags, license)`

**Type:** DATA handler (filesystem write, no code gen).

**Logic:**

1. Verify `workspace/scene_exports/<scene_name>/` exists (the directory
   that `export_scene_package` writes to).
2. Build a manifest dict: `name`, `author`, `description`, `tags` (list),
   `license` (`MIT` / `Apache-2.0` / `CC-BY-4.0` / `proprietary`),
   `created_utc`, `files` (mirror of files in the export dir).
3. Compute SHA-256 of every file in the export dir; record under
   `manifest["checksums"][filename]`.
4. Write `<scene_name>/community_manifest.json` next to the export.
5. Append a registry row to `workspace/community/registry.jsonl` with
   `{name, author, tags, license, manifest_path, created_utc}`.
6. Return the manifest path, registry path, and sha map.

Required args: `scene_name`, `author`, `description`. Optional:
`tags` (default `[]`), `license` (default `"MIT"`).

**Why DATA:** publishing is a filesystem operation; no Kit interaction.

### CR.2 `search_community_scenes(query, tag, license, limit)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Read every line of `workspace/community/registry.jsonl` (tolerate the
   file being missing — return empty `results`).
2. Score each row: `+3` per tag match, `+2` if `query` substring in
   `name`, `+1` if `query` substring in `description`. `license` filter
   is a hard equality check.
3. Sort descending, truncate to `limit`.
4. Return `{results: [...], count, total_indexed}`.

Required args: none. Optional: `query`, `tag`, `license`, `limit`
(default `10`).

**Why DATA:** result is a list of pointers for the LLM to suggest;
the user picks one to import.

### CR.3 `remote_session_invite(session_name, expires_in_minutes, allow_write)`

**Type:** DATA handler (filesystem write, no network).

**Logic:**

1. Generate a 32-hex token via `secrets.token_hex(16)`.
2. Compute `expires_utc = now + minutes`.
3. Build invite URL: `isaac-assist://join/<token>?write=<0|1>` and a
   matching HTTPS fallback URL the chat UI can render as a clickable
   link: `https://isaac-assist.local/invite/<token>`.
4. Append a row to `workspace/community/invites.jsonl` with the token,
   session name, expiry, write flag, and `created_utc`.
5. Return both URLs, the token (for QR codes), expiry timestamp and a
   short human-readable note.

Required args: `session_name`. Optional: `expires_in_minutes` (default
`60`, max `1440`), `allow_write` (default `False`).

**Why DATA:** the artifact is a URL the user shares — no Kit involvement.

### CR.4 `connect_remote_kit(host, port, auth_token, name)`

**Type:** DATA handler (filesystem write, no network call).

**Logic:**

1. Validate `host` is a hostname or IPv4 (no spaces, no scheme).
2. Validate `port` is in `1..65535`.
3. Validate `auth_token` is a non-empty string of length ≥ 8.
4. Build a connection profile dict: `name`, `host`, `port`, `url`
   (`http://<host>:<port>`), `ws_url` (`ws://<host>:<port>/ws`),
   `auth_token_preview` (first 4 + last 4 chars only), `added_utc`.
5. Read existing `workspace/community/remote_kits.json` (or seed an
   empty `{"profiles": []}`).
6. Replace any profile with the same `name`; append otherwise.
7. Write the file back.
8. Return the profile (with the redacted token), the file path, and a
   note telling the LLM to surface the URL to the user.

Required args: `host`, `port`, `auth_token`. Optional: `name` (default
`"remote-kit"`).

**Why DATA:** persists a config the user later activates manually; no
Kit handshake happens here, so we cannot write CODE_GEN.

### CR.5 `publish_skill_recipe(recipe_name, description, steps, inputs, tags)`

**Type:** CODE_GEN handler (returns Python source for the recipe).

**Output:** A standalone Python script under
`workspace/community/skills/<recipe_name>.py` that:

1. Defines a top-level docstring with the recipe description and a
   manifest dict (name, tags, inputs schema).
2. Defines a `def run(inputs: dict) -> dict:` entry point.
3. Inside `run`, emits one numbered comment + the original step's code
   for each step in `steps` (each step is `{description, code}`).
4. Returns `{"completed_steps": <n>, "recipe": "<name>"}` so the caller
   can chain.

Also writes `workspace/community/skills/<recipe_name>.yaml` with the
manifest (name, description, tags, inputs, step descriptions).

Required args: `recipe_name`, `description`, `steps` (list of
`{description, code}`). Optional: `inputs` (list of input names,
default `[]`), `tags` (list, default `[]`).

**Why CODE_GEN:** the artifact is runnable Python the user (or the LLM)
can `exec()` later; the caller needs `compile()` to succeed before the
file is written.

---

## Code patterns

- All five live at the bottom of `tool_executor.py` under a `Community
  & Remote Addendum` header, mirroring the Safety & Compliance layout.
- `share_scene_to_community` uses `hashlib.sha256` chunked reads.
- `search_community_scenes` uses simple substring matching — no
  embeddings.
- `remote_session_invite` uses `secrets.token_hex` and `datetime.utcnow`.
- `connect_remote_kit` writes JSON pretty-printed (indent=2) so a human
  can edit it.
- `publish_skill_recipe` uses `repr()` for every user-supplied string
  that ends up in generated source.
- Register one CODE_GEN_HANDLER + four DATA_HANDLERS at the end of the
  file.

---

## Schemas (tool_schemas.py)

Five entries appended to `ISAAC_SIM_TOOLS`, under a header comment:

```python
# ─── Community & Remote Addendum ──────────────────────────────────────
```

All five are `type: function` entries with required-args enforcement.

---

## Test Strategy

| Test                                                          | Level | What                                                  |
|---------------------------------------------------------------|-------|-------------------------------------------------------|
| `share_scene_to_community` — writes manifest + registry       | L0    | manifest.json on disk, registry has new row           |
| `share_scene_to_community` — missing export dir → error       | L0    | returns `error` field, no files written               |
| `share_scene_to_community` — checksum stable                  | L0    | sha256 of known content matches expected              |
| `search_community_scenes` — empty registry → no crash         | L0    | returns `{count: 0}` cleanly                          |
| `search_community_scenes` — tag + license filter              | L0    | only matching rows surface                            |
| `search_community_scenes` — score ordering                    | L0    | tag-match outranks description-match                  |
| `remote_session_invite` — generates URLs + persists row       | L0    | token length, both URL forms present, jsonl appended  |
| `remote_session_invite` — clamps expiry                       | L0    | expires > 1440 → clamped to 1440                      |
| `remote_session_invite` — write flag toggles URL              | L0    | `write=1` vs `write=0` reflected in URL               |
| `connect_remote_kit` — valid profile written + token redacted | L0    | file present, only preview chars stored               |
| `connect_remote_kit` — invalid port rejected                  | L0    | returns error, no file write                          |
| `connect_remote_kit` — short token rejected                   | L0    | returns error                                         |
| `connect_remote_kit` — replaces same-name profile             | L0    | second call updates, doesn't duplicate                |
| `publish_skill_recipe` — code compiles                        | L0    | `compile()` success                                   |
| `publish_skill_recipe` — yaml + py written                    | L0    | both files exist; yaml lists step descriptions        |
| `publish_skill_recipe` — recipe_name with quotes safely quoted| L0    | weird name still compiles                             |
