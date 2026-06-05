"""Phase 47 — add 5+ new patch_validator rules from production gap log.

Each new rule is a PatchValidatorRule subclass registered via the
Phase 11 framework. Examples from production:
  - usd_set_color_wrong_signature
  - omnigraph_compute_outputs_pattern
  - bad_isaac_lab_import

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 47.
"""
NEW_PRODUCTION_RULES_REGISTERED: list = [
    "usd_set_color_wrong_signature",
    "omnigraph_compute_outputs_pattern",
    "bad_isaac_lab_import",
    "missing_kit_app_lock",
    "stage_inside_callback",
]
