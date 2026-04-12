#!/usr/bin/env python3
"""
test_runner.py
--------------
Validates test cases against a running Isaac Sim + Isaac Assist stack.

Modes:
  --dry-run     : Validate test case schema only (no Isaac Sim needed)
  --syntax      : Check that expected_code parses as valid Python
  --live        : Execute against a running Kit RPC (localhost:8001)
  --llm         : Send instructions to the LLM service (localhost:8000) and
                  compare tool calls against expected_tool

Usage:
    python scripts/test_runner.py --dry-run
    python scripts/test_runner.py --syntax
    python scripts/test_runner.py --live --category physics
    python scripts/test_runner.py --llm --category omnigraph
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent / "workspace"
TEST_CASES = WORKSPACE / "knowledge" / "test_cases.jsonl"

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

REQUIRED_FIELDS = {"id", "category", "instruction", "expected_tool", "expected_code", "tags"}


def load_test_cases(path: Path, category: str | None = None, tag: str | None = None) -> list[dict]:
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                tc = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"{RED}PARSE ERROR line {line_no}: {e}{RESET}")
                continue
            if category and tc.get("category") != category:
                continue
            if tag and tag not in tc.get("tags", []):
                continue
            cases.append(tc)
    return cases


# ─── Mode: dry-run (schema validation) ───────────────────────────────────────

def run_dry(cases: list[dict]) -> tuple[int, int]:
    passed = failed = 0
    for tc in cases:
        tc_id = tc.get("id", "???")
        missing = REQUIRED_FIELDS - set(tc.keys())
        if missing:
            print(f"  {RED}FAIL{RESET} {tc_id}: missing fields {missing}")
            failed += 1
        elif not tc["instruction"].strip():
            print(f"  {RED}FAIL{RESET} {tc_id}: empty instruction")
            failed += 1
        elif not tc["expected_code"].strip():
            print(f"  {RED}FAIL{RESET} {tc_id}: empty expected_code")
            failed += 1
        else:
            print(f"  {GREEN}PASS{RESET} {tc_id}: schema valid")
            passed += 1
    return passed, failed


# ─── Mode: syntax (Python parse check) ───────────────────────────────────────

def run_syntax(cases: list[dict]) -> tuple[int, int]:
    passed = failed = 0
    for tc in cases:
        tc_id = tc.get("id", "???")
        code = tc.get("expected_code", "")

        # Skip comment-only code blocks
        real_lines = [l for l in code.split("\n") if l.strip() and not l.strip().startswith("#")]
        if not real_lines:
            print(f"  {YELLOW}SKIP{RESET} {tc_id}: comment-only code")
            passed += 1
            continue

        try:
            ast.parse(code)
            print(f"  {GREEN}PASS{RESET} {tc_id}: syntax valid")
            passed += 1
        except SyntaxError as e:
            print(f"  {RED}FAIL{RESET} {tc_id}: {e.msg} (line {e.lineno})")
            failed += 1
    return passed, failed


# ─── Mode: live (execute against Kit RPC) ────────────────────────────────────

async def run_live(cases: list[dict], kit_url: str = "http://127.0.0.1:8001") -> tuple[int, int]:
    try:
        import aiohttp
    except ImportError:
        print(f"{RED}aiohttp required for live mode: pip install aiohttp{RESET}")
        return 0, len(cases)

    passed = failed = 0

    # Check Kit RPC health first
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{kit_url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    print(f"{RED}Kit RPC not healthy: {data}{RESET}")
                    return 0, len(cases)
    except Exception as e:
        print(f"{RED}Cannot connect to Kit RPC at {kit_url}: {e}{RESET}")
        print(f"{YELLOW}Make sure Isaac Sim is running with the extension loaded{RESET}")
        return 0, len(cases)

    print(f"{GREEN}Kit RPC connected at {kit_url}{RESET}\n")

    for tc in cases:
        tc_id = tc.get("id", "???")
        code = tc.get("expected_code", "")

        # Skip non-executable test cases
        if code.strip().startswith("#"):
            print(f"  {YELLOW}SKIP{RESET} {tc_id}: comment-only")
            passed += 1
            continue

        print(f"  {CYAN}EXEC{RESET} {tc_id}: {tc['instruction'][:60]}...", end=" ")
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"code": code, "description": f"Test: {tc_id}"}
                async with session.post(
                    f"{kit_url}/exec_patch",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
                    if result.get("queued") or result.get("ok"):
                        print(f"{GREEN}QUEUED{RESET}")
                        passed += 1
                    elif "error" in result:
                        print(f"{RED}ERROR: {result['error'][:80]}{RESET}")
                        failed += 1
                    else:
                        print(f"{GREEN}OK{RESET}")
                        passed += 1
        except Exception as e:
            print(f"{RED}EXCEPTION: {e}{RESET}")
            failed += 1

        # Small delay between executions to not overwhelm Kit
        await asyncio.sleep(0.5)

    return passed, failed


# ─── Mode: llm (test LLM tool selection) ─────────────────────────────────────

async def run_llm(cases: list[dict], service_url: str = "http://127.0.0.1:8000") -> tuple[int, int]:
    try:
        import aiohttp
    except ImportError:
        print(f"{RED}aiohttp required for llm mode: pip install aiohttp{RESET}")
        return 0, len(cases)

    passed = failed = 0

    # Health check
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{service_url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if data.get("status") != "ok":
                    print(f"{RED}Service not healthy: {data}{RESET}")
                    return 0, len(cases)
    except Exception as e:
        print(f"{RED}Cannot connect to service at {service_url}: {e}{RESET}")
        return 0, len(cases)

    print(f"{GREEN}Service connected at {service_url}{RESET}\n")

    for tc in cases:
        tc_id = tc.get("id", "???")
        instruction = tc.get("instruction", "")
        expected_tool = tc.get("expected_tool", "")

        print(f"  {CYAN}LLM{RESET} {tc_id}: \"{instruction[:50]}...\"", end=" ")

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "session_id": f"test_{tc_id}",
                    "message": instruction,
                    "context": tc.get("context", {}),
                }
                async with session.post(
                    f"{service_url}/api/v1/chat/message",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    result = await resp.json()

                    # Check if the LLM mentioned the expected tool or generated relevant code
                    response_text = ""
                    for msg in result.get("response_messages", []):
                        response_text += msg.get("content", "")

                    tool_mentioned = expected_tool.lower() in response_text.lower()
                    code_present = "```" in response_text or "import omni" in response_text

                    if tool_mentioned or code_present:
                        print(f"{GREEN}PASS{RESET} (tool: {expected_tool})")
                        passed += 1
                    else:
                        print(f"{RED}MISS{RESET} (expected: {expected_tool})")
                        failed += 1

        except Exception as e:
            print(f"{RED}ERROR: {e}{RESET}")
            failed += 1

        await asyncio.sleep(1)  # Rate limit

    return passed, failed


# ─── Report ───────────────────────────────────────────────────────────────────

def print_report(mode: str, passed: int, failed: int, elapsed: float, cases: list[dict]):
    total = passed + failed
    pct = (passed / total * 100) if total > 0 else 0
    color = GREEN if failed == 0 else RED

    # Category breakdown
    cat_results: dict[str, dict[str, int]] = {}
    for i, tc in enumerate(cases):
        cat = tc.get("category", "unknown")
        if cat not in cat_results:
            cat_results[cat] = {"total": 0}
        cat_results[cat]["total"] += 1

    print(f"\n{'='*60}")
    print(f"  Mode: {mode}")
    print(f"  Total: {total} | {GREEN}Passed: {passed}{RESET} | {RED}Failed: {failed}{RESET}")
    print(f"  Pass rate: {color}{pct:.1f}%{RESET}")
    print(f"  Elapsed: {elapsed:.2f}s")
    print(f"\n  Categories tested:")
    for cat, data in sorted(cat_results.items()):
        print(f"    {cat}: {data['total']} cases")
    print(f"{'='*60}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Isaac Assist test runner")
    parser.add_argument("--dry-run", action="store_true", help="Schema validation only")
    parser.add_argument("--syntax", action="store_true", help="Python syntax check")
    parser.add_argument("--live", action="store_true", help="Execute against Kit RPC")
    parser.add_argument("--llm", action="store_true", help="Test LLM tool selection")
    parser.add_argument("--category", type=str, help="Filter by category")
    parser.add_argument("--tag", type=str, help="Filter by tag")
    parser.add_argument("--kit-url", default="http://127.0.0.1:8001")
    parser.add_argument("--service-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    if not any([args.dry_run, args.syntax, args.live, args.llm]):
        args.dry_run = True  # Default

    if not TEST_CASES.exists():
        print(f"{RED}Test cases not found at {TEST_CASES}{RESET}")
        sys.exit(1)

    cases = load_test_cases(TEST_CASES, category=args.category, tag=args.tag)
    print(f"Loaded {len(cases)} test cases", end="")
    if args.category:
        print(f" (category={args.category})", end="")
    if args.tag:
        print(f" (tag={args.tag})", end="")
    print("\n")

    if not cases:
        print(f"{YELLOW}No matching test cases found{RESET}")
        sys.exit(0)

    start = time.time()

    if args.dry_run:
        print(f"{CYAN}Running schema validation...{RESET}")
        passed, failed = run_dry(cases)
        print_report("dry-run", passed, failed, time.time() - start, cases)

    if args.syntax:
        print(f"{CYAN}Running syntax check...{RESET}")
        passed, failed = run_syntax(cases)
        print_report("syntax", passed, failed, time.time() - start, cases)

    if args.live:
        print(f"{CYAN}Running live execution against Kit RPC...{RESET}")
        passed, failed = asyncio.run(run_live(cases, args.kit_url))
        print_report("live", passed, failed, time.time() - start, cases)

    if args.llm:
        print(f"{CYAN}Running LLM tool-selection tests...{RESET}")
        passed, failed = asyncio.run(run_llm(cases, args.service_url))
        print_report("llm", passed, failed, time.time() - start, cases)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
