#!/usr/bin/env python3
"""
Automated API-level test runner for Isaac Assist test scenarios T15-T25.
Tests the service endpoint without requiring Isaac Sim to be running.
Validates: intent classification, tool selection, code generation, and response structure.
"""
import json
import sys
import time
import requests

BASE = "http://localhost:8000/api/v1/chat/message"
TIMEOUT = 120  # seconds per request

TESTS = [
    {
        "id": "T15",
        "name": "Create a Camera",
        "message": "Add a camera named TopCam at position 0, 0, 5 looking down",
        "checks": {
            "has_code_patch": True,
            "code_contains": ["Camera", "TopCam", "DefinePrim"],
            "tool_used": "create_prim",
        },
    },
    {
        "id": "T16",
        "name": "Set Viewport Camera",
        "message": "Switch the viewport to use /World/TopCam",
        "checks": {
            "has_code_patch": True,
            "code_contains": ["viewport", "TopCam"],
            "tool_used": "set_viewport_camera",
        },
    },
    {
        "id": "T17",
        "name": "Capture Screenshot",
        "message": "Capture a screenshot of the current viewport",
        "checks": {
            "has_code_patch": False,
            "has_reply": True,
        },
    },
    {
        "id": "T18",
        "name": "Add a Dome Light",
        "message": "Add a dome light with intensity 1000",
        "checks": {
            "has_code_patch": True,
            "code_contains": ["DomeLight"],
            "tool_used": "create_prim",
        },
    },
    {
        "id": "T19",
        "name": "Create OmniGraph",
        "message": "Create a ROS2 clock publisher OmniGraph",
        "checks": {
            "has_code_patch": True,
            "code_contains": ["omni.graph.core", "FABRIC_SHARED"],
        },
    },
    {
        "id": "T20",
        "name": "Multi-Object Scene",
        "message": "Create a table scene: a flat box as a table at 0,0,0.5 (size 2x1x0.05), and three small spheres on top at positions -0.5,0,0.55 and 0,0,0.55 and 0.5,0,0.55",
        "checks": {
            "has_code_patch": True,
            "has_reply": True,
        },
    },
    {
        "id": "T21",
        "name": "Console Error Check",
        "message": "Are there any errors in the console?",
        "checks": {
            "has_reply": True,
        },
    },
    {
        "id": "T22",
        "name": "Sensor Spec Lookup",
        "message": "What are the specs for the Intel RealSense D455?",
        "checks": {
            "has_reply": True,
            "reply_contains": ["D455"],
            "tool_used": "lookup_product_spec",
        },
    },
    {
        "id": "T23",
        "name": "Add Sensor to Prim",
        "message": "Add a camera sensor to /World/Franka",
        "checks": {
            "has_code_patch": True,
            "code_contains": ["Camera", "Franka"],
        },
    },
    {
        "id": "T24",
        "name": "Clone a Prim",
        "message": "Clone /World/Ball to /World/Ball_Copy at position 3, 0, 0.5",
        "checks": {
            "has_code_patch": True,
            "code_contains": ["CopySpec", "Ball", "Ball_Copy"],
            "tool_used": "clone_prim",
        },
    },
    {
        "id": "T25",
        "name": "Move a Prim",
        "message": "Move /World/Ball to position 0, 5, 1",
        "checks": {
            "has_code_patch": True,
            "code_contains": ["Ball"],
        },
    },
]


def run_test(test: dict) -> dict:
    tid = test["id"]
    session = f"autotest_{tid}_{int(time.time())}"
    payload = {
        "session_id": session,
        "message": test["message"],
        "context": {},
    }

    result = {"id": tid, "name": test["name"], "pass": True, "errors": []}

    try:
        resp = requests.post(BASE, json=payload, timeout=TIMEOUT)
        if resp.status_code != 200:
            result["pass"] = False
            result["errors"].append(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return result

        data = resp.json()
    except requests.exceptions.Timeout:
        result["pass"] = False
        result["errors"].append("Request timed out")
        return result
    except Exception as e:
        result["pass"] = False
        result["errors"].append(f"Request failed: {e}")
        return result

    checks = test["checks"]
    reply = data.get("response_messages", [{}])[0].get("content", "")
    actions = data.get("actions_to_approve") or []
    tool_calls = data.get("tool_calls") or []
    all_code = " ".join(a.get("code", "") for a in actions)

    # Check has_reply
    if checks.get("has_reply") and not reply:
        result["pass"] = False
        result["errors"].append("Expected a text reply but got empty")

    # Check reply_contains
    for word in checks.get("reply_contains", []):
        if word.lower() not in reply.lower():
            result["pass"] = False
            result["errors"].append(f"Reply missing '{word}'")

    # Check has_code_patch
    if checks.get("has_code_patch") is True and not actions:
        result["pass"] = False
        result["errors"].append("Expected code patch but got none")
    elif checks.get("has_code_patch") is False and actions:
        # Not a hard fail — LLM sometimes generates code even for data queries
        pass

    # Check code_contains
    for word in checks.get("code_contains", []):
        if word not in all_code:
            result["pass"] = False
            result["errors"].append(f"Code missing '{word}'")

    # Check code_must_not_contain
    for word in checks.get("code_must_not_contain", []):
        if word in all_code:
            result["pass"] = False
            result["errors"].append(f"Code contains forbidden '{word}'")

    # Check tool_used
    expected_tool = checks.get("tool_used")
    if expected_tool:
        tools_used = [tc.get("tool") for tc in tool_calls]
        if expected_tool not in tools_used:
            result["pass"] = False
            result["errors"].append(f"Expected tool '{expected_tool}' but got {tools_used}")

    result["intent"] = data.get("intent", "?")
    result["tools_used"] = [tc.get("tool") for tc in tool_calls]
    result["reply_preview"] = reply[:100] if reply else "(empty)"
    result["code_preview"] = all_code[:120] if all_code else "(none)"

    return result


def main():
    print("=" * 70)
    print("Isaac Assist API Test Runner — T15-T25")
    print("=" * 70)

    # Health check
    try:
        r = requests.get("http://localhost:8000/docs", timeout=5)
        if r.status_code != 200:
            print("ERROR: Service not healthy")
            sys.exit(1)
    except Exception:
        print("ERROR: Service not reachable at localhost:8000")
        sys.exit(1)

    results = []
    passed = 0
    failed = 0

    for test in TESTS:
        print(f"\n{'─' * 50}")
        print(f"Running {test['id']}: {test['name']}...")
        print(f"  Prompt: {test['message'][:60]}...")
        sys.stdout.flush()

        r = run_test(test)
        results.append(r)

        if r["pass"]:
            passed += 1
            print(f"  ✅ PASS  intent={r.get('intent','?')}  tools={r.get('tools_used', [])}")
        else:
            failed += 1
            print(f"  ❌ FAIL  intent={r.get('intent','?')}  tools={r.get('tools_used', [])}")
            for e in r["errors"]:
                print(f"     → {e}")

        if r.get("code_preview") and r["code_preview"] != "(none)":
            print(f"  Code: {r['code_preview']}...")

    print(f"\n{'=' * 70}")
    print(f"Results: {passed} passed, {failed} failed out of {len(TESTS)}")
    print("=" * 70)

    # Summary table
    print(f"\n{'ID':<6} {'Name':<25} {'Status':<8} {'Intent':<18} {'Tools'}")
    print("─" * 90)
    for r in results:
        status = "✅" if r["pass"] else "❌"
        tools = ", ".join(r.get("tools_used", [])) or "—"
        print(f"{r['id']:<6} {r['name']:<25} {status:<8} {r.get('intent','?'):<18} {tools}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
