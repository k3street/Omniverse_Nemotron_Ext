"""
Add new task .md files to the isaac_assist_templates ChromaDB collection.

Reads the Goal block from each .md file and inserts/updates a template
entry with task_id == filename stem. Idempotent — re-running with the
same files updates the document/metadata in place.

Usage:
    python -m scripts.qa.add_templates_from_tasks T4-06 T4-07 ...
    python -m scripts.qa.add_templates_from_tasks --all-new

Single-process. Do not run while another writer (post-commit indexer,
chat session) is touching the same ChromaDB collection.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import chromadb

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "docs" / "qa" / "tasks"
INDEX_PATH = REPO_ROOT / "workspace" / "tool_index"
COLLECTION = "isaac_assist_templates"


def parse_goal(md_path: Path) -> str:
    """Extract the **Goal:** paragraph from a task markdown file."""
    text = md_path.read_text()
    m = re.search(r"\*\*Goal:\*\*\s*(.+?)(?=\n\n|\n\*\*)", text, re.S)
    return m.group(1).strip() if m else ""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("task_ids", nargs="*", help="task IDs to add (e.g. T4-06 CW-31)")
    p.add_argument("--all-new", action="store_true",
                   help="add every task .md file not already in ChromaDB")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    client = chromadb.PersistentClient(path=str(INDEX_PATH))
    coll = client.get_collection(COLLECTION)
    existing_ids = set(coll.get()["ids"])

    if args.all_new:
        all_md = sorted(TASKS_DIR.glob("*.md"))
        ids = [m.stem for m in all_md if m.stem not in existing_ids]
    else:
        ids = args.task_ids

    if not ids:
        print("Nothing to add.")
        return 0

    docs, metadatas, kept_ids = [], [], []
    for tid in ids:
        md = TASKS_DIR / f"{tid}.md"
        if not md.exists():
            print(f"SKIP {tid}: no {md.name}", file=sys.stderr)
            continue
        goal = parse_goal(md)
        if not goal:
            print(f"SKIP {tid}: no Goal block parsed", file=sys.stderr)
            continue
        kept_ids.append(tid)
        docs.append(goal)
        metadatas.append({"task_id": tid})

    if not kept_ids:
        return 0

    if args.dry_run:
        for tid, goal in zip(kept_ids, docs):
            print(f"[dry-run] {tid}: {goal[:80]}...")
        return 0

    upsert_ids = [t for t in kept_ids if t in existing_ids]
    add_ids = [t for t in kept_ids if t not in existing_ids]

    if add_ids:
        add_docs = [docs[kept_ids.index(t)] for t in add_ids]
        add_metas = [metadatas[kept_ids.index(t)] for t in add_ids]
        coll.add(ids=add_ids, documents=add_docs, metadatas=add_metas)
        print(f"Added {len(add_ids)} new templates: {add_ids}")
    if upsert_ids:
        up_docs = [docs[kept_ids.index(t)] for t in upsert_ids]
        up_metas = [metadatas[kept_ids.index(t)] for t in upsert_ids]
        coll.update(ids=upsert_ids, documents=up_docs, metadatas=up_metas)
        print(f"Updated {len(upsert_ids)} existing templates: {upsert_ids}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
