#!/usr/bin/env python3
"""
generate_knowledge.py
---------------------
Generates fine-tuning knowledge from multiple sources:
  1. Test case corpus (test_cases.jsonl) → instruction/response pairs
  2. Live session capture (audit.jsonl) → real user interactions
  3. Isaac Sim API docs (scraped) → factual QA pairs
  4. Synthetic augmentation → paraphrased variants of existing pairs

Outputs to workspace/finetune_exports/ in multiple formats:
  - ShareGPT (Unsloth/Qwen/Llama)
  - Gemini Vertex AI
  - OpenAI fine-tune
  - Alpaca

Usage:
    python scripts/data_curation/generate_knowledge.py --all
    python scripts/data_curation/generate_knowledge.py --from-tests
    python scripts/data_curation/generate_knowledge.py --from-audit
    python scripts/data_curation/generate_knowledge.py --augment
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent.parent / "workspace"
KNOWLEDGE_DIR = WORKSPACE / "knowledge"
EXPORT_DIR = WORKSPACE / "finetune_exports"
TEST_CASES = KNOWLEDGE_DIR / "test_cases.jsonl"
AUDIT_LOG = WORKSPACE / "audit.jsonl"

SYSTEM_PROMPT = (
    "You are Isaac Assist, an AI agent with full control over NVIDIA Isaac Sim. "
    "You can create and modify USD prims, apply physics and materials, build OmniGraph "
    "action graphs, attach sensors, control the simulation, import robots, generate "
    "synthetic data, and debug console errors. You execute Python code inside the Kit "
    "process using omni.kit.commands (all actions are Ctrl+Z undoable). Always explain "
    "what you will do before executing, and show the code for user approval."
)

# ─── Paraphrase templates for augmentation ────────────────────────────────────
INSTRUCTION_VARIANTS = {
    "create": ["Make", "Build", "Add", "Generate", "Spawn", "Set up", "Put"],
    "delete": ["Remove", "Get rid of", "Destroy", "Clear", "Eliminate"],
    "move": ["Move", "Relocate", "Shift", "Place", "Position", "Put"],
    "show": ["Show me", "Display", "Let me see", "What does", "Can you show"],
    "add": ["Attach", "Apply", "Enable", "Put on", "Give it"],
    "set": ["Change", "Update", "Modify", "Configure", "Adjust", "Set"],
    "what": ["What is", "Tell me about", "Explain", "Describe", "What are"],
    "help": ["Help me", "I need help with", "Fix", "Debug", "Why is"],
    "import": ["Load", "Import", "Bring in", "Open", "Fetch"],
    "list": ["List", "Show all", "Find all", "What are all the", "Enumerate"],
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def save_jsonl(entries: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(entries)} entries to {path}")


# ─── Source 1: Convert test cases to training pairs ───────────────────────────

def test_cases_to_pairs(test_cases: list[dict]) -> list[dict]:
    """Convert structured test cases into instruction/response fine-tuning pairs."""
    pairs = []
    for tc in test_cases:
        instruction = tc.get("instruction", "")
        code = tc.get("expected_code", "")
        tool = tc.get("expected_tool", "")
        context = tc.get("context", {})
        tags = tc.get("tags", [])

        if not instruction or not code:
            continue

        # Build the response with reasoning + code
        context_parts = []
        if context.get("selected_prim"):
            context_parts.append(
                f"I see you have **{context['selected_prim']}** selected"
            )
        if context.get("prim_type"):
            context_parts.append(f"(type: {context['prim_type']})")

        context_str = ". ".join(context_parts) + ". " if context_parts else ""

        response = (
            f"{context_str}"
            f"I'll use the `{tool}` tool to accomplish this. "
            f"Here's the code I'll execute:\n\n"
            f"```python\n{code}\n```\n\n"
            f"This will be executed inside Isaac Sim with undo support (Ctrl+Z). "
            f"Shall I proceed?"
        )

        pairs.append(
            {
                "instruction": instruction,
                "response": response,
                "system": SYSTEM_PROMPT,
                "source": "test_corpus",
                "category": tc.get("category", ""),
                "tags": tags,
            }
        )
    return pairs


# ─── Source 2: Convert audit log entries to training pairs ────────────────────

def audit_to_pairs(audit_entries: list[dict]) -> list[dict]:
    """Convert real user interaction logs into training pairs."""
    pairs = []
    for entry in audit_entries:
        user_msg = entry.get("user_message", entry.get("instruction", ""))
        assistant_msg = entry.get("assistant_response", entry.get("response", ""))
        if not user_msg or not assistant_msg:
            continue

        # Skip system/internal messages
        if user_msg.startswith("System Report:"):
            continue

        pairs.append(
            {
                "instruction": user_msg,
                "response": assistant_msg,
                "system": SYSTEM_PROMPT,
                "source": "audit_log",
                "category": entry.get("intent", "general"),
                "tags": [],
            }
        )
    return pairs


# ─── Source 3: Synthetic augmentation via paraphrasing ────────────────────────

def augment_pairs(pairs: list[dict], multiplier: int = 3) -> list[dict]:
    """Generate paraphrased variants of existing instruction/response pairs."""
    augmented = []
    for pair in pairs:
        instruction = pair["instruction"]

        for _ in range(multiplier):
            new_instruction = _paraphrase(instruction)
            if new_instruction != instruction:
                augmented.append(
                    {
                        "instruction": new_instruction,
                        "response": pair["response"],
                        "system": pair.get("system", SYSTEM_PROMPT),
                        "source": "augmented",
                        "category": pair.get("category", ""),
                        "tags": pair.get("tags", []) + ["augmented"],
                    }
                )
    return augmented


def _paraphrase(text: str) -> str:
    """Simple rule-based paraphrasing for data augmentation."""
    result = text

    # Randomly apply transformations
    if random.random() < 0.5:
        # Swap leading verb with synonym
        for verb, synonyms in INSTRUCTION_VARIANTS.items():
            pattern = re.compile(rf"^{verb}\b", re.IGNORECASE)
            if pattern.match(result):
                replacement = random.choice(synonyms)
                result = pattern.sub(replacement, result, count=1)
                break

    if random.random() < 0.3:
        # Add polite prefix
        prefixes = [
            "Can you ",
            "Please ",
            "I'd like to ",
            "Could you ",
            "I want to ",
            "I need to ",
        ]
        if not any(result.lower().startswith(p.lower()) for p in prefixes):
            result = random.choice(prefixes) + result[0].lower() + result[1:]

    if random.random() < 0.2:
        # Add trailing context
        suffixes = [
            " in the current scene",
            " for me",
            " right now",
            " if possible",
        ]
        if not result.endswith("?"):
            result = result.rstrip(".") + random.choice(suffixes)

    return result


# ─── Export formatters ────────────────────────────────────────────────────────

def to_sharegpt(pairs: list[dict]) -> list[dict]:
    """ShareGPT format for Unsloth (Qwen, Llama, Gemma)."""
    records = []
    for p in pairs:
        conversations = []
        if p.get("system"):
            conversations.append({"from": "system", "value": p["system"]})
        conversations.append({"from": "human", "value": p["instruction"]})
        conversations.append({"from": "gpt", "value": p["response"]})
        records.append({"conversations": conversations})
    return records


def to_gemini(pairs: list[dict]) -> list[dict]:
    """Vertex AI Gemini fine-tuning format."""
    records = []
    for p in pairs:
        contents = []
        if p.get("system"):
            contents.append(
                {"role": "user", "parts": [{"text": f"[System] {p['system']}"}]}
            )
            contents.append(
                {
                    "role": "model",
                    "parts": [{"text": "Understood. I am Isaac Assist, ready to help."}],
                }
            )
        contents.append({"role": "user", "parts": [{"text": p["instruction"]}]})
        contents.append({"role": "model", "parts": [{"text": p["response"]}]})
        records.append({"contents": contents})
    return records


def to_openai(pairs: list[dict]) -> list[dict]:
    """OpenAI fine-tuning chat format."""
    records = []
    for p in pairs:
        messages = []
        if p.get("system"):
            messages.append({"role": "system", "content": p["system"]})
        messages.append({"role": "user", "content": p["instruction"]})
        messages.append({"role": "assistant", "content": p["response"]})
        records.append({"messages": messages})
    return records


def to_alpaca(pairs: list[dict]) -> list[dict]:
    """Alpaca instruction-tuning format."""
    records = []
    for p in pairs:
        records.append(
            {
                "instruction": p["instruction"],
                "input": "",
                "output": p["response"],
                "system": p.get("system", ""),
            }
        )
    return records


def to_tool_calling(pairs: list[dict], test_cases: list[dict]) -> list[dict]:
    """
    Tool-calling format: instruction → tool_call JSON.
    Maps test cases to structured function calls for training models on tool use.
    """
    records = []
    tc_map = {tc["id"]: tc for tc in test_cases if "id" in tc}

    for tc in test_cases:
        tool = tc.get("expected_tool", "")
        code = tc.get("expected_code", "")
        instruction = tc.get("instruction", "")
        context = tc.get("context", {})

        if not tool or not instruction:
            continue

        tool_call = {
            "name": tool,
            "arguments": {
                "code": code,
            },
        }
        if context.get("selected_prim"):
            tool_call["arguments"]["prim_path"] = context["selected_prim"]

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{tc.get('id', 'unknown')}",
                        "type": "function",
                        "function": tool_call,
                    }
                ],
            },
        ]
        records.append({"messages": messages})
    return records


# ─── Main pipeline ────────────────────────────────────────────────────────────

def run_pipeline(
    from_tests: bool = True,
    from_audit: bool = True,
    augment: bool = True,
    augment_multiplier: int = 3,
):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    all_pairs = []
    test_cases = []

    # Source 1: Test cases
    if from_tests:
        print("Loading test cases...")
        test_cases = load_jsonl(TEST_CASES)
        pairs = test_cases_to_pairs(test_cases)
        print(f"  {len(pairs)} pairs from {len(test_cases)} test cases")
        all_pairs.extend(pairs)

    # Source 2: Audit log
    if from_audit:
        print("Loading audit log...")
        audit = load_jsonl(AUDIT_LOG)
        pairs = audit_to_pairs(audit)
        print(f"  {len(pairs)} pairs from {len(audit)} audit entries")
        all_pairs.extend(pairs)

    # Source 3: Existing knowledge files
    print("Loading existing knowledge...")
    for kb_file in KNOWLEDGE_DIR.glob("knowledge_*.jsonl"):
        entries = load_jsonl(kb_file)
        for e in entries:
            if e.get("instruction") and e.get("response"):
                all_pairs.append(
                    {
                        "instruction": e["instruction"],
                        "response": e["response"],
                        "system": SYSTEM_PROMPT,
                        "source": f"knowledge_{kb_file.stem}",
                        "category": "docs",
                        "tags": [],
                    }
                )
        print(f"  {len(entries)} entries from {kb_file.name}")

    # Deduplicate by instruction
    seen = set()
    deduped = []
    for p in all_pairs:
        key = p["instruction"].strip().lower()
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    print(f"  {len(deduped)} unique pairs after dedup (from {len(all_pairs)} total)")
    all_pairs = deduped

    # Source 4: Augmentation
    augmented = []
    if augment and all_pairs:
        print(f"Augmenting with {augment_multiplier}x paraphrasing...")
        augmented = augment_pairs(all_pairs, multiplier=augment_multiplier)
        # Deduplicate augmented against originals
        for a in augmented:
            key = a["instruction"].strip().lower()
            if key not in seen:
                seen.add(key)
                all_pairs.append(a)
        print(f"  {len(augmented)} augmented pairs added")

    print(f"\nTotal training pairs: {len(all_pairs)}")

    # Category stats
    cat_counts: dict[str, int] = {}
    for p in all_pairs:
        cat = p.get("category", "unknown")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    print("\nCategory breakdown:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    # Export all formats
    print("\nExporting...")
    save_jsonl(to_sharegpt(all_pairs), EXPORT_DIR / "sharegpt_all.jsonl")
    save_jsonl(to_gemini(all_pairs), EXPORT_DIR / "gemini_all.jsonl")
    save_jsonl(to_openai(all_pairs), EXPORT_DIR / "openai_all.jsonl")
    save_jsonl(to_alpaca(all_pairs), EXPORT_DIR / "alpaca_all.jsonl")

    if test_cases:
        save_jsonl(
            to_tool_calling(all_pairs, test_cases),
            EXPORT_DIR / "tool_calling_all.jsonl",
        )

    # Also export per-category splits
    categories = set(p.get("category", "") for p in all_pairs)
    for cat in categories:
        if not cat:
            continue
        cat_pairs = [p for p in all_pairs if p.get("category") == cat]
        safe_cat = re.sub(r"[^a-zA-Z0-9_]", "_", cat)
        save_jsonl(
            to_sharegpt(cat_pairs), EXPORT_DIR / f"sharegpt_{safe_cat}.jsonl"
        )

    # Summary manifest
    manifest = {
        "total_pairs": len(all_pairs),
        "categories": cat_counts,
        "sources": {
            "test_corpus": sum(
                1 for p in all_pairs if p.get("source") == "test_corpus"
            ),
            "audit_log": sum(
                1 for p in all_pairs if p.get("source") == "audit_log"
            ),
            "augmented": sum(
                1 for p in all_pairs if p.get("source") == "augmented"
            ),
            "knowledge": sum(
                1
                for p in all_pairs
                if p.get("source", "").startswith("knowledge_")
            ),
        },
        "formats": [
            "sharegpt (Unsloth/Qwen/Llama/Gemma)",
            "gemini (Vertex AI)",
            "openai (GPT fine-tune)",
            "alpaca (instruction-tune)",
            "tool_calling (function-calling)",
        ],
    }
    with open(EXPORT_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest written to {EXPORT_DIR / 'manifest.json'}")
    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate fine-tuning knowledge")
    parser.add_argument(
        "--all", action="store_true", help="Run full pipeline (default)"
    )
    parser.add_argument(
        "--from-tests", action="store_true", help="Generate from test cases only"
    )
    parser.add_argument(
        "--from-audit", action="store_true", help="Generate from audit log only"
    )
    parser.add_argument(
        "--augment", action="store_true", help="Generate augmented variants only"
    )
    parser.add_argument(
        "--augment-multiplier",
        type=int,
        default=3,
        help="Number of paraphrase variants per pair (default: 3)",
    )
    parser.add_argument(
        "--no-augment",
        action="store_true",
        help="Skip augmentation step",
    )
    args = parser.parse_args()

    # Default to --all if nothing specified
    if not (args.from_tests or args.from_audit or args.augment):
        args.all = True

    run_pipeline(
        from_tests=args.all or args.from_tests,
        from_audit=args.all or args.from_audit,
        augment=(args.all or args.augment) and not args.no_augment,
        augment_multiplier=args.augment_multiplier,
    )
