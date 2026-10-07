#!/usr/bin/env python3
"""Score the motion critic against hand labels (workspace/knowledge/critic_gold.json).

A critic is only as good as its agreement with a careful human. This reads
each labelled asset's latest critic verdict (or runs the critic again with
--rerun) and reports agreement: a 'bad' asset the critic passed is the costly
miss, since auto-approval trusts a pass.

    python scripts/critic_gold.py            # score the stored verdicts
    python scripts/critic_gold.py --rerun    # judge every labelled asset again first
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
GOLD = REPO / "workspace" / "knowledge" / "critic_gold.json"
QUEUE = REPO / "workspace" / "review_queue"


def score(rerun: bool = False) -> dict:
    labels = json.loads(GOLD.read_text())["labels"]
    rows, tally = [], {"agree": 0, "missed_bad": 0, "rejected_good": 0, "not_judged": 0}
    for a, lab in sorted(labels.items()):
        qf = QUEUE / f"{a}.json"
        if not qf.exists():
            continue
        if rerun:
            from motion_critic import critique
            try:
                critique(a)
            except Exception as ex:  # noqa: BLE001 - a missing video is a row, not a crash
                rows.append((a, lab["label"], f"error: {str(ex)[:60]}"))
                continue
        mq = json.loads(qf.read_text()).get("motion_qa") or {}
        verdict = "pass" if mq.get("pass") else "incomplete" if mq.get("incomplete") else "fail" if mq else "none"
        if verdict in ("incomplete", "none"):
            tally["not_judged"] += 1
        elif (verdict == "pass") == (lab["label"] == "good"):
            tally["agree"] += 1
        elif verdict == "pass":
            tally["missed_bad"] += 1
        else:
            tally["rejected_good"] += 1
        rows.append((a, lab["label"], verdict))
    judged = tally["agree"] + tally["missed_bad"] + tally["rejected_good"]
    return {"rows": rows, **tally, "judged": judged,
            "agreement": round(tally["agree"] / judged, 3) if judged else None}


def main() -> int:
    r = score("--rerun" in sys.argv)
    for a, lab, v in r["rows"]:
        mark = "" if v in ("incomplete", "none") or v.startswith("error") else \
            ("  ok" if (v == "pass") == (lab == "good") else "  <-- MISSED BAD" if v == "pass" else "  <-- rejected good")
        print(f"{a:45s} {lab:5s} critic {v}{mark}")
    print(f"agreement {r['agreement']} on {r['judged']} judged; {r['missed_bad']} bad passed, "
          f"{r['rejected_good']} good failed, {r['not_judged']} not judged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
