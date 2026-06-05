"""Long-term experiential knowledge base for Isaac Assist.

Stores versioned instruction/response JSONL pairs categorised as:
- ``auto_error_learning`` — failure patterns with normalized deduplication
- ``auto_success_learning`` — successful execution examples
- ``negative_patterns`` — structured failure records with root-cause and fix

Provides keyword-overlap retrieval so the chat orchestrator can inject
relevant past experience into the LLM system prompt, and a ``compact()``
method to prune the store to configurable size limits.
"""
import json
import hashlib
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone

from service.isaac_assist_service.config import config

logger = logging.getLogger(__name__)

_LINE_REF_RE = re.compile(r'(?:File "[^"]*", line \d+(?:, in \w+)?|at line \d+|in file [^\s]+)')


def _error_signature(output: str) -> str:
    """Extract a normalized error signature suitable for deduplication.

    Scans ``output`` line-by-line for the first line containing an error
    keyword, then strips file paths and line-number references so that
    the same logical error from different call sites produces the same key.

    Args:
        output (str): Raw error output or error description string.

    Returns:
        str: Normalised signature, at most 200 characters.  Falls back to the
        first 200 characters of the stripped input if no error line is found.
    """
    for line in output.split("\n"):
        stripped = line.strip()
        if any(kw in stripped for kw in ("Error", "Exception", "Traceback")):
            # Normalize: drop file paths and line numbers
            sig = _LINE_REF_RE.sub("", stripped).strip()
            return sig[:200]
    return output.strip()[:200]


def _keyword_set(text: str) -> Set[str]:
    """Extract a lowercased word set from text, filtering noise.

    Args:
        text (str): Arbitrary text to tokenize.

    Returns:
        Set[str]: Words longer than 2 characters, all lowercased.
    """
    return {w for w in text.lower().split() if len(w) > 2}

