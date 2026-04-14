import json
import hashlib
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from service.isaac_assist_service.config import config

logger = logging.getLogger(__name__)

_LINE_REF_RE = re.compile(r'(?:File "[^"]*", line \d+(?:, in \w+)?|at line \d+|in file [^\s]+)')


def _error_signature(output: str) -> str:
    """Extract a normalized error signature for dedup."""
    for line in output.split("\n"):
        stripped = line.strip()
        if any(kw in stripped for kw in ("Error", "Exception", "Traceback")):
            # Normalize: drop file paths and line numbers
            sig = _LINE_REF_RE.sub("", stripped).strip()
            return sig[:200]
    return output.strip()[:200]

class KnowledgeBase:
    """
    Long-term experiential memory. Stores instructional data 
    separated by the application version to build fine-tuning datasets.
    """
    def __init__(self, storage_dir: str = "workspace/knowledge"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # In-memory set of known error signatures to prevent duplicates
        self._known_errors: Dict[str, Set[str]] = {}  # version -> set of sigs
        
    def _get_file_path(self, version: str) -> Path:
        # Sanitize version string for file names
        clean_version = "".join(c for c in version if c.isalnum() or c in "._-").strip()
        if not clean_version:
            clean_version = "default_version"
        return self.storage_dir / f"knowledge_{clean_version}.jsonl"

    def add_entry(self, version: str, instruction: str, response: str, source: str = "audit"):
        """Appends a new QA pair / instruction pair to the version-specific KB.
        
        Local learning sources (auto_error_learning, auto_success_learning) always write.
        User-contributed data (approved_patch) respects the contribute_data opt-in.
        """
        if source == "approved_patch" and not config.contribute_data:
            logger.info("Data contribution opt-in is disabled. Skipping approved_patch KB entry.")
            return False
            
        file_path = self._get_file_path(version)
        entry = {
            "instruction": instruction,
            "response": response,
            "source": source
        }
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            return True
        except Exception as e:
            logger.error(f"Failed to append to knowledge base {version}: {e}")
            return False

    def get_entries(self, version: str) -> List[Dict[str, str]]:
        """Retrieves all stored context for a given version."""
        file_path = self._get_file_path(version)
        entries = []
        if not file_path.exists():
            return entries
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))
            return entries
        except Exception as e:
            logger.error(f"Failed to read knowledge base {version}: {e}")
            return []

    def get_supported_versions(self) -> List[str]:
        """Lists all versions we currently have knowledge for."""
        versions = []
        if not self.storage_dir.exists():
            return versions
            
        for file in self.storage_dir.glob("knowledge_*.jsonl"):
            # Strip knowledge_ prefix and .jsonl suffix
            ver = file.name[10:-6]
            versions.append(ver)
        return versions

    # ── Deduplication ────────────────────────────────────────────────────

    def _load_error_sigs(self, version: str) -> Set[str]:
        """Load known error signatures for a version (lazy, once)."""
        if version in self._known_errors:
            return self._known_errors[version]

        sigs: Set[str] = set()
        for entry in self.get_entries(version):
            if entry.get("source") == "auto_error_learning":
                inst = entry.get("instruction", "")
                # Extract the Error: line from the instruction
                for line in inst.split("\n"):
                    if line.startswith("Error:"):
                        sigs.add(_error_signature(line[6:]))
                        break
        self._known_errors[version] = sigs
        return sigs

    def is_known_error(self, version: str, error_output: str) -> bool:
        """Check if this error pattern was already recorded."""
        sig = _error_signature(error_output)
        return sig in self._load_error_sigs(version)

    def add_error(self, version: str, instruction: str, response: str,
                  error_output: str) -> bool:
        """Add an error learning entry, skipping duplicates."""
        sig = _error_signature(error_output)
        sigs = self._load_error_sigs(version)
        if sig in sigs:
            logger.info(f"[knowledge] Skipping duplicate error: {sig[:80]}")
            return False
        sigs.add(sig)
        return self.add_entry(version, instruction, response,
                              source="auto_error_learning")

    # ── Error retrieval for LLM injection ────────────────────────────────

    def get_error_learnings(self, version: str, user_message: str,
                            limit: int = 5) -> List[Dict[str, str]]:
        """
        Retrieve error learnings relevant to a user message.
        Uses keyword overlap between the user message and the stored
        instruction/error text to find the most relevant warnings.
        """
        entries = self.get_entries(version)
        errors = [e for e in entries if e.get("source") == "auto_error_learning"]
        if not errors:
            return []

        msg_words = set(user_message.lower().split())
        scored = []
        seen_sigs: Set[str] = set()  # dedup within results
        for e in errors:
            inst = e.get("instruction", "")
            sig = _error_signature(inst)
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)

            inst_words = set(inst.lower().split())
            overlap = len(msg_words & inst_words)
            if overlap > 0:
                scored.append((overlap, e))

        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    def format_error_learnings(self, learnings: List[Dict[str, str]]) -> str:
        """Format error learnings for LLM system prompt injection."""
        if not learnings:
            return ""
        lines = [
            "--- KNOWN ERRORS (from previous executions — AVOID these patterns) ---"
        ]
        for i, e in enumerate(learnings, 1):
            inst = e.get("instruction", "")
            resp = e.get("response", "")
            lines.append(f"\n[ERROR {i}]")
            lines.append(inst[:600])
            lines.append(f"FIX: {resp}")
        return "\n".join(lines)

    # ── Success learning ─────────────────────────────────────────────────

    def _success_sig(self, user_message: str) -> str:
        """Normalize a user message into a dedup key for successes."""
        return user_message.strip().lower()[:200]

    def add_success(self, version: str, instruction: str, response: str,
                    code: str = "") -> bool:
        """Add a successful execution pattern, skipping near-duplicates."""
        sig = self._success_sig(instruction)
        key = f"{version}:success"
        if key not in self._known_errors:
            # Lazy-load existing success sigs
            sigs: Set[str] = set()
            for entry in self.get_entries(version):
                if entry.get("source") == "auto_success_learning":
                    sigs.add(self._success_sig(entry.get("instruction", "")))
            self._known_errors[key] = sigs
        if sig in self._known_errors[key]:
            logger.info(f"[knowledge] Skipping duplicate success: {sig[:60]}")
            return False
        self._known_errors[key].add(sig)
        return self.add_entry(version, instruction, response,
                              source="auto_success_learning")

    def get_success_learnings(self, version: str, user_message: str,
                              limit: int = 3) -> List[Dict[str, str]]:
        """
        Retrieve successful code patterns relevant to a user message.
        Returns the most relevant proven-working examples.
        """
        entries = self.get_entries(version)
        successes = [e for e in entries if e.get("source") == "auto_success_learning"]
        if not successes:
            return []

        msg_words = set(user_message.lower().split())
        scored = []
        for e in successes:
            inst = e.get("instruction", "")
            inst_words = set(inst.lower().split())
            overlap = len(msg_words & inst_words)
            if overlap > 0:
                scored.append((overlap, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    def format_success_learnings(self, learnings: List[Dict[str, str]]) -> str:
        """Format success learnings for LLM system prompt injection."""
        if not learnings:
            return ""
        lines = [
            "--- PROVEN WORKING PATTERNS (from successful executions — PREFER these) ---"
        ]
        for i, e in enumerate(learnings, 1):
            inst = e.get("instruction", "")
            resp = e.get("response", "")
            lines.append(f"\n[WORKING EXAMPLE {i}] User asked: {inst[:200]}")
            lines.append(resp[:800])
        return "\n".join(lines)

    # ── Compaction ───────────────────────────────────────────────────────

    def compact(self, version: str, max_errors: int = 20,
                max_successes: int = 30, max_other: int = 50) -> Dict[str, int]:
        """
        Compact the knowledge file for a version by:
        1. Deduplicating error entries by signature
        2. Deduplicating success entries by user message
        3. Keeping only the most recent N entries per source type
        4. Rewriting the file atomically

        Returns counts of entries before and after.
        """
        entries = self.get_entries(version)
        if not entries:
            return {"before": 0, "after": 0}

        before = len(entries)
        kept: List[Dict] = []

        # Separate by source
        errors = []
        successes = []
        other = []
        for e in entries:
            src = e.get("source", "")
            if src == "auto_error_learning":
                errors.append(e)
            elif src == "auto_success_learning":
                successes.append(e)
            else:
                other.append(e)

        # Dedup errors by signature
        seen_err: Set[str] = set()
        for e in reversed(errors):  # keep most recent
            inst = e.get("instruction", "")
            sig = _error_signature(inst)
            if sig not in seen_err:
                seen_err.add(sig)
                kept.append(e)
            if len([x for x in kept if x.get("source") == "auto_error_learning"]) >= max_errors:
                break

        # Dedup successes by user message
        seen_suc: Set[str] = set()
        for e in reversed(successes):  # keep most recent
            sig = self._success_sig(e.get("instruction", ""))
            if sig not in seen_suc:
                seen_suc.add(sig)
                kept.append(e)
            if len([x for x in kept if x.get("source") == "auto_success_learning"]) >= max_successes:
                break

        # Keep most recent other entries
        kept.extend(other[-max_other:])

        # Rewrite atomically
        file_path = self._get_file_path(version)
        tmp = file_path.with_suffix(".jsonl.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for e in kept:
                    f.write(json.dumps(e) + "\n")
            tmp.replace(file_path)
        except Exception as exc:
            logger.error(f"Compaction failed for {version}: {exc}")
            if tmp.exists():
                tmp.unlink()
            return {"before": before, "after": before}

        # Reset caches
        self._known_errors.pop(version, None)
        self._known_errors.pop(f"{version}:success", None)

        after = len(kept)
        logger.info(f"[knowledge] Compacted v{version}: {before} → {after} entries")
        return {"before": before, "after": after}
