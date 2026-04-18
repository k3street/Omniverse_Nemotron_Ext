# Phase 5 Addendum — Pedagogy Mode & Uncertainty Expression

**Enhances:** Phase 5 (Polish & Fine-Tuning Loop)  
**Source:** Personas P06 (Alessandro, professor), P07 (Thomas, safety)

---

## 5.X1 — Configurable Pedagogy Mode

**Setting:** `PEDAGOGY_MODE=true` in config or toggle in UI

**Behavior changes when enabled:**
- **Explain before fix:** "The robot falls because /World/Ground has no CollisionAPI. This API tells PhysX to include this prim in collision detection. Want me to apply it?"
- **Ask before patch:** Never auto-generate code. Show the concept, ask user to confirm approach, THEN generate
- **Show reasoning:** Every response includes "I checked: [list]. The most likely cause is: [X]. Here's why: [Y]."
- **"Why?" button:** On every action card, a button that expands the reasoning chain

**Implementation:** Add `pedagogy_mode` flag to orchestrator context. When true:
- System prompt appended with: "You are a teaching assistant. Explain concepts before proposing fixes. Ask clarifying questions. Show your reasoning."
- Tool calls wrapped with pre-explanation: before executing, describe what the tool will do and why
- Post-action: explain what happened and what the user should learn from it

---

## 5.X2 — Honest Uncertainty Expression

**All responses tagged with confidence signal:**

```
[HIGH CONFIDENCE] The Franka Panda's reach is 0.855m — this is from the official URDF spec.

[MEDIUM CONFIDENCE] Setting friction to 0.6 for steel-on-rubber should work — based on our material database, but real values vary ±30%.

[LOW CONFIDENCE] I'm not sure why the sim crashes after 1000 steps. It could be memory fragmentation or a PhysX bug. Here are three things to check: ...

[I DON'T KNOW] I haven't found documentation for this specific IsaacLab config parameter. Check the source code at isaaclab/envs/mdp/ or ask on GitHub Discussions.
```

**Implementation:** LLM prompted to self-assess confidence. Calibrated with examples:
- Factual API answers = HIGH
- Physics parameter suggestions = MEDIUM (always vary)
- Debugging hypotheses = LOW (multiple possible causes)
- Undocumented features = I DON'T KNOW

**Thomas's requirement (safety):** INFERRED and UNKNOWN outputs cannot be used in safety-relevant simulation runs without human override + documented justification.

---

## 5.X3 — Intentionally Broken Scene Creation (Teaching)

**Tool:** `create_broken_scene(fault_type)`

For professors: create a scene with a specific, diagnosable fault for students to find and fix.

**Fault types:**
| Type | What's Wrong | Learning Goal |
|------|-------------|---------------|
| `missing_collision` | Ground plane has no CollisionAPI | Physics basics |
| `zero_mass` | Robot link has mass=0 | Inertia understanding |
| `wrong_scale` | Object imported at 100x scale (cm vs m) | USD units |
| `inverted_joint` | One joint axis flipped | URDF import debugging |
| `no_physics_scene` | PhysicsScene prim missing | Scene setup |
| `inf_joint_limits` | Joint limits set to ±inf | URDF best practices |

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Pedagogy prompt injection | L0 | Verify system prompt changes when flag=true |
| Confidence classification | L0 | Known answer types → correct confidence level |
| Broken scene generation | L0 | Each fault type → correct broken attribute |
