"""Phase 26 — content-addressable storage for LayoutSpec history.

Every accepted LayoutSpec mutation produces a new revision hash. The
canvas SPA shows a timeline; user can roll back to any revision.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 26.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class LayoutSpecRevision:
    revision_hash: str
    parent_hash: Optional[str]
    spec_json: str
    timestamp: str
    author: str = "user"


class CASHistory:
    """In-memory CAS history. Production wires this to persistence.py."""

    def __init__(self) -> None:
        self._revisions: Dict[str, LayoutSpecRevision] = {}
        self._session_head: Dict[str, str] = {}  # session_id → latest hash

    def commit(self, session_id: str, spec: Any, author: str = "user") -> str:
        canonical = json.dumps(spec if isinstance(spec, dict) else {
            "objects": getattr(spec, "objects", []),
            "intent": getattr(spec, "intent", None),
        }, sort_keys=True, default=str)
        rev_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        parent = self._session_head.get(session_id)
        rev = LayoutSpecRevision(
            revision_hash=rev_hash,
            parent_hash=parent,
            spec_json=canonical,
            timestamp=datetime.now(timezone.utc).isoformat(),
            author=author,
        )
        self._revisions[rev_hash] = rev
        self._session_head[session_id] = rev_hash
        return rev_hash

    def history(self, session_id: str) -> List[LayoutSpecRevision]:
        out: List[LayoutSpecRevision] = []
        cur = self._session_head.get(session_id)
        while cur is not None:
            rev = self._revisions.get(cur)
            if rev is None:
                break
            out.append(rev)
            cur = rev.parent_hash
        return out

    def rollback(self, session_id: str, revision_hash: str) -> bool:
        if revision_hash not in self._revisions:
            return False
        self._session_head[session_id] = revision_hash
        return True


_HISTORY: Optional[CASHistory] = None


def get_history() -> CASHistory:
    global _HISTORY
    if _HISTORY is None:
        _HISTORY = CASHistory()
    return _HISTORY
