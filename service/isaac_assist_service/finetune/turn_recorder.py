"""
turn_recorder.py
-----------------
Captures every chat turn as a structured training record for fine-tuning.

Records are stored as daily JSONL files under workspace/finetune_data/sessions/.
Each line is a self-contained JSON object with user input, context, intent,
tool calls, assistant output, and optional human feedback.

The recorder also handles:
  - Redaction of API keys, bearer tokens, and external file paths
  - Feedback linking (approve / reject / correct a previous turn)
  - Export to provider-specific fine-tuning formats (OpenAI, Anthropic, Ollama, Alpaca)
  - Aggregate quality statistics
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Regex patterns for sensitive data that must be redacted
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),          # OpenAI API keys
    re.compile(r"\bAIza[A-Za-z0-9_-]{35,}\b"),        # Google API keys
    re.compile(r"\bBearer\s+[A-Za-z0-9_\-.]+", re.I), # Bearer tokens
    re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),           # GitHub PATs
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),       # GitLab PATs
    re.compile(r"\bxox[bpoas]-[A-Za-z0-9-]+\b"),       # Slack tokens
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),               # AWS access key IDs
    re.compile(r"\b[A-Za-z0-9/+=]{40}\b"),             # AWS secret keys (40 char base64)
]

# Matches absolute paths that are NOT under workspace/
_EXTERNAL_PATH_RE = re.compile(r"(?<!\w)(/(?:home|usr|etc|var|tmp|opt|root|mnt)[/][^\s\"']+)")

SYSTEM_PROMPT = (
    "You are Isaac Assist, an AI agent by 10Things, Inc. with full control over "
    "NVIDIA Isaac Sim. You can create and modify USD prims, apply physics and materials, "
    "build OmniGraph action graphs, attach sensors, control the simulation, import robots, "
    "generate synthetic data, and debug console errors."
)


class TurnRecorder:
    """Records chat turns as structured JSONL for fine-tuning pipelines."""

    def __init__(self, output_dir: str = "workspace/finetune_data/sessions"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Core recording ──────────────────────────────────────────────────────

    def record_turn(
        self,
        session_id: str,
        turn_id: int,
        user_message: str,
        context: Dict[str, Any],
        intent: str,
        tool_calls: List[Dict[str, Any]],
        assistant_message: str,
        feedback: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Append a turn record to today's JSONL file.

        Returns the path to the file the record was written to.
        """
        record = {
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "input": {
                "user_message": user_message,
                "selected_prim": context.get("selected_prim"),
                "stage_context": context.get("stage_summary"),
            },
            "intent": intent,
            "output": {
                "tool_calls": tool_calls,
                "assistant_message": assistant_message,
            },
            "feedback": feedback,
        }
        record = self._redact(record)

        filepath = self.output_dir / f"{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

        logger.debug(f"[TurnRecorder] Recorded turn {session_id}/{turn_id} -> {filepath}")
        return filepath

    # ── Feedback linking ────────────────────────────────────────────────────

    def record_feedback(
        self,
        session_id: str,
        turn_id: int,
        approved: bool,
        edited: bool = False,
        correction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Link feedback to a previously recorded turn.

        Scans all JSONL files for the matching (session_id, turn_id) record
        and appends an updated copy with the feedback attached.
        """
        target = self._find_turn(session_id, turn_id)
        if target is None:
            return {
                "status": "not_found",
                "message": f"Turn {session_id}/{turn_id} not found in recorded data.",
            }

        record, filepath = target
        record["feedback"] = {
            "approved": approved,
            "edited": edited,
            "correction": correction,
            "feedback_timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Append the updated record (consumers should use the latest entry per turn)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

        return {
            "status": "linked",
            "session_id": session_id,
            "turn_id": turn_id,
            "approved": approved,
            "edited": edited,
        }

    # ── Statistics ──────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return data quality stats from all recorded session files."""
        records = self._load_all_records()

        if not records:
            return {
                "total_turns": 0,
                "approval_rate": 0.0,
                "error_rate": 0.0,
                "tool_distribution": {},
                "date_range": {"earliest": None, "latest": None},
                "rejection_correction_pairs": 0,
            }

        # De-duplicate: keep the latest entry per (session_id, turn_id)
        latest: Dict[str, Dict] = {}
        for r in records:
            key = f"{r.get('session_id')}:{r.get('turn_id')}"
            latest[key] = r

        unique = list(latest.values())
        total = len(unique)

        # Feedback stats
        with_feedback = [r for r in unique if r.get("feedback")]
        approved_count = sum(
            1 for r in with_feedback if r["feedback"].get("approved")
        )
        feedback_count = len(with_feedback)

        # Error rate (tool calls that returned type=error)
        error_count = 0
        tool_counter: Counter = Counter()
        for r in unique:
            for tc in r.get("output", {}).get("tool_calls", []):
                tool_name = tc.get("tool", "unknown")
                tool_counter[tool_name] += 1
                if tc.get("result", {}).get("type") == "error":
                    error_count += 1

        total_tool_calls = sum(tool_counter.values())

        # Date range
        timestamps = [r.get("timestamp", "") for r in unique if r.get("timestamp")]
        timestamps.sort()

        # Rejection+correction pairs
        rejection_corrections = sum(
            1
            for r in with_feedback
            if not r["feedback"].get("approved") and r["feedback"].get("correction")
        )

        return {
            "total_turns": total,
            "approval_rate": (
                round(approved_count / feedback_count, 4)
                if feedback_count
                else 0.0
            ),
            "error_rate": (
                round(error_count / total_tool_calls, 4)
                if total_tool_calls
                else 0.0
            ),
            "tool_distribution": dict(tool_counter.most_common()),
            "date_range": {
                "earliest": timestamps[0] if timestamps else None,
                "latest": timestamps[-1] if timestamps else None,
            },
            "rejection_correction_pairs": rejection_corrections,
        }

    # ── Export ───────────────────────────────────────────────────────────────

    def export(
        self,
        fmt: str,
        min_quality: str = "approved_successful",
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Export recorded turns to a provider-specific fine-tuning format.

        Args:
            fmt: Target format — "openai", "anthropic", "ollama", "alpaca".
            min_quality: Filter level — "all", "approved", "approved_successful".
            output_path: Optional explicit output path. Defaults to
                workspace/finetune_data/exports/<fmt>_<date>.jsonl.

        Returns:
            Dict with status, path, record_count.
        """
        records = self._load_deduplicated()
        records = self._filter_quality(records, min_quality)

        if not records:
            return {
                "status": "empty",
                "message": f"No records match quality filter '{min_quality}'.",
                "record_count": 0,
            }

        export_dir = self.output_dir.parent / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)

        if output_path:
            out = Path(output_path)
        else:
            out = export_dir / f"{fmt}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jsonl"

        converter = {
            "openai": self._to_openai,
            "anthropic": self._to_anthropic,
            "ollama": self._to_ollama,
            "alpaca": self._to_alpaca,
        }.get(fmt)

        if converter is None:
            return {
                "status": "error",
                "message": f"Unknown format '{fmt}'. Supported: openai, anthropic, ollama, alpaca.",
                "record_count": 0,
            }

        out.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with open(out, "w", encoding="utf-8") as f:
            for r in records:
                converted = converter(r)
                if converted:
                    f.write(json.dumps(converted, default=str) + "\n")
                    count += 1

        return {
            "status": "success",
            "path": str(out),
            "record_count": count,
            "format": fmt,
            "quality_filter": min_quality,
        }

    # ── Redaction ────────────────────────────────────────────────────────────

    def _redact(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Strip API keys, tokens, and external paths from a record."""
        raw = json.dumps(record, default=str)
        for pattern in _SECRET_PATTERNS:
            raw = pattern.sub("<REDACTED_SECRET>", raw)
        raw = _EXTERNAL_PATH_RE.sub("<REDACTED_PATH>", raw)
        return json.loads(raw)

    def redact_file(
        self,
        input_path: str,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the redaction pipeline on an existing JSONL file.

        Args:
            input_path: Path to the input JSONL file.
            output_path: Optional output path. Defaults to <input>_redacted.jsonl.

        Returns:
            Dict with status, output_path, record_count.
        """
        inp = Path(input_path)
        if not inp.exists():
            return {"status": "error", "message": f"File not found: {input_path}"}

        if output_path:
            out = Path(output_path)
        else:
            out = inp.with_name(inp.stem + "_redacted" + inp.suffix)

        out.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with open(inp, "r", encoding="utf-8") as fin, open(out, "w", encoding="utf-8") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    redacted = self._redact(record)
                    fout.write(json.dumps(redacted, default=str) + "\n")
                    count += 1
                except json.JSONDecodeError:
                    logger.warning(f"[TurnRecorder] Skipping malformed line in {input_path}")

        return {
            "status": "success",
            "output_path": str(out),
            "record_count": count,
        }

    # ── Internal helpers ────────────────────────────────────────────────────

    def _find_turn(
        self, session_id: str, turn_id: int
    ) -> Optional[tuple]:
        """Find a recorded turn by session_id and turn_id. Returns (record, filepath)."""
        for jsonl_file in sorted(self.output_dir.glob("*.jsonl"), reverse=True):
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if (
                        record.get("session_id") == session_id
                        and record.get("turn_id") == turn_id
                    ):
                        return record, jsonl_file
                except json.JSONDecodeError:
                    continue
        return None

    def _load_all_records(self) -> List[Dict]:
        """Load every record from all JSONL files in output_dir."""
        records = []
        for jsonl_file in sorted(self.output_dir.glob("*.jsonl")):
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def _load_deduplicated(self) -> List[Dict]:
        """Load records, keeping only the latest entry per (session_id, turn_id)."""
        all_records = self._load_all_records()
        latest: Dict[str, Dict] = {}
        for r in all_records:
            key = f"{r.get('session_id')}:{r.get('turn_id')}"
            latest[key] = r
        return list(latest.values())

    def _filter_quality(self, records: List[Dict], min_quality: str) -> List[Dict]:
        """Filter records by quality level."""
        if min_quality == "all":
            return records
        if min_quality == "approved":
            return [
                r for r in records
                if (r.get("feedback") or {}).get("approved") is True
            ]
        if min_quality == "approved_successful":
            result = []
            for r in records:
                if not (r.get("feedback") or {}).get("approved"):
                    continue
                # Check none of the tool calls errored
                tool_calls = r.get("output", {}).get("tool_calls", [])
                has_error = any(
                    tc.get("result", {}).get("type") == "error" for tc in tool_calls
                )
                if not has_error:
                    result.append(r)
            return result
        return records

    # ── Format converters ───────────────────────────────────────────────────

    @staticmethod
    def _to_openai(record: Dict) -> Optional[Dict]:
        """Convert a turn record to OpenAI fine-tuning format."""
        user_msg = record.get("input", {}).get("user_message", "")
        assistant_msg = record.get("output", {}).get("assistant_message", "")
        if not user_msg or not assistant_msg:
            return None

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        tool_calls = record.get("output", {}).get("tool_calls", [])
        if tool_calls:
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc.get("tool", ""),
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            })
            for i, tc in enumerate(tool_calls):
                messages.append({
                    "role": "tool",
                    "tool_call_id": f"call_{i}",
                    "content": json.dumps(tc.get("result", {}), default=str),
                })

        messages.append({"role": "assistant", "content": assistant_msg})
        return {"messages": messages}

    @staticmethod
    def _to_anthropic(record: Dict) -> Optional[Dict]:
        """Convert a turn record to Anthropic tool_use fine-tuning format."""
        user_msg = record.get("input", {}).get("user_message", "")
        assistant_msg = record.get("output", {}).get("assistant_message", "")
        if not user_msg or not assistant_msg:
            return None

        messages = [
            {"role": "user", "content": user_msg},
        ]

        tool_calls = record.get("output", {}).get("tool_calls", [])
        if tool_calls:
            content_blocks = []
            for i, tc in enumerate(tool_calls):
                content_blocks.append({
                    "type": "tool_use",
                    "id": f"toolu_{i}",
                    "name": tc.get("tool", ""),
                    "input": tc.get("arguments", {}),
                })
            messages.append({"role": "assistant", "content": content_blocks})
            for i, tc in enumerate(tool_calls):
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"toolu_{i}",
                            "content": json.dumps(tc.get("result", {}), default=str),
                        }
                    ],
                })

        messages.append({"role": "assistant", "content": assistant_msg})
        return {"system": SYSTEM_PROMPT, "messages": messages}

    @staticmethod
    def _to_ollama(record: Dict) -> Optional[Dict]:
        """Convert to Unsloth/ShareGPT format (Ollama-compatible)."""
        user_msg = record.get("input", {}).get("user_message", "")
        assistant_msg = record.get("output", {}).get("assistant_message", "")
        if not user_msg or not assistant_msg:
            return None

        return {
            "conversations": [
                {"from": "system", "value": SYSTEM_PROMPT},
                {"from": "human", "value": user_msg},
                {"from": "gpt", "value": assistant_msg},
            ]
        }

    @staticmethod
    def _to_alpaca(record: Dict) -> Optional[Dict]:
        """Convert to Alpaca instruction-tuning format."""
        user_msg = record.get("input", {}).get("user_message", "")
        assistant_msg = record.get("output", {}).get("assistant_message", "")
        if not user_msg or not assistant_msg:
            return None

        context_parts = []
        selected = record.get("input", {}).get("selected_prim")
        if selected:
            context_parts.append(f"Selected prim: {selected}")
        stage_ctx = record.get("input", {}).get("stage_context")
        if stage_ctx:
            context_parts.append(f"Stage: {stage_ctx}")

        return {
            "instruction": user_msg,
            "input": "\n".join(context_parts),
            "output": assistant_msg,
            "system": SYSTEM_PROMPT,
        }
