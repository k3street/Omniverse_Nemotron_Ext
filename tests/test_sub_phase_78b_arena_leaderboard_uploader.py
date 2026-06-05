"""Tests for Phase 78b — arena leaderboard uploader."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, call, patch

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_response(status: int, body: dict) -> MagicMock:
    """Return a mock that behaves like the context-manager result of urlopen."""
    raw = json.dumps(body).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.status = status
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _make_http_error(code: int, body: dict | None = None) -> urllib.error.HTTPError:
    raw = json.dumps(body or {}).encode("utf-8")
    return urllib.error.HTTPError(
        url="http://test",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,  # type: ignore[arg-type]
        fp=BytesIO(raw),
    )


def _noop_sleep(seconds: float) -> None:  # noqa: ARG001
    return


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPhase78bMetadata:
    def test_metadata(self):
        from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import (
            get_phase_metadata,
        )

        md = get_phase_metadata()
        assert md["phase"] == "78b"
        assert md["status"] == "landed"
        assert "spec_ref" in md


class TestUploadEntry200:
    def test_successful_200_returns_ok_attempts_1(self):
        from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import (
            LeaderboardUploader,
        )

        uploader = LeaderboardUploader(
            endpoint_url="http://example.com/api/leaderboard",
            _sleep=_noop_sleep,
        )
        mock_resp = _make_http_response(200, {"ok": True})

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = uploader.upload_entry("scenario_A", {"score": 42.0})

        assert result["status"] == "ok"
        assert result["http_status"] == 200
        assert result["attempts"] == 1
        assert result["response"] == {"ok": True}


class TestUpload5xxRetry:
    def test_500_retries_to_max_then_error(self):
        from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import (
            LeaderboardUploader,
        )

        max_retries = 2
        uploader = LeaderboardUploader(
            endpoint_url="http://example.com/api/leaderboard",
            max_retries=max_retries,
            backoff_base_s=0.01,
            _sleep=_noop_sleep,
        )

        with patch("urllib.request.urlopen", side_effect=_make_http_error(500)):
            result = uploader.upload_entry("scenario_B", {"score": 1.0})

        assert result["status"] == "error"
        assert result["http_status"] == 500
        # total attempts == max_retries + 1
        assert result["attempts"] == max_retries + 1


class TestUpload4xxNoRetry:
    def test_400_no_retry_attempts_1(self):
        from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import (
            LeaderboardUploader,
        )

        uploader = LeaderboardUploader(
            endpoint_url="http://example.com/api/leaderboard",
            max_retries=3,
            _sleep=_noop_sleep,
        )

        with patch("urllib.request.urlopen", side_effect=_make_http_error(400)):
            result = uploader.upload_entry("scenario_C", {"score": 0.5})

        assert result["status"] == "error"
        assert result["http_status"] == 400
        assert result["attempts"] == 1


class TestBackoffTiming:
    def test_exponential_backoff_sleep_calls(self):
        """Verify sleep is called with backoff_base_s * 2**attempt values."""
        from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import (
            LeaderboardUploader,
        )

        sleep_calls: list[float] = []

        def record_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        base = 1.5
        max_retries = 3
        uploader = LeaderboardUploader(
            endpoint_url="http://example.com/api/leaderboard",
            max_retries=max_retries,
            backoff_base_s=base,
            _sleep=record_sleep,
        )

        with patch("urllib.request.urlopen", side_effect=_make_http_error(503)):
            uploader.upload_entry("scenario_D", {})

        # Sleeps happen between attempts: after attempt 0, 1, 2 (not after last)
        assert len(sleep_calls) == max_retries
        for i, actual in enumerate(sleep_calls):
            expected = base * (2 ** i)
            assert actual == pytest.approx(expected), (
                f"sleep call {i}: expected {expected}, got {actual}"
            )


class TestApiKeyHeader:
    def test_api_key_sets_authorization_header(self):
        from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import (
            LeaderboardUploader,
        )

        api_key = "supersecret-token"
        uploader = LeaderboardUploader(
            endpoint_url="http://example.com/api/leaderboard",
            api_key=api_key,
            _sleep=_noop_sleep,
        )
        mock_resp = _make_http_response(200, {"created": True})
        captured_requests: list[urllib.request.Request] = []

        def fake_urlopen(req):  # noqa: ANN001
            captured_requests.append(req)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = uploader.upload_entry("scenario_E", {"score": 99.0})

        assert result["status"] == "ok"
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.get_header("Authorization") == f"Bearer {api_key}"
        assert req.get_header("Content-type") == "application/json"


class TestUploadLeaderboard:
    def test_upload_leaderboard_iterates_scenarios(self):
        """upload_leaderboard uploads each entry in each scenario."""
        from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import Leaderboard
        from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import (
            LeaderboardUploader,
        )

        import tempfile
        import os

        # Build a leaderboard with 2 scenarios, 3 entries total
        with tempfile.TemporaryDirectory() as tmpdir:
            lb_path = os.path.join(tmpdir, "test_lb.json")
            lb = Leaderboard(path=lb_path)
            lb.submit("scen_1", 1.0, "agent_a")
            lb.submit("scen_1", 2.0, "agent_b")
            lb.submit("scen_2", 3.0, "agent_c")

            uploader = LeaderboardUploader(
                endpoint_url="http://example.com/api/leaderboard",
                _sleep=_noop_sleep,
            )
            mock_resp = _make_http_response(200, {"ok": True})

            with patch("urllib.request.urlopen", return_value=mock_resp):
                report = uploader.upload_leaderboard(lb)

        assert report["total"] == 3
        assert report["ok"] == 3
        assert report["error"] == 0
        assert len(report["results"]) == 3
        assert all(r["status"] == "ok" for r in report["results"])

    def test_upload_leaderboard_scenario_filter(self):
        """upload_leaderboard respects the scenarios filter."""
        from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import Leaderboard
        from service.isaac_assist_service.multimodal.sub_phase_78b_arena_leaderboard_uploader import (
            LeaderboardUploader,
        )

        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            lb_path = os.path.join(tmpdir, "test_lb2.json")
            lb = Leaderboard(path=lb_path)
            lb.submit("scen_alpha", 5.0, "agent_x")
            lb.submit("scen_beta", 6.0, "agent_y")
            lb.submit("scen_beta", 7.0, "agent_z")

            uploader = LeaderboardUploader(
                endpoint_url="http://example.com/api/leaderboard",
                _sleep=_noop_sleep,
            )
            mock_resp = _make_http_response(201, {"inserted": True})

            with patch("urllib.request.urlopen", return_value=mock_resp):
                report = uploader.upload_leaderboard(lb, scenarios=["scen_beta"])

        # Only the 2 scen_beta entries should be uploaded
        assert report["total"] == 2
        assert report["ok"] == 2
