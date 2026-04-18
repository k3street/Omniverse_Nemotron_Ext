# Phase 7H — IsaacAutomator Cloud Deployment

**Status:** Not implemented  
**Depends on:** Phase 7A (launch_training)  
**Research:** `research_reports/7H_cloud_deployment.md`

---

## Overview

Launch headless Isaac Sim/Lab instances on cloud GPUs for training, SDG, and evaluation at scale. Wraps `isaac-sim/IsaacAutomator` (v4.0.0-rc4, actively maintained).

---

## Critical Corrections

### GPU Compatibility
**Isaac Sim does NOT support A100/H100** (no RT cores). Supported cloud instances:

| Provider | Instance | GPU | Works? |
|----------|----------|-----|--------|
| AWS | g5.2xlarge | A10G (24 GB) | Yes |
| AWS | g6e.2xlarge | L40S (48 GB) | Yes |
| GCP | g2-standard-8 | L4 (24 GB) | Yes |
| Azure | NCasT4_v3 | T4 (16 GB) | Yes |
| **OVHcloud** | — | — | **NOT supported** |

**Exception:** Isaac Lab 3.0 "kit-less" Newton backend CAN run on A100/H100 for pure RL training (no rendering). This is a different entry point than headless Isaac Sim.

### Security — Mandatory Requirements

All `cloud_*` tools MUST require explicit human approval regardless of AUTO_APPROVE setting.

---

## Tools

### 7H.1 `cloud_launch(instance_type, num_gpus, isaac_version, script_template)`

**Correction:** `script` → `script_template`. Parameter restricted to an allowlist of known scripts, NOT freeform LLM-generated text.

**Implementation:** Shell out to IsaacAutomator's `./deploy-{aws,gcp,azure}` scripts, capture output, parse job ID.

**Prerequisites (user must configure before first use):**
- NGC API key for pulling `nvcr.io/nvidia/isaac-sim` container
- Cloud provider IAM credentials with least-privilege permissions
- GPU quota in the target region (often requires manual request)

**Terraform state:** Persist in cloud storage (S3 + DynamoDB / GCS bucket) with locking — NOT local `state/` directory.

### 7H.2 `cloud_status(job_id)`

Returns: `{status, gpu_utilization, estimated_remaining, cost_so_far}`

### 7H.3 `cloud_download_results(job_id, output_dir)`

Pull checkpoints / datasets from cloud instance to local.

### 7H.4 `cloud_teardown(job_id)`

Terminate instances. **Must always be called** — runaway cost risk otherwise.

**Implementation:** Add automatic cost alerts. If an instance runs > 24h without a status check, send a warning to the chat.

### 7H.5 Cost Estimator

**Realistic model:** `cost = $/hr × estimated_hours`. Do NOT promise sub-step cost estimation — model it as user-provided hours estimate × known instance pricing.

**Pricing data (keep updated):**
- AWS g5.2xlarge (A10G): ~$1.21/hr
- AWS g6e.2xlarge (L40S): ~$2.50/hr
- GCP g2-standard-8 (L4): ~$1.35/hr

---

## Governance

- `cloud_launch` and `cloud_teardown`: **always_require_approval = true** in policy_engine
- Cloud credentials: stored in secrets manager or dedicated credential file, NOT in `.env`
- `get_settings` must NEVER return cloud credentials

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Script template allowlist | L0 | Reject unknown scripts |
| Cost estimation | L0 | Math validation |
| Credential masking | L0 | Verify keys not in get_settings |
| IsaacAutomator CLI wrapper | L1 | Mock subprocess calls |
| Actual cloud launch | L3 | Requires cloud credentials + GPU quota |

## Known Limitations

- A100/H100 not supported for full Isaac Sim (only kit-less Newton)
- GPU quota is account-specific — may require manual requests
- Each provider needs separate IAM setup
- OVHcloud not supported
- Terraform state must be persistent with locking
