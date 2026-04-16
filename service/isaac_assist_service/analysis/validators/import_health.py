"""
ImportHealthValidator — detect broken USD references, missing assets,
unresolved payloads, and orphan Xform prims.

Runs purely on serialized stage_data JSON (no Kit dependency).
"""
from typing import List, Dict, Any
import uuid
import os

from .base import ValidationRule
from ..models import ValidationFinding, FixSuggestion, ProposedChange


class ImportHealthValidator(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "import.health"
        self.pack = "import_health"
        self.severity = "error"
        self.name = "Import health check"
        self.description = (
            "Detects broken USD references, missing asset files, "
            "unresolved payloads, and orphan Xform prims."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        prims = stage_data.get("prims", [])
        prim_paths = {p.get("path") for p in prims}

        for prim in prims:
            path = prim.get("path", "")
            prim_type = prim.get("type", "")
            refs = prim.get("references", [])
            payloads = prim.get("payloads", [])
            children = prim.get("children", [])
            has_geometry = prim.get("has_geometry", False)

            # --- Broken references ---
            for ref in refs:
                asset_path = ref if isinstance(ref, str) else ref.get("asset_path", "")
                if not asset_path:
                    continue
                # Skip Nucleus/network paths — can't validate locally
                if asset_path.startswith(("omniverse://", "http://", "https://")):
                    continue
                # Resolve relative paths against stage root
                stage_root = stage_data.get("stage_root_dir", "")
                if stage_root and not os.path.isabs(asset_path):
                    asset_path = os.path.join(stage_root, asset_path)
                if not os.path.exists(asset_path):
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="import.broken_reference",
                        pack=self.pack,
                        severity="error",
                        prim_path=path,
                        message=f"Broken asset reference: file not found.",
                        detail=(
                            f"Prim '{path}' references '{ref}' but the file "
                            f"does not exist locally. This causes 'Accessed "
                            f"schema on invalid prim' errors at runtime."
                        ),
                        evidence={"missing_asset": str(ref)},
                        auto_fixable=False,
                    ))

            # --- Unresolved payloads ---
            for payload in payloads:
                payload_path = payload if isinstance(payload, str) else payload.get("asset_path", "")
                if not payload_path:
                    continue
                if payload_path.startswith(("omniverse://", "http://", "https://")):
                    continue
                stage_root = stage_data.get("stage_root_dir", "")
                if stage_root and not os.path.isabs(payload_path):
                    payload_path = os.path.join(stage_root, payload_path)
                if not os.path.exists(payload_path):
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="import.unresolved_payload",
                        pack=self.pack,
                        severity="error",
                        prim_path=path,
                        message=f"Unresolved payload: file not found.",
                        detail=(
                            f"Prim '{path}' has a payload pointing to "
                            f"'{payload}' which cannot be resolved."
                        ),
                        evidence={"missing_payload": str(payload)},
                        auto_fixable=False,
                    ))

            # --- Orphan Xform prims (no geometry, no children, no references) ---
            if prim_type == "Xform" and not refs and not payloads and not has_geometry:
                child_paths = [
                    c for c in prim_paths
                    if c.startswith(path + "/") and c.count("/") == path.count("/") + 1
                ]
                if not child_paths and not children:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="import.orphan_xform",
                        pack=self.pack,
                        severity="warning",
                        prim_path=path,
                        message="Orphan Xform — no geometry, children, or references.",
                        detail=(
                            f"Prim '{path}' is an Xform with no child prims, "
                            f"no geometry, and no references. It may be "
                            f"leftover from a failed import."
                        ),
                        evidence={"prim_type": prim_type},
                        auto_fixable=True,
                    ))

        return findings

    def auto_fixable(self) -> bool:
        return True

    def suggest_fix(self, finding: ValidationFinding):
        if finding.rule_id == "import.orphan_xform":
            return FixSuggestion(
                description=f"Delete orphan Xform '{finding.prim_path}'",
                confidence=0.8,
                changes=[ProposedChange(
                    target_type="prim",
                    target_path=finding.prim_path,
                    action="delete",
                )],
            )
        return None
