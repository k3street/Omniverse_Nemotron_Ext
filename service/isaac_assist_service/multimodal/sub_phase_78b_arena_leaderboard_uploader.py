"""Phase 78b — arena leaderboard uploader.

Uploads leaderboard entries to a remote HTTP endpoint with retry/backoff.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 78b.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import Leaderboard


PHASE_ID = "78b"
PHASE_TITLE = "arena leaderboard uploader"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 78b",
    }


class LeaderboardUploader:
    """Upload leaderboard entries to a remote HTTP endpoint.

    Retry policy:
    - 5xx: retry up to *max_retries* with exponential backoff
      (``backoff_base_s * 2 ** attempt``, 0-indexed).
    - 4xx: no retry, return immediately with status="error".
    - Network/other exceptions: retry up to *max_retries*.
    - After exhausting retries: status="error".

    Parameters
    ----------
    endpoint_url:
        Full URL to POST entries to.
    api_key:
        Optional bearer token.  When set, adds
        ``Authorization: Bearer {api_key}`` to every request.
    max_retries:
        Maximum number of *extra* attempts after the first failure
        (so total attempts = max_retries + 1 at most).
    backoff_base_s:
        Base sleep duration for exponential backoff.
    _sleep:
        Injectable sleep callable (default: ``time.sleep``).  Pass a
        no-op or mock in tests to avoid real delays.
    """

    def __init__(
        self,
        endpoint_url: str,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        backoff_base_s: float = 1.0,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.api_key = api_key
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._sleep = _sleep

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _do_request(self, body: bytes, headers: Dict[str, str]) -> tuple[int, Dict[str, Any]]:
        """Execute a single HTTP POST.  Returns (http_status, response_dict).

        Raises ``urllib.error.HTTPError`` for HTTP-level errors and
        ``urllib.error.URLError`` / other exceptions for network errors.
        """
        req = urllib.request.Request(
            self.endpoint_url,
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            status = resp.status
        try:
            response_body = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            response_body = {"raw": raw.decode("utf-8", errors="replace")}
        return status, response_body

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_entry(self, scenario_id: str, payload: dict) -> Dict[str, Any]:
        """POST *payload* tagged with *scenario_id* to the configured endpoint.

        Returns a dict::

            {
                "status":      "ok" | "error",
                "http_status": int,        # last HTTP status code, or 0
                "attempts":    int,        # total attempts made
                "response":    dict | None,
            }
        """
        body = json.dumps({"scenario_id": scenario_id, **payload}).encode("utf-8")
        headers = self._build_headers()

        last_http_status = 0
        last_response: Optional[Dict[str, Any]] = None

        for attempt in range(self.max_retries + 1):
            try:
                http_status, response_body = self._do_request(body, headers)
                last_http_status = http_status
                last_response = response_body
                return {
                    "status": "ok",
                    "http_status": http_status,
                    "attempts": attempt + 1,
                    "response": response_body,
                }
            except urllib.error.HTTPError as exc:
                last_http_status = exc.code
                try:
                    raw = exc.read()
                    last_response = json.loads(raw)
                except Exception:
                    last_response = None

                if 400 <= exc.code < 500:
                    # 4xx — do NOT retry
                    return {
                        "status": "error",
                        "http_status": last_http_status,
                        "attempts": attempt + 1,
                        "response": last_response,
                    }
                # 5xx — fall through to retry logic below
            except Exception:
                # Network error or other — will retry
                last_http_status = 0
                last_response = None

            # Not the last attempt — sleep with backoff before retrying
            if attempt < self.max_retries:
                self._sleep(self.backoff_base_s * (2 ** attempt))

        # Exhausted all retries
        return {
            "status": "error",
            "http_status": last_http_status,
            "attempts": self.max_retries + 1,
            "response": last_response,
        }

    def upload_leaderboard(
        self,
        leaderboard: Leaderboard,
        scenarios: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Upload every entry for each scenario in *leaderboard*.

        Parameters
        ----------
        leaderboard:
            The :class:`~isaaclab_arena_leaderboard.Leaderboard` to read.
        scenarios:
            Optional list of scenario IDs to restrict the upload to.
            When ``None``, all known scenarios are uploaded.

        Returns an aggregate report::

            {
                "total":    int,
                "ok":       int,
                "error":    int,
                "results":  list[dict],   # one per entry upload
            }
        """
        if scenarios is None:
            scenarios = leaderboard.list_scenarios()

        results: List[Dict[str, Any]] = []
        ok_count = 0
        error_count = 0

        for scenario_id in scenarios:
            entries = leaderboard.all_for_scenario(scenario_id)
            for entry in entries:
                result = self.upload_entry(scenario_id, entry)
                results.append(result)
                if result["status"] == "ok":
                    ok_count += 1
                else:
                    error_count += 1

        return {
            "total": len(results),
            "ok": ok_count,
            "error": error_count,
            "results": results,
        }