class KnowledgeBase:
    """Long-term experiential memory for Isaac Assist.

    Persists instruction/response pairs in per-version JSONL files under
    ``storage_dir``.  Each entry carries a ``source`` tag so callers can
    retrieve only errors, only successes, or negative patterns.

    Key methods:
    - :meth:`add_error` / :meth:`add_success` — write deduplicated entries.
    - :meth:`get_error_learnings` / :meth:`get_success_learnings` — retrieve
      keyword-matched examples for LLM injection.
    - :meth:`compact` — prune oversized stores atomically.
    - :meth:`add_negative_pattern` — record structured failure records with
      root-cause and applied fix.
    """
    def __init__(self, storage_dir: str = "workspace/knowledge"):
        """Initialise the knowledge base, creating the storage directory if absent.

        Args:
            storage_dir: Directory where version-scoped JSONL files are stored.
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # In-memory set of known error signatures to prevent duplicates
        self._known_errors: Dict[str, Set[str]] = {}  # version -> set of sigs
        
    def _get_file_path(self, version: str) -> Path:
        """Return the JSONL file path for a given version string.

        Sanitizes ``version`` to safe filename characters, falling back to
        ``"default_version"`` if the result would be empty.

        Args:
            version (str): Isaac Sim / extension version identifier.

        Returns:
            Path: Absolute path to ``knowledge_{version}.jsonl``.
        """
        clean_version = "".join(c for c in version if c.isalnum() or c in "._-").strip()
        if not clean_version:
            clean_version = "default_version"
        return self.storage_dir / f"knowledge_{clean_version}.jsonl"

    def add_entry(self, version: str, instruction: str, response: str, source: str = "audit"):
        """Append a new instruction/response pair to the version-specific JSONL store.

        Local learning sources (``auto_error_learning``, ``auto_success_learning``)
        always write.  User-contributed data (``approved_patch``) respects the
        ``contribute_data`` opt-in setting.

        Args:
            version (str): Isaac Sim version string, used to select the JSONL file.
            instruction (str): The instruction or context text for the entry.
            response (str): The corresponding response or fix text.
            source (str): Entry source tag.  One of ``"audit"``, ``"approved_patch"``,
                ``"auto_error_learning"``, or ``"auto_success_learning"``.

        Returns:
            bool: True if the entry was written, False if skipped or an error occurred.
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
        """Return all stored entries for a given version.

        Args:
            version (str): Isaac Sim version string.

        Returns:
            List[dict]: All parsed JSONL entries, or an empty list if the file does
            not exist or contains unreadable content.
        """
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
        """Return all version strings that have a knowledge file on disk.

        Returns:
            List[str]: Version identifiers derived from ``knowledge_*.jsonl``
            filenames in ``storage_dir``.
        """
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
        """Load and cache the set of known error signatures for a version.

        Result is memoised in ``_known_errors``.  Subsequent calls for the
        same version return the cached set without re-reading disk.

        Args:
            version (str): Isaac Sim version string.

        Returns:
            Set[str]: Normalized error signatures already recorded for this version.
        """
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
        """Return True if a matching error signature is already stored.

        Args:
            version (str): Isaac Sim version string.
            error_output (str): Raw error text to check.

        Returns:
            bool: True if the normalized signature matches a stored entry.
        """
        sig = _error_signature(error_output)
        return sig in self._load_error_sigs(version)

    def add_error(self, version: str, instruction: str, response: str,
                  error_output: str) -> bool:
        """Add an error learning entry, skipping exact duplicates.

        Args:
            version (str): Isaac Sim version string.
            instruction (str): Context/instruction text for the entry.
            response (str): The fix or corrective response text.
            error_output (str): Raw error output used to build the dedup signature.

        Returns:
            bool: True if the entry was written, False if a duplicate was detected.
        """
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
        """Retrieve error learnings most relevant to a user message.

        Ranks stored ``auto_error_learning`` entries by word-overlap with
        ``user_message`` and returns the top ``limit`` unique entries.

        Args:
            version (str): Isaac Sim version string.
            user_message (str): The current user query to match against.
            limit (int): Maximum number of entries to return (default 5).

        Returns:
            List[dict]: Matching entries sorted by descending overlap score,
            deduplicated by error signature.
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
        """Normalize a user message into a deduplication key for success entries.

        Args:
            user_message (str): Raw user message text.

        Returns:
            str: Lowercased, stripped, max-200-char key.
        """
        return user_message.strip().lower()[:200]

    def add_success(self, version: str, instruction: str, response: str,
                    code: str = "") -> bool:
        """Add a successful execution pattern, skipping near-duplicates.

        Uses a normalized form of ``instruction`` as the dedup key so that
        identical user requests do not accumulate redundant entries.

        Args:
            version (str): Isaac Sim version string.
            instruction (str): The user instruction or context text.
            response (str): The successful response or code pattern.
            code (str): Optional raw Python code for reference (not indexed).

        Returns:
            bool: True if the entry was written, False if a near-duplicate exists.
        """
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
        """Retrieve successful code patterns most relevant to a user message.

        Args:
            version (str): Isaac Sim version string.
            user_message (str): The current user query to match against.
            limit (int): Maximum number of entries to return (default 3).

        Returns:
            List[dict]: Matching ``auto_success_learning`` entries sorted by
            descending keyword-overlap score.
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

    # ── Error pattern query (real implementation) ────────────────────────

    def query_by_error_pattern(self, version: str, error_text: str,
                               limit: int = 3) -> List[Dict[str, str]]:
        """Search error learnings and negative patterns by keyword overlap.

        Combines results from ``auto_error_learning`` entries in the main KB
        and the separate negative-pattern store.  Negative patterns get a +1
        score bonus to prefer their structured root-cause data.

        Args:
            version (str): Isaac Sim version string.
            error_text (str): Error output or description to search against.
            limit (int): Maximum number of results to return (default 3).

        Returns:
            List[dict]: Matched entries (minimum 2-keyword overlap) sorted by
            descending score.  Negative pattern entries are reshaped to match
            the standard instruction/response/source dict format.
        """
        results: List[tuple] = []   # (score, entry)
        query_words = _keyword_set(error_text)
        if not query_words:
            return []

        # 1. Search error learnings in the main KB
        for entry in self.get_entries(version):
            if entry.get("source") != "auto_error_learning":
                continue
            inst = entry.get("instruction", "")
            entry_words = _keyword_set(inst)
            overlap = len(query_words & entry_words)
            if overlap >= 2:
                results.append((overlap, entry))

        # 2. Search negative patterns store
        for neg in self._load_negative_patterns(version):
            sig_words = _keyword_set(neg.get("error_signature", ""))
            cause_words = _keyword_set(neg.get("root_cause", ""))
            combined = sig_words | cause_words
            overlap = len(query_words & combined)
            if overlap >= 2:
                # Reshape to match the entry format callers expect
                results.append((overlap + 1, {  # +1 to prefer negatives
                    "instruction": (
                        f"Error: {neg.get('error_signature', '')}\n"
                        f"Root cause: {neg.get('root_cause', '')}\n"
                        f"Failing code:\n```python\n{neg.get('failing_code', '')[:500]}\n```"
                    ),
                    "response": f"Fix applied: {neg.get('fix_applied', 'Unknown')}",
                    "source": "negative_pattern",
                }))

        results.sort(key=lambda x: -x[0])
        return [entry for _, entry in results[:limit]]

    # ── Negative pattern store ───────────────────────────────────────────

    def _negative_patterns_path(self, version: str) -> Path:
        """Return the JSONL file path for negative patterns for a version.

        Args:
            version (str): Isaac Sim / extension version identifier.

        Returns:
            Path: Absolute path to ``negative_patterns_{version}.jsonl``.
        """
        clean = "".join(c for c in version if c.isalnum() or c in "._-").strip()
        return self.storage_dir / f"negative_patterns_{clean or 'default'}.jsonl"

    def _load_negative_patterns(self, version: str) -> List[Dict[str, Any]]:
        """Load negative patterns from the versioned JSONL file."""
        path = self._negative_patterns_path(version)
        if not path.exists():
            return []
        patterns = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        patterns.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to read negative patterns: {e}")
        return patterns

    def add_negative_pattern(
        self,
        version: str,
        error_signature: str,
        failing_code: str,
        root_cause: str,
        fix_applied: str,
    ) -> bool:
        """Record a failure pattern so the system avoids repeating it.

        Deduplicates by normalized error signature within a 24-hour window —
        the same logical error is only stored once per day.

        Args:
            version (str): Isaac Sim version string.
            error_signature (str): Short error description or exception line.
            failing_code (str): The Python snippet that produced the error.
            root_cause (str): Human-readable explanation of why it failed.
            fix_applied (str): Description of the corrective action taken.

        Returns:
            bool: True if the entry was written, False if a recent duplicate exists.
        """
        sig = _error_signature(error_signature)
        existing = self._load_negative_patterns(version)

        # Dedup: same signature within 24h
        now = datetime.now(timezone.utc)
        for p in existing:
            if _error_signature(p.get("error_signature", "")) == sig:
                ts = p.get("timestamp", "")
                try:
                    old_dt = datetime.fromisoformat(ts)
                    if (now - old_dt).total_seconds() < 86400:
                        logger.info(f"[knowledge] Skipping dup negative pattern: {sig[:60]}")
                        return False
                except (ValueError, TypeError):
                    pass  # malformed timestamp, allow new entry

        entry = {
            "error_signature": error_signature[:500],
            "failing_code": failing_code[:2000],
            "root_cause": root_cause[:500],
            "fix_applied": fix_applied[:500],
            "timestamp": now.isoformat(),
        }
        path = self._negative_patterns_path(version)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            logger.info(f"[knowledge] Stored negative pattern: {sig[:60]}")
            return True
        except Exception as e:
            logger.error(f"Failed to write negative pattern: {e}")
            return False

    def get_negative_patterns(self, version: str, user_message: str,
                              limit: int = 3) -> List[Dict[str, Any]]:
        """Retrieve negative patterns relevant to a user message."""
        patterns = self._load_negative_patterns(version)
        if not patterns:
            return []
        msg_words = _keyword_set(user_message)
        scored = []
        for p in patterns:
            p_words = _keyword_set(
                p.get("error_signature", "") + " " + p.get("root_cause", "")
            )
            overlap = len(msg_words & p_words)
            if overlap > 0:
                scored.append((overlap, p))
        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:limit]]

    def format_negative_patterns(self, patterns: List[Dict[str, Any]]) -> str:
        """Format negative patterns for LLM system prompt injection."""
        if not patterns:
            return ""
        lines = [
            "--- KNOWN FAILURE PATTERNS (DO NOT repeat these) ---"
        ]
        for i, p in enumerate(patterns, 1):
            lines.append(f"\n[FAILURE {i}]")
            lines.append(f"Error: {p.get('error_signature', '')[:200]}")
            lines.append(f"Root cause: {p.get('root_cause', '')[:200]}")
            lines.append(f"Fix: {p.get('fix_applied', '')[:200]}")
        return "\n".join(lines)

    # ── Plan outcome capture ─────────────────────────────────────────────

    def capture_plan_outcome(
        self,
        version: str,
        user_message: str,
        plan_steps: List[Dict[str, Any]],
        success: bool,
        error_output: str = "",
        code: str = "",
    ) -> bool:
        """Persist the outcome of an executed plan as a learning entry.

        On success stores via :meth:`add_success`.  On failure stores via
        :meth:`add_negative_pattern` with the error output and a generic
        root-cause note.

        Args:
            version (str): Isaac Sim version string.
            user_message (str): Original user request.
            plan_steps (list): The ordered list of plan step dicts.
            success (bool): Whether the plan executed without errors.
            error_output (str): Raw error text, used when ``success`` is False.
            code (str): Python code that was executed, for reference.

        Returns:
            bool: True if a new entry was written, False if skipped as a duplicate.
        """
        if success:
            return self.add_success(
                version, user_message,
                f"Plan executed successfully with {len(plan_steps)} steps:\n"
                f"```python\n{code[:1500]}\n```",
                code=code,
            )
        else:
            return self.add_negative_pattern(
                version,
                error_signature=error_output[:500],
                failing_code=code[:2000],
                root_cause=f"Plan for '{user_message[:100]}' failed during execution",
                fix_applied="Rolled back via snapshot. Needs alternative approach.",
            )
