# Phase 7H — IsaacAutomator Cloud Deployment: Assessment

**Agent:** Research 7H Cloud Deployment  
**Date:** 2026-04-15  
**Status:** Complete

## IsaacAutomator: Active (v4.0.0-rc4)

Supports: AWS, GCP, Azure, Alibaba. **OVHcloud NOT supported** (spec invents this).

## Critical: A100/H100 Not Supported by Isaac Sim

GPUs without RT cores are not supported. Must use T4/L4/A10G/L40S. Isaac Lab 3.0 "kit-less" Newton backend can run on A100/H100 for pure RL, but that's not "Isaac Sim."

## Security — Most Serious Concern

- `cloud_launch(script)` = remote code execution by design
- Runaway cost: forgotten instances at $2.50/hr per GPU
- Cloud credentials in `.env` accessible via `get_settings`

**Requirements before implementing:**
- Mandatory human approval for all `cloud_*` tools
- Cloud-provider spending caps
- Credentials in secrets manager, not `.env`
- `script` parameter restricted to allowlist

## Cost Data (April 2026)

| Instance | GPU | $/hr |
|---|---|---|
| AWS g5.2xlarge | A10G | ~$1.21 |
| AWS g6e.2xlarge | L40S | ~$2.50 |
| GCP g2-standard-8 | L4 | ~$1.35 |

## Sources
- [isaac-sim/IsaacAutomator](https://github.com/isaac-sim/IsaacAutomator)
- [Isaac Sim Requirements](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html)
