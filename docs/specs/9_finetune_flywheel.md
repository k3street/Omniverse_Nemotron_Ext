# Phase 9 — Fine-Tune Flywheel: Self-Improving Tool Calling

**Status:** Not implemented (data capture partially designed in Phase 5)  
**Depends on:** Phase 5 (data export infrastructure), Phase 1A (tool calling working)  
**Research:** `rev2/defend_spec_strengths.md` (flagged as strategic moat)

---

## Overview

Every user session generates training data: what the user asked, what tools the LLM selected, what code it generated, whether the user approved or rejected, and what the outcome was. Phase 9 turns this into a closed-loop improvement system.

**This is the strategic moat.** Competitors can replicate the architecture but not the accumulated interaction data.

---

## Two Stages

### Stage A — Instrumentation (Start immediately, no users needed)

Ensure every chat turn produces a complete, structured training record.

**Record schema:**
```json
{
  "session_id": "uuid",
  "turn_id": 0,
  "timestamp": "2026-04-15T14:30:00Z",
  "isaac_sim_version": "5.1.0",
  
  "input": {
    "user_message": "make the arm grab the cup",
    "selected_prim": "/World/Franka",
    "viewport_thumbnail_b64": "...",
    "stage_context": { "prim_type": "Articulation", "schemas": [...] }
  },
  
  "intent": {
    "classified_as": "patch_request",
    "tools_offered": ["move_to_pose", "grasp_object", "set_joint_targets"],
    "distiller_reasoning": "motion + manipulation intent"
  },
  
  "output": {
    "tool_calls": [
      {
        "tool": "move_to_pose",
        "arguments": { "articulation_path": "/World/Franka", "target_position": [0.5, 0.2, 0.3] },
        "generated_code": "rmpflow.set_end_effector_target(...)",
        "execution_result": "success"
      }
    ],
    "assistant_message": "I'll move the Franka arm to the cup position..."
  },
  
  "feedback": {
    "user_approved": true,
    "user_rejected": false,
    "user_edited": false,
    "execution_success": true,
    "error_message": null,
    "follow_up_correction": null
  }
}
```

**Implementation tasks:**

- [ ] **9A.1** Define record schema (above) and create `TurnRecorder` class
- [ ] **9A.2** Hook `TurnRecorder` into `orchestrator.py` — capture every turn automatically
- [ ] **9A.3** Store records as JSONL at `workspace/finetune_data/sessions/{date}.jsonl`
- [ ] **9A.4** Capture approval/rejection from governance flow — this is the highest-signal feedback
- [ ] **9A.5** Capture follow-up corrections: if user immediately re-phrases or says "no, I meant...", link to previous turn
- [ ] **9A.6** Redact sensitive data: strip API keys, file paths outside workspace, user-identifiable info
- [ ] **9A.7** Export CLI: `python -m isaac_assist_service.finetune export --format openai|anthropic|ollama`
- [ ] **9A.8** Data quality dashboard: turn count, approval rate, tool distribution, error rate over time

**Signals ranked by training value:**
1. **User rejected + corrected** — strongest signal (the model was wrong AND the user showed the right answer)
2. **User rejected** — model was wrong
3. **Execution failed** — generated code had errors
4. **User approved + succeeded** — positive example
5. **Auto-approved + succeeded** — weaker positive (no explicit human validation)

### Stage B — Fine-Tuning Loop (When data exists)

**Trigger:** ~500+ approved turns with diverse tool usage.

**Approach:** LoRA fine-tuning on tool-calling format, NOT full model training.

- [ ] **9B.1** Data preprocessing: filter to approved+successful turns, balance tool distribution
- [ ] **9B.2** Format converter: session records → provider-specific fine-tune format
  - OpenAI: `{"messages": [...], "tools": [...]}` JSONL
  - Anthropic: tool_use training format
  - Ollama/local: Unsloth LoRA format (already mentioned in Phase 5.2)
- [ ] **9B.3** Validation split: 90/10 train/val, stratified by tool type
- [ ] **9B.4** Fine-tune execution:
  - Cloud (Anthropic/OpenAI): API fine-tune job submission
  - Local (Ollama): Unsloth LoRA on quantized base model
- [ ] **9B.5** Evaluation: compare fine-tuned vs base model on held-out val set
  - Metrics: tool selection accuracy, code compilation rate, argument correctness
- [ ] **9B.6** A/B deployment: route X% of traffic to fine-tuned model, compare approval rates
- [ ] **9B.7** Rollback: if fine-tuned model degrades, revert to base automatically

**Autoresearch connection:** This is the Karpathy autoresearch pattern applied to tool calling:
- Fixed metric: tool-call success rate (approval + execution success)
- Agent modifies: LoRA weights via training data selection
- Keep/revert: based on val set performance
- Iterate: as new session data accumulates

---

## Privacy & Consent

- [ ] Opt-in toggle in settings UI: "Allow session data to be used for model improvement"
- [ ] Data stays local by default — never uploaded without explicit action
- [ ] Redaction pipeline strips anything outside `workspace/` paths
- [ ] No viewport images stored unless user explicitly enables it (bandwidth + privacy)

---

## Metrics

| Metric | Source | Goal |
|--------|--------|------|
| Tool selection accuracy | Val set comparison | +15% over base model |
| Code compilation rate | `compile()` on generated code | >95% |
| User approval rate | Session records | Trending upward over time |
| Rejection → correction pairs | Linked turns | Accumulate for highest-value training |
| Error rate | Execution failures | Trending downward |

---

## Dependencies

- Phase 5.2 (Unsloth pipeline) for local fine-tuning
- Phase 1A (tool calling) must be stable
- Governance flow must reliably capture approve/reject signals

## Test Strategy

| Test | Level | What |
|------|-------|------|
| TurnRecorder schema | L0 | Validate JSON schema, required fields |
| Redaction | L0 | API keys, paths outside workspace stripped |
| Export format | L0 | OpenAI/Anthropic/Ollama format correctness |
| Follow-up linking | L0 | Correction turns linked to prior turn |
| Data quality stats | L0 | Counts, distributions from mock data |
| LoRA training | L3 | Requires GPU + base model |

## Known Limitations

- Need ~500+ diverse turns before fine-tuning is meaningful
- Tool distribution will be skewed (USD ops dominate, rare tools underrepresented)
- Rejection signal is noisy — user might reject for non-model reasons (wrong intent, changed mind)
- Viewport images are expensive to store — default off
- Fine-tuned model may overfit to one user's patterns if single-user deployment
