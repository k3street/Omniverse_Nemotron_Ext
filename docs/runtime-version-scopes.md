# Runtime Version Scopes

Isaac Assist supports two compatibility lanes. Keep artifacts in the lane where
they were actually verified; do not assume Isaac Sim 5.1 code works in Isaac Sim
6.0 or Isaac Lab 3.x.

The machine-readable policy lives in
`workspace/knowledge/runtime_scopes.json`. The Python runtime source of truth is
`service/isaac_assist_service/runtime_profiles.py`.

## Scope Matrix

| Scope | Isaac Sim 5.1 Lane | Isaac Sim 6.0 Lane |
|---|---|---|
| Runtime profile | `isaacsim-5.1` | `isaacsim-6.0` |
| Isaac Lab | 2.x | 3.x |
| Launcher | `./launch_isaac.sh --version 5.1` | `./launch_isaac.sh --version 6.0` |
| Extension folder | `exts/isaac_5.1` | `exts/isaac_6.0` |
| Code patterns | `workspace/knowledge/code_patterns_5.1.0.jsonl` | `workspace/knowledge/code_patterns_6.0.0.jsonl` |
| Indexed docs | `workspace/knowledge/knowledge_5.1.0.jsonl` | `workspace/knowledge/knowledge_6.0.0.jsonl` |
| Negative patterns | `workspace/knowledge/negative_patterns_5.1.0.jsonl` | `workspace/knowledge/negative_patterns_6.0.0.jsonl` |
| ROS2 OmniGraph node types | `isaacsim.ros2.bridge.*` | `isaacsim.ros2.nodes.*` |
| Template default | Unscoped legacy templates are allowed | Only explicit 6.0-tagged templates are allowed |
| QA default | Existing unscoped QA is 5.1-baseline | 6.0 QA must be tagged or copied into a 6.0-specific record |

## Artifact Rules

Templates:
Add at least one of these fields before a template may be used in the 6.0 lane:

```json
{
  "runtime_profiles": ["isaacsim-6.0"],
  "isaac_sim_versions": ["6.0.0"],
  "isaac_lab_versions": ["3.x"]
}
```

Known-good code patterns:
Save snippets to the matching `code_patterns_<version>.jsonl` file and include
`runtime_profiles`, `isaac_sim_versions`, and `isaac_lab_versions`. A successful
5.1 run is not 6.0 evidence.

QA docs:
New QA docs should include a short metadata block near the top:

```markdown
Runtime Scope: isaacsim-6.0
Isaac Sim: 6.0.0
Isaac Lab: 3.x
Evidence: live run, log, screenshot, or test output
```

Research reports:
Reports may compare multiple versions, but every recommendation should say
which runtime profile it applies to.

Launchers and harnesses:
Use explicit `--version` when reproducing QA evidence. Relying on auto-detect is
fine for daily use, but explicit version selection is better for known-good
evidence.

## Defaults

Unscoped legacy artifacts default to `isaacsim-5.1`. This preserves the existing
5.1 corpus while preventing it from silently contaminating 6.0 suggestions.

The only safe cross-version code is conservative USD-level code that uses
`pxr`, `omni.usd`, and APIs verified in both runtimes. Even then, save the
artifact as `runtime_profiles: ["any"]` only after testing in both lanes.
