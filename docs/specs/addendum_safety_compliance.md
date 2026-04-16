# Safety & Compliance Addendum

**For:** The session building Isaac Assist's safety / regulatory guard rails.
**Priority:** Add before any robot is deployed outside the sim cage.
**Effort:** Small — four tool handlers, no new Kit RPC endpoint.

---

## Motivation

Isaac Assist currently lets an LLM generate arbitrary robot motions, SDG
scenes and RL reward terms. None of those tools understands the physical
world's safety constraints:

- An industrial robot that exceeds ISO 10218-1 joint-velocity / TCP-speed
  limits will not be certifiable for collaborative work.
- A workcell without declared Category 3 PL-d safety-rated monitored stop
  zones cannot legally share space with humans.
- Synthetic-data pipelines used for model training may harvest images of
  people; GDPR Art. 35 DPIA obligations kick in when biometric data is
  generated, even in sim.
- Generated code that drives real hardware needs an auditable record of
  which ISO / IEC clauses were satisfied, so a Functional Safety assessor
  can close a project file.

Each of these is a lightweight pre-flight check. The LLM needs structured
tools to:

1. Validate that a robot's commanded motion envelope is inside ISO 10218-1
   and ISO/TS 15066 bounds.
2. Declare, record and validate safety-rated monitored stop zones around
   operator workspaces (ISO 13855).
3. Assess a synthetic-data scene for personal-data content and produce a
   GDPR Art. 35 DPIA starter record.
4. Generate a compliance report that correlates executed patches with the
   ISO / IEC clauses they satisfy — a single artifact the human signs off.

All four are pure data / code-gen — no Kit RPC, no subprocess, no network.

---

## Tools

### SC.1 `validate_iso10218_limits(robot_type, max_joint_velocity_deg_s, max_tcp_speed_m_s, payload_kg, scenario)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Look up the scenario's bound table:
   - `scenario="collaborative"` → ISO/TS 15066 power & force limits
     (TCP speed ≤ 250 mm/s, quasi-static force ≤ 140 N typical).
   - `scenario="industrial"` → ISO 10218-1 full-speed (TCP ≤ 1500 mm/s,
     joint velocity ≤ 180 deg/s for 6-DoF arms).
   - `scenario="reduced"` → ISO 10218-1 reduced-speed teach (TCP ≤ 250 mm/s).
2. For each limit, compare the caller-supplied number against the bound.
3. Emit a `verdict` (`"compliant"`, `"warning"`, `"violation"`) and a
   list of failing clauses.

**Returns:**
```python
{
    "robot_type": "franka",
    "scenario": "collaborative",
    "verdict": "violation",
    "checks": [
        {"clause": "ISO/TS 15066 §5.5.5", "limit": "TCP speed ≤ 0.25 m/s",
         "actual": 0.5, "passed": False},
        {"clause": "ISO 10218-1 §5.10.8", "limit": "joint velocity ≤ 180 deg/s",
         "actual": 120, "passed": True},
    ],
    "recommendation": "Reduce commanded TCP speed to 250 mm/s or switch to industrial scenario.",
}
```

**Why DATA:** the LLM must reason about the numbers before it writes any
motion code. It must be a live lookup, not a user-facing patch.

### SC.2 `declare_safety_zone(zone_name, zone_type, geometry, linked_robot_path)`

**Type:** CODE_GEN handler (returns a Python script).

**Output:** A standalone Python script the user runs in Isaac Sim Script
Editor (or via Kit RPC) that:

1. Creates a USD Xform under `/World/SafetyZones/<zone_name>`.
2. Emits a `Cube` child of the declared geometry (axis-aligned box from
   `geometry={"min": [x,y,z], "max": [x,y,z]}`).
3. Sets `primvars:display:color` based on zone type:
   - `zone_type="restricted"` → red (no-entry while robot powered).
   - `zone_type="monitored"` → amber (reduced-speed if human detected).
   - `zone_type="collaborative"` → green (ISO/TS 15066 envelope).
4. Records the zone in a module-level `metadata` dict as a custom USD
   string attribute `safety:iso13855_classification`.
5. Links the zone to the robot's articulation root by writing a
   relationship `safety:protects` pointing at `linked_robot_path`.

Required args: `zone_name`, `zone_type`, `geometry`. Optional: `linked_robot_path`.

**Why CODE_GEN:** the zone must live in the USD stage so downstream
checkers (and a human reviewing the scene) can see it.

### SC.3 `gdpr_sdg_scan(scene_description, generates_people, generates_biometrics, data_recipients)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Parse the booleans. If `generates_people=False` and
   `generates_biometrics=False`, return `dpia_required=False` with a
   reassurance note.
