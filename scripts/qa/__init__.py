"""Phase 12 — Agent-driven QA campaign infrastructure.

This package orchestrates Claude Code subprocess sessions that role-play
personas against Isaac Assist, then judges the resulting transcripts.

Modules:
    build_session_prompt — assembles persona + rules + modifiers + task into one prompt
    launch_campaign      — orchestrates parallel sessions, saves transcripts
    judge_session        — runs LLM judge over a transcript, returns scores
    aggregate_results    — campaign-wide rollup of judge scores
"""

from __future__ import annotations

__all__ = [
    "build_session_prompt",
    "launch_campaign",
    "judge_session",
    "aggregate_results",
]
