# Phase 7E — LLM Reward Generation: Approach Comparison

**Agent:** Research 7E Approaches (Rev2)
**Date:** 2026-04-15
**Status:** Complete

---

## Background

Phase 7E targets a user flow: describe a robot task in natural language → LLM generates a reward function → RL policy trains in IsaacLab → user gives iterative feedback ("it keeps dropping the handle") → reward is refined and policy retrains. Rev1 identified the wrong repo URL (`isaac-sim/Eureka`) and noted that Eureka's algorithm is greedy-only (not evolutionary). This report compares the three leading candidates plus three newer alternatives and produces a concrete recommendation.

---

## Approach 1: Eureka / IsaacLabEureka

**Paper:** "Eureka: Human-Level Reward Design via Coding Large Language Models" (ICLR 2024, Ma et al., Penn + NVIDIA)
**Repo:** [isaac-sim/IsaacLabEureka](https://github.com/isaac-sim/IsaacLabEureka)

### Architecture

Eureka runs a fixed outer loop:

```
for iteration in range(N):              # typically N=5
    samples = llm.generate(K=16)        # K reward function candidates in parallel
    for each sample:
        run RL training (PPO, ~1000 steps, RSL-RL or RL-Games)
        collect tensorboard metrics (success_metric, per-component stats)
    best = argmax(success_metric)
    reflection = format_feedback(best, all_samples)
    prompt = [env_source_code + task_description + reflection]
```

The LLM receives the **full unmodified environment source code** plus a task description plus (from iteration 2 onward) a structured reward reflection summarizing what worked and what didn't. It outputs Python code for a `compute_rewards(self)` method whose variables are attributes of `self` (the DirectRLEnv instance).

IsaacLabEureka wraps this in a launcher that:
1. Runs `prune_env.py` to prepare the env context
2. Manages parallel RL training processes
3. Reads TensorBoard logs via `_get_eureka_task_feedback()`
4. Constructs the next prompt iteration

### Key Constraints

- **Only `DirectRLEnv` is supported.** Manager-based environments (`ManagerBasedRLEnv`) are explicitly excluded. The reward function must use `self.*` attributes directly.
- **Supported RL libraries:** RSL-RL and RL-Games only (not Stable Baselines3, skrl, CleanRL).
- **LLM backend:** Any OpenAI-compatible API. GPT-4 used in the original paper.
- Each Eureka run (5 iterations × 16 samples × ~10 min RL each) requires roughly 4–8 hours wall-clock on a single RTX 4090 / A100 (16 candidates train in parallel but still need GPU saturation).

### Strengths

- Official NVIDIA repo — actively maintained, Isaac Sim aligned
- Lowest integration cost: designed for IsaacLab's `DirectRLEnv` out of the box
- Reflection mechanism provides automatic reward component diagnosis without human input
- Outperforms expert-human rewards on 83% of 29 tasks (52% average improvement)
- Each RL run ≤8 GB VRAM — can run 4 parallel candidates on a single 40 GB GPU, or 1–2 on a 24 GB consumer card

### Weaknesses

- **Greedy-best-only:** Keeps only the single best candidate per iteration — no population, no crossover. Prone to local optima and premature convergence.
- **Static specification problem:** Reward is fixed once training starts; no mid-training adaptation.
- **No native user feedback hook:** Human input can be added to the reflection prompt manually, but there is no built-in mechanism to parse natural-language critique like "it keeps dropping the handle."
- **Long cycle times:** Useful feedback arrives hours after the user's request. Not interactive.
- **No domain randomization:** Sim-to-real transfer is not addressed.

---

## Approach 2: DrEureka

**Paper:** "DrEureka: Language Model Guided Sim-To-Real Transfer" (RSS 2024, Ma et al., Penn + NVIDIA)
**Repo:** [eureka-research/DrEureka](https://github.com/eureka-research/DrEureka)

### Architecture

DrEureka is a three-stage sequential pipeline that extends Eureka:

```
Stage 1 (Reward):    Eureka generates reward function with safety-biased prompt
Stage 2 (RAPP):      Train initial policy; perturb each physics parameter independently;
                     record which perturbation ranges maintain task performance
                     → produces bounded feasibility intervals per parameter
Stage 3 (DR):        LLM receives {reward_fn, RAPP bounds, task description}
                     → outputs domain_randomization_config.yaml
                     (mass range, friction range, damping range, etc.)
```

The DR config is then used in a final training run with randomized physics to produce a policy that transfers to the real robot.

### Key Constraints

- **Simulator:** IsaacGym Preview 4 only. **Not ported to IsaacLab.** IsaacGym is officially deprecated by NVIDIA (superseded by IsaacLab). Using DrEureka as-is requires reverting to the deprecated stack.
- **No IsaacLab migration path documented** — porting would require rewriting environments from IsaacGymEnvs format to DirectRLEnv.
- RAPP requires at least one complete policy training run before DR generation, adding a full training cycle to the pipeline (total wall-clock ~3 hours per configuration on 16 A100s in the paper, or much longer on a single workstation).

### Strengths

- Only approach that natively addresses sim-to-real transfer — essential for physical robot deployment
- RAPP grounds DR parameter ranges in empirical feasibility (not heuristic bounds), preventing unsafe randomization
- Safety-biased reward prompting reduces reward hacking that causes unsafe physical behaviors
- Substantially faster than alternative DR approaches: ~3 hours vs. 10 hours (CEM) or 20 hours (Bayesian Opt)
- Demonstrated on real robots: 34% velocity improvement on Unitree Go1, ~300% improvement on LEAP hand cube rotation

### Weaknesses

- **IsaacGym dependency = blocking.** Cannot use with current IsaacLab/Isaac Sim 5.1/6.0 stack without a significant porting effort.
- **No human feedback mechanism.** Same greedy-best limitation as base Eureka for the reward stage.
- **Three-stage pipeline doubles total compute** vs. Eureka alone.
- **No population diversity** in reward search (inherits Eureka's greedy approach).
- Requires real hardware or high-fidelity simulation to validate transfer quality.

---

## Approach 3: REvolve

**Paper:** "REvolve: Reward Evolution with Large Language Models using Human Feedback" (ICLR 2025, Hazra et al.)
**Repo:** [RishiHazra/Revolve](https://github.com/RishiHazra/Revolve)

### Architecture

REvolve is a true population-based evolutionary algorithm where the LLM acts as the genetic operator:

```
Population structure: I=13 islands, each with sub-population of up to 8 individuals
                      Total concurrent policies: up to ~104

for generation in range(G=5):
    # Reproduction phase
    for each island:
        select_parents()                    # weighted by fitness σ
        offspring = llm_crossover(p1, p2)   # GPT-4 combines best components of two parents
        offspring = llm_mutation(offspring)  # GPT-4 modifies one reward component
    
    # Evaluation phase
    train_policies(all_offspring)           # parallel RL training
    
    # Human feedback phase (optional)
    present_pairs_of_rollout_videos()       # evaluators watch 30-40s video pairs
    collect_elo_preferences()               # pairwise binary preference → Elo score
    
    # Selection phase
    fitness = w1*task_score + w2*elo_score
    survive = top_K(all_individuals, fitness)
    migrate(between_islands)                # periodically share best across islands
```

Reward functions are Python code of form `r = Σᵢ rᵢ`. Crossover combines the strongest components from two parents; mutation modifies a single component. Natural language qualitative feedback (checkboxes: "more stability," "reduce jerkiness") is passed directly into the LLM prompt alongside the parent reward code.

### Key Constraints

- **Simulator:** MuJoCo (humanoid, manipulation) and AirSim (driving). **Not IsaacLab.** Porting requires adapting environment wrappers.
- **Computational scale:** AirSim experiments used 16×A100 GPUs for ~50 hours per experimental run; MuJoCo used same hardware for ~24 hours. This is research-scale compute, not a single workstation.
- **Human evaluator bottleneck:** The paper used 10 human evaluators per generation, each assessing 20 video pairs. This is expensive and slow — not user-session-speed feedback.
- 5 generations × 15 individuals = 75 total policy training runs before convergence.

### Strengths

- **Best handling of user feedback:** Human language feedback maps naturally into mutation/crossover prompts. A comment like "it keeps dropping the handle" can directly inform a mutation operator targeting grasp stability components.
- Outperforms Eureka on all three tested domains (autonomous driving, humanoid locomotion, dexterous manipulation) with same or lower token cost.
- Population diversity avoids local optima that greedy-best approaches converge to.
- Island model with migration prevents population homogeneity.
- Qualitative feedback (checkbox-style) is practical for real users — no engineering domain knowledge required.

### Weaknesses

- **Not IsaacLab native** — requires porting effort.
- **Extreme compute cost** at full scale: 75+ parallel training runs per optimization loop is infeasible on a single workstation.
- Human evaluation pipeline (pairwise video comparison, Elo rating) is elaborate — not plug-and-play.
- No sim-to-real transfer mechanism.
- Requires multiple human evaluators per generation in its full form; single-user version is an unverified simplification.

---

## Newer Approaches (2025–2026)

### FORGE — Feedback-Optimized Reward Generation and Evolution (ICLR 2026, submitted)

**Source:** [OpenReview Z6GStCfccl](https://openreview.net/forum?id=Z6GStCfccl)

A multi-agent LLM framework combining structured reward initialization, evolutionary refinement, and complexity-aware memory. Multiple agents coordinate: one initializes reward structure, another evolves it, a third maintains a memory of what components worked across tasks. Achieves 38.5% improvement over Eureka and 19.0% over REvolve on the Humanoid task. Designed for gaming + robotics. Not yet published or open-sourced — submitted to ICLR 2026 as of this writing.

**Assessment:** Promising extension of REvolve's ideas. The complexity-aware memory is novel — it enables warm-starting reward search from past runs, which directly benefits an iterative user-facing system. **Not usable yet (no code, under review).**

### MIRA — Metacognitive Introspective Reward Architecture (MDPI Systems 2025)

**Source:** [MDPI 2079-8954/13/12/1124](https://www.mdpi.com/2079-8954/13/12/1124)

A closed-loop dual-loop architecture:
- **Inner loop:** Learns a potential-based shaping signal over reward factors, aligned to sparse extrinsic returns (standard RL).
- **Outer loop:** LLM monitors trajectory-level diagnostics, detects persistent anomalies via online density estimation, and triggers semantic-level edits to the reward factor space mid-training.

Key innovation: reward is a *revisable program* updated in response to the agent's own learning dynamics, not a static artifact. Substantially outperforms static LLM-generated rewards on sample efficiency and robustness to initial reward misspecification.

**Assessment:** The outer loop concept — continuous monitoring + semantic edits — is the most sophisticated handling of the "reward desert" and "specification gaming" failure modes. However, no open-source implementation exists yet, and IsaacLab integration would require significant engineering.

### Text2Reward (ICLR 2024)

**Source:** [text-to-reward.github.io](https://text-to-reward.github.io/)

Generates dense reward functions from natural language without environment source code in the prompt. Uses a compact environment variable description instead. Evaluated on ManiSkill2 and MetaWorld (manipulation) and MuJoCo (locomotion). Can outperform human oracle rewards on convergence speed.

**Assessment:** Lower context requirement than Eureka (no full source code injection) is an advantage. But no IsaacLab integration, no feedback loop, and older than IsaacLabEureka. Not a strong alternative unless context window is a limiting factor.

### ReWiND (CoRL 2025)

**Source:** [arxiv 2505.10911](https://arxiv.org/abs/2505.10911)

Learns a language-conditioned reward model from 5 demonstrations per task, then trains policies with offline RL. Generalizes to unseen tasks. Fundamentally different paradigm: reward from demonstrations + language, not LLM code generation. Achieves 79% success on unseen MetaWorld tasks in 100k steps.

**Assessment:** Orthogonal to the Eureka family. Better suited to manipulation learning from examples than the "user describes task from scratch" scenario in 7E. Not directly applicable without demo data.

---

## Comparison Table

| Criterion | Eureka / IsaacLabEureka | DrEureka | REvolve | FORGE | MIRA |
|---|---|---|---|---|---|
| **Publication** | ICLR 2024 | RSS 2024 | ICLR 2025 | ICLR 2026 (submitted) | MDPI 2025 |
| **IsaacLab native** | Yes (DirectRLEnv only) | No (IsaacGym only) | No (MuJoCo/AirSim) | No | No |
| **Open source / usable now** | Yes | Yes (deprecated sim) | Yes | No | No |
| **Search strategy** | Greedy best-of-K | Greedy (inherits Eureka) | Population + crossover + mutation | Multi-agent evolution + memory | Dual-loop metacognition |
| **Simultaneous candidates** | 16 per iteration | 16 (reward stage) | Up to 104 | Not yet published | N/A |
| **User feedback mechanism** | Manual prompt injection only | None | Native (Elo + qualitative text) | Native (memory-guided) | Continuous (anomaly detection) |
| **Handles "keep dropping handle"** | With manual prompt edit | With manual prompt edit | Yes — directly drives mutation | Yes — stored in complexity memory | Yes — outer loop detects grasping failure |
| **Sim-to-real (DR generation)** | No | Yes (RAPP) | No | Unknown | No |
| **GPU memory per candidate** | ≤8 GB | ≤8 GB | Varies (MuJoCo: low) | Unknown | Unknown |
| **Single-workstation feasible** | Yes (slow) | No (IsaacGym + RAPP overhead) | Reduced population: yes | Unknown | Unknown |
| **Compute for full run** | ~4–8h on RTX 4090 | ~3h on 16×A100 (paper scale) | ~24–50h on 16×A100 (paper scale) | Unknown | Unknown |
| **Failure modes addressed** | Reward desert, component imbalance (partially) | Unsafe behaviors, transfer gap | Local optima, premature convergence | All of the above + token efficiency | Reward desert, specification gaming |
| **Integration effort** | Low (plug-in) | Very high (deprecated sim) | High (port env wrappers) | N/A | Very high |

---

## Integration with IsaacLab DirectRLEnv

All three main candidates require or benefit from IsaacLab's `DirectRLEnv`. The interface contract is:

```python
class MyTaskEnv(DirectRLEnv):
    def _get_observations(self) -> dict:
        ...
    def _get_rewards(self) -> torch.Tensor:
        # Eureka replaces this with generated code
        ...
    def _get_dones(self) -> tuple[torch.Tensor, torch.Tensor]:
        ...
    def _reset_idx(self, env_ids: torch.Tensor) -> None:
        ...
```

IsaacLabEureka generates a `compute_rewards(self)` method that is injected as `_get_rewards`. The full env source file is passed to the LLM so it knows what `self.*` attributes are available. A `success_metric` field in the task config defines the training signal for reward comparison.

**Manager-based environments (`ManagerBasedRLEnv`) are not supported by IsaacLabEureka.** If Phase 7E needs to support manager-based tasks (which is the standard for IsaacLab's built-in task catalog — locomotion, manipulation, etc.), the team would need to either:
1. Rewrite those tasks in DirectRLEnv format, or
2. Extend IsaacLabEureka to support the reward manager injection pattern.

---

## Handling User Feedback: "It Keeps Dropping the Handle"

This is the core UX requirement for Phase 7E. How each approach handles it:

**Eureka / IsaacLabEureka:** The reflection prompt after each iteration can be pre-pended with user text. Implementation: expose a `user_note` field in the chat; inject it verbatim into the next reflection prompt. The LLM will attempt to address it but there is no guarantee — the system may ignore it if task_score is already high. Reliability: low without prompt engineering.

**DrEureka:** Same as Eureka at the reward stage. User feedback not structurally incorporated.

**REvolve:** User feedback maps directly to the mutation/crossover operator prompt. "It keeps dropping the handle" becomes: "The current reward function [code] produces a policy where the gripper releases prematurely. Modify the reward components to penalize early release and reward sustained contact duration." This is a first-class mechanism — the LLM is explicitly asked to encode the feedback into reward code changes.

**Recommended hybrid pattern (see below):** Use the IsaacLabEureka infrastructure but add a feedback injection layer that formats user natural language into a structured mutation instruction before the next Eureka iteration.

---

## Combining Approaches

The cleanest combination is:

```
Eureka (IsaacLabEureka) core
    + REvolve-style feedback formatting (user text → mutation instruction)
    + DrEureka-style DR generation (ported to IsaacLab, separate optional phase)
```

Concretely:
- **Reward loop:** Use IsaacLabEureka's existing infrastructure (DirectRLEnv injection, RSL-RL training, TensorBoard reflection). Add a `user_critique_to_mutation_prompt()` translator that takes chat text and converts it to a structured reward mutation instruction appended to the reflection.
- **Population (optional):** Replace the greedy-best selection with a small population (K=4 survivors from N=16 samples) to improve diversity without multiplying compute.
- **DR generation (optional, Phase 2):** Once IsaacLab environments are validated in simulation, add a DrEureka-style RAPP + DR generation phase to prepare for real robot transfer. This requires porting DrEureka's DR generator from IsaacGym to IsaacLab — significant but bounded engineering work.

This staged combination is viable on a single workstation for the reward loop. DR generation can be deferred to Phase 2 when physical robots are in scope.

---

## Minimum Viable Version (First Release)

The simplest thing that works for Phase 7E:

```
IsaacLabEureka + feedback injection
```

Specifically:

1. **Task scaffolding tool:** Chat command → generates `DirectRLEnv` subclass with `_get_observations()`, `_reset_idx()`, action space, and a placeholder `compute_rewards(self)`. Uses the existing `_generate_isaaclab_env_code` tool (after fixing the 5 bugs identified in rev2/verify_7A_bugs.md).

2. **Eureka launcher:** Calls IsaacLabEureka's main loop with the generated env, the user's natural language task description, and a configured LLM backend (OpenAI-compatible API — can be Nemotron local or any API key).

3. **Feedback injection:** After each Eureka iteration, the chat panel shows the current best reward function + training metrics. User can type critique. The critique is appended to the reflection prompt as: `"User observation: [text]. Prioritize addressing this in the next reward function."`

4. **Progress streaming:** Iteration counter, current best success_metric, estimated remaining time streamed to the chat panel via SSE (the existing FastAPI SSE infrastructure in `main.py` already supports this).

5. **Policy deployment hook:** After the final iteration, offer to load the trained policy via `isaacsim.robot.policy.examples` (already a gap item in the plan, Phase 7A-adjacent).

**What this deliberately excludes for V1:**
- Population evolution (REvolve-style) — adds compute, deferred to V2
- DR generation (DrEureka) — deferred until real robot target is defined
- Pairwise video comparison — too heavy for a chat-panel workflow
- Manager-based env support — DirectRLEnv only

---

## Computational Requirements Summary

| Scenario | Hardware | Wall-clock | Notes |
|---|---|---|---|
| Eureka MVP (K=16, N=5 iterations) | Single RTX 4090 (24 GB) | 8–12 hours | 1–2 parallel candidates at a time; rest queue |
| Eureka MVP (K=16, N=5 iterations) | Single A100 (40 GB) | 4–6 hours | 4 candidates in parallel |
| Eureka MVP (K=4, N=5 iterations) | Single RTX 4090 | 2–4 hours | Reduced K trades diversity for speed |
| Eureka + small population (K=16, survivors=4) | Single A100 | 5–8 hours | Adds 1 selection step |
| DrEureka full (reward + RAPP + DR) | Single A100 | 8–14 hours | Sequential stages; no parallelism |
| REvolve full (I=13, G=5) | 16× A100 | 24–50 hours | Research scale; impractical for V1 |
| REvolve reduced (I=3, G=3, K=4 each) | Single A100 | 6–10 hours | Estimated; no verified benchmark |

**Single-workstation verdict:** Eureka MVP is feasible. DrEureka without the deprecated IsaacGym dependency is feasible with porting effort. REvolve at research scale is not feasible on a single workstation — but a reduced population (3 islands × 4 individuals) is plausible.

---

## Recommendation

### Implement: Eureka / IsaacLabEureka + feedback injection layer

**Rationale:**
1. IsaacLabEureka is the only approach natively compatible with Isaac Lab's `DirectRLEnv` interface — zero simulator porting cost.
2. It is NVIDIA-maintained and aligned with the Isaac Sim 5.1/6.0 roadmap.
3. The greedy-best limitation is real but acceptable for V1: the feedback injection layer converts user critique to structured mutation instructions, compensating for the lack of population diversity.
4. It runs on a single RTX 4090 at reduced K (4–8 samples per iteration instead of 16), which is appropriate for a chat-assistant context where the user is waiting.
5. The existing FastAPI service (SSE streaming, multi-turn chat, tool executor) maps cleanly onto Eureka's iteration loop.

### Phase 2 addition: small population (REvolve-style)

Once the MVP is validated, increase sample size and retain top-N survivors (instead of greedy-best) to improve reward quality. This is a 20-line change to the IsaacLabEureka launcher loop and can be implemented without changing the environment or LLM interface.

### Phase 3 (if real robots targeted): DrEureka DR generation ported to IsaacLab

Port DrEureka's RAPP + DR generation pipeline from IsaacGym to IsaacLab `DirectRLEnv`. Estimated effort: 2–4 weeks. The LLM prompt and DR config output format can be reused directly; only the environment runner and physics perturbation API change.

### Do not adopt REvolve in its full form for this project

The human evaluation pipeline (10 evaluators, pairwise video comparison, Elo rating) is incompatible with a single-user chat assistant. The insight to borrow from REvolve is the feedback formatting pattern: structured natural language critique → explicit mutation instruction. Adopt the idea, not the full machinery.

### Do not wait for FORGE or MIRA

Both are not yet open-sourced and have no IsaacLab integration. FORGE (ICLR 2026 submission) may become available later in 2026 and should be re-evaluated if open-sourced.

---

## Action Items for Spec Update

1. Fix the 5 code bugs in `_generate_isaaclab_env_code` (documented in `verify_7A_bugs.md`) before any Eureka integration work.
2. Confirm that all Phase 7E target tasks will be implemented as `DirectRLEnv` — if any are manager-based (e.g., from IsaacLab's built-in catalog), either port them or extend IsaacLabEureka.
3. Add a `user_critique_to_mutation_prompt()` function to the Eureka integration layer — the spec should explicitly describe this as the user feedback mechanism.
4. Set K=4–8 (not the paper's K=16) as the default for workstation runs; expose as a configurable parameter.
5. Make the Eureka training loop async (already possible with FastAPI BackgroundTasks) and stream iteration progress via the existing SSE endpoint.
6. Add note: only RSL-RL and RL-Games are supported — if skrl or CleanRL is used elsewhere in the project, a wrapper or library switch is needed.

---

## Sources

- [IsaacLabEureka GitHub (isaac-sim/IsaacLabEureka)](https://github.com/isaac-sim/IsaacLabEureka)
- [Eureka paper — arXiv 2310.12931](https://arxiv.org/abs/2310.12931)
- [Eureka project page](https://eureka-research.github.io/)
- [Eureka ICLR 2024 proceedings](https://openreview.net/forum?id=IEduRUO55F)
- [DrEureka GitHub (eureka-research/DrEureka)](https://github.com/eureka-research/DrEureka)
- [DrEureka paper — arXiv 2406.01967](https://arxiv.org/html/2406.01967v1)
- [DrEureka VentureBeat coverage](https://venturebeat.com/automation/nvidias-dreureka-outperforms-humans-in-training-robotics-systems)
- [REvolve GitHub (RishiHazra/Revolve)](https://github.com/RishiHazra/Revolve)
- [REvolve paper — arXiv 2406.01309](https://arxiv.org/abs/2406.01309)
- [REvolve ICLR 2025 proceedings](https://openreview.net/forum?id=cJPUpL8mOw)
- [FORGE — OpenReview Z6GStCfccl](https://openreview.net/forum?id=Z6GStCfccl)
- [MIRA dual-loop — MDPI Systems 2025](https://www.mdpi.com/2079-8954/13/12/1124)
- [Text2Reward](https://text-to-reward.github.io/)
- [ReWiND — arXiv 2505.10911](https://arxiv.org/abs/2505.10911)
- [IsaacLab DirectRLEnv documentation](https://isaac-sim.github.io/IsaacLab/main/source/tutorials/03_envs/create_direct_rl_env.html)
- [IsaacLab Task Workflows](https://isaac-sim.github.io/IsaacLab/main/source/overview/core-concepts/task_workflows.html)
- [Isaac Gym deprecation notice](https://forums.developer.nvidia.com/t/isaac-gym-deprecation-transition-to-isaac-lab/322978)
- [IsaacLabEureka DeepWiki overview](https://deepwiki.com/isaac-sim/IsaacLabEureka/1-overview)
- [Leveraging LLMs for reward function design — arXiv 2511.19355](https://arxiv.org/html/2511.19355v1)