2. Otherwise build a DPIA starter record:
   - lawful basis hint (`"Art. 6(1)(f) legitimate interest"` default,
     `"Art. 9(2)(a) explicit consent"` if biometric).
   - risk class (`"low"` if only synthetic strangers, `"high"` if
     biometric data generation).
   - minimum controls (pseudonymisation, retention limit, recipients list).
3. Emit the list of required Art. 35 §7 elements (purpose, necessity,
   risks, safeguards).

**Returns:**
```python
{
    "dpia_required": True,
    "risk_class": "high",
    "lawful_basis_hint": "Art. 9(2)(a)",
    "required_elements": [...],
    "minimum_controls": [...],
    "recommendation": "Attach to project wiki; review with DPO before training runs.",
}
```

**Why DATA:** the result is a checklist for the human — no code needs to
touch the Kit process.

### SC.4 `generate_compliance_report(scene_name, session_id, standards)`

**Type:** DATA handler (filesystem write, no code gen).

**Logic:**

1. Walk the audit log (`routes._audit.query_logs`) for the session_id and
   collect all successfully-executed patches.
2. For every patch, look up which declared safety tools (`validate_iso*`,
   `declare_safety_zone`, `gdpr_sdg_scan`) fired in the same session.
3. Build a Markdown document `workspace/compliance_reports/<scene>.md`
   with:
   - Summary table: clause → status → evidence link.
   - Patch log.
   - Safety-zone inventory.
   - DPIA status.
   - Sign-off lines for Functional Safety assessor + DPO.
4. Return the path and a machine-readable dict mirror.

Required args: `scene_name`. Optional: `session_id` (default
`"default_session"`), `standards` (list; default
`["ISO 10218-1", "ISO/TS 15066", "ISO 13855", "GDPR Art. 35"]`).

**Why DATA:** the artifact is committed by the user — not executed in
Kit.

---

## Code patterns

- `validate_iso10218_limits` lives next to other lookup handlers.
  Limit tables are module-level dicts; no knowledge-base file needed.
- `declare_safety_zone` follows the existing code-gen pattern (`_gen_*`
  returning `str` of Python source). Use `repr()` for user-supplied name /
  string literal ending up in the generated file.
- `gdpr_sdg_scan` is a pure function — no filesystem, no imports beyond
  the stdlib.
- `generate_compliance_report` writes to `workspace/compliance_reports/`.
  It must tolerate a missing audit log (tests run without routes).
- Register all four at the end of `tool_executor.py`, mirroring the Phase
  7G addendum layout.

---

## Schemas (tool_schemas.py)

Four entries appended to `ISAAC_SIM_TOOLS`, under a header comment:

```python
# ─── Safety & Compliance Addendum ─────────────────────────────────────
```

All four are `type: function` entries with required-args enforcement.

---

## Test Strategy

| Test                                                          | Level | What                                                       |
|---------------------------------------------------------------|-------|------------------------------------------------------------|
| `validate_iso10218_limits` — compliant collaborative motion   | L0    | TCP 0.2 m/s → verdict=compliant                            |
| `validate_iso10218_limits` — TCP speed violation              | L0    | TCP 0.5 m/s collaborative → verdict=violation, clause hit  |
| `validate_iso10218_limits` — industrial scenario              | L0    | Fast joint motion passes industrial, fails collaborative   |
| `declare_safety_zone` — compiles                              | L0    | `compile()` success + DefinePrim present                   |
| `declare_safety_zone` — zone color matches type               | L0    | restricted → red, monitored → amber, collaborative → green |
| `declare_safety_zone` — quoting safely                        | L0    | Zone name with quote does not break syntax                 |
| `gdpr_sdg_scan` — no people required                          | L0    | `dpia_required=False` when no people                       |
| `gdpr_sdg_scan` — biometric triggers high risk                | L0    | `risk_class="high"` + Art. 9(2)(a) suggestion              |
| `gdpr_sdg_scan` — synthetic people low risk                   | L0    | `risk_class="low"` + Art. 6(1)(f)                          |
| `generate_compliance_report` — writes file                    | L0    | Markdown file exists under tmp workspace                   |
| `generate_compliance_report` — includes standards             | L0    | Every requested standard appears in the report             |

All eleven tests are L0 — no Kit, no GPU, no network.

---

## Known Limitations

- `validate_iso10218_limits` only covers the three named scenarios.
  Extended clauses (ISO 10218-2 workcell, IEC 61508 SIL) are out of
  scope for this addendum.
- `declare_safety_zone` produces geometry only; separating zone
  monitoring from the robot controller (e.g. safety-rated monitored
  stop wiring) is outside Isaac Sim's remit.
- `gdpr_sdg_scan` is a starter checklist, not a legal opinion. The DPO
  owns sign-off.
- `generate_compliance_report` does not evaluate evidence — it
  correlates; a human assessor reads and signs.
