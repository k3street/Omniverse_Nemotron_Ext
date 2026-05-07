"""
measure_token_char_ratio.py — Track C 9.4 calibration.

Samples tool_result content from historical sessions and computes
char-to-token ratios. The default `chars / 4` token estimate likely
under-counts for path-heavy USD content (paths get split aggressively
into many small tokens).

Tokenizer: `sentence-transformers/all-MiniLM-L6-v2` BertTokenizer
(already pulled by ChromaDB). This is NOT Gemini's native tokenizer
(unavailable without the google-generativeai SDK), but for
order-of-magnitude calibration it's a reasonable proxy. BertTokenizer
WordPiece tokenization is structurally similar enough to detect
"path-heavy = many small tokens" pattern.

Why this matters:
- P1 (per-tool size cap) and P2 (per-call request budget) thresholds
  are set in chars or tokens. If `chars / 4` undercounts by 2x for
  USD content, our payload thresholds are 2x too lax.
- 9.2 reported max payload 542 KB. If real token count is ~chars/2,
  that's 271k tokens — close to model context limits.

Usage:
  python scripts/qa/measure_token_char_ratio.py
  python scripts/qa/measure_token_char_ratio.py --max-samples 500
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
QA_RUNS = REPO_ROOT / "workspace" / "qa_runs"
REPORT_PATH = QA_RUNS / "token_char_ratio_layer1.md"


def percentile(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(int(p * len(s)), len(s) - 1)
    return s[idx]


def collect_samples(max_samples: int | None = None) -> List[Dict]:
    """Walk run_direct files, extract tool_result content with tool name."""
    out: List[Dict] = []
    runs = sorted(QA_RUNS.glob("run_direct_*"))
    for r in runs:
        if max_samples and len(out) >= max_samples:
            break
        files = list(r.glob("*_direct.jsonl"))
        if not files:
            continue
        try:
            with open(files[0]) as fh:
                for line in fh:
                    if max_samples and len(out) >= max_samples:
                        break
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("event") != "isaac_assist_reply":
                        continue
                    for tc in d.get("tool_calls") or []:
                        if max_samples and len(out) >= max_samples:
                            break
                        result = tc.get("result")
                        if result is None:
                            continue
                        text = json.dumps(result, default=str)
                        if not text:
                            continue
                        out.append({
                            "tool": tc.get("tool", "?"),
                            "text": text,
                            "chars": len(text),
                        })
        except Exception:
            continue
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-samples", type=int, default=2000,
                   help="Max tool_result samples (default 2000)")
    p.add_argument("--out", default=str(REPORT_PATH))
    args = p.parse_args()

    print(f"[token-ratio] loading tokenizer (MiniLM BertWordPiece)...")
    try:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(
            "sentence-transformers/all-MiniLM-L6-v2"
        )
    except Exception as e:
        print(f"[token-ratio] failed to load tokenizer: {e}")
        return 2

    print(f"[token-ratio] collecting samples (max {args.max_samples})...")
    samples = collect_samples(args.max_samples)
    print(f"[token-ratio] {len(samples)} samples collected")

    if not samples:
        print("[token-ratio] no data — exiting")
        return 1

    # Tokenize each, compute char/token ratio
    per_tool: Dict[str, List[float]] = defaultdict(list)
    all_ratios: List[float] = []
    all_token_counts: List[int] = []
    all_chars: List[int] = []

    for i, s in enumerate(samples):
        if i % 200 == 0:
            print(f"[token-ratio] {i}/{len(samples)}...")
        try:
            n_tokens = len(tok.encode(s["text"], add_special_tokens=False))
        except Exception:
            continue
        if n_tokens == 0:
            continue
        ratio = s["chars"] / n_tokens
        per_tool[s["tool"]].append(ratio)
        all_ratios.append(ratio)
        all_token_counts.append(n_tokens)
        all_chars.append(s["chars"])

    # Aggregate
    n = len(all_ratios)
    median_ratio = percentile(all_ratios, 0.5)
    mean_ratio = sum(all_ratios) / n if n else 0
    p10 = percentile(all_ratios, 0.1)  # path-heaviest
    p90 = percentile(all_ratios, 0.9)
    total_chars = sum(all_chars)
    total_tokens = sum(all_token_counts)
    overall_ratio = total_chars / total_tokens if total_tokens else 0

    # Render report
    lines = ["# Token-vs-char ratio measurement (Track C 9.4)"]
    lines.append("")
    lines.append("Calibrates the `chars / 4` token-count heuristic against the "
                 "actual tokenization of historical tool_result content. Uses "
                 "MiniLM BertTokenizer as a proxy for Gemini's tokenizer "
                 "(`google-generativeai` SDK not installed; MiniLM WordPiece "
                 "is order-of-magnitude similar for English + path-heavy "
                 "USD content).")
    lines.append("")
    lines.append(f"**Samples**: {n} tool_result blobs from "
                 "`workspace/qa_runs/run_direct_*`.")
    lines.append("")
    lines.append("## Aggregate ratio (chars per token)")
    lines.append("")
    lines.append(f"- median: **{median_ratio:.2f}**")
    lines.append(f"- mean: **{mean_ratio:.2f}**")
    lines.append(f"- weighted (total chars / total tokens): **{overall_ratio:.2f}**")
    lines.append(f"- p10 (path-heaviest 10%): {p10:.2f} chars/token")
    lines.append(f"- p90 (text-heaviest 10%): {p90:.2f} chars/token")
    lines.append("")
    lines.append(f"- Total chars sampled: {total_chars:,}")
    lines.append(f"- Total tokens: {total_tokens:,}")
    lines.append("")

    # Compare to chars/4 heuristic
    if median_ratio < 3.5:
        char4_verdict = (
            f"**`chars / 4` UNDERCOUNTS tokens by ~{100*(4-median_ratio)/median_ratio:.0f}%**. "
            "Path-heavy / structured-data outputs tokenize more aggressively "
            "than English. Threshold tuning for P1/P2 should use this measured "
            "ratio instead of the default heuristic."
        )
    elif median_ratio > 4.5:
        char4_verdict = (
            f"**`chars / 4` OVERCOUNTS tokens by ~{100*(median_ratio-4)/4:.0f}%**. "
            "Content is text-heavy (English prose, comments). Default heuristic "
            "leaves headroom — but path-heavy sub-types may still undercount."
        )
    else:
        char4_verdict = (
            f"**`chars / 4` is approximately accurate** (within 12%). "
            "Default heuristic is fine for typical content."
        )
    lines.append(char4_verdict)
    lines.append("")

    # Per-tool breakdown
    if per_tool:
        lines.append("## Per-tool ratio")
        lines.append("")
        lines.append("| Tool | n | median chars/token | min | max |")
        lines.append("|------|--:|-------------------:|----:|----:|")
        rows = []
        for tool, rs in per_tool.items():
            rows.append((tool, len(rs), percentile(rs, 0.5), min(rs), max(rs)))
        # Sort by sample count
        rows.sort(key=lambda r: -r[1])
        for tool, cnt, med, mn, mx in rows[:15]:
            lines.append(f"| `{tool}` | {cnt} | {med:.2f} | {mn:.2f} | {mx:.2f} |")
        if len(rows) > 15:
            lines.append(f"| ... ({len(rows)-15} more) | | | | |")
        lines.append("")

        # Path-heavy outliers
        path_heavy = [(t, percentile(r, 0.5)) for t, r in per_tool.items() if len(r) >= 5]
        path_heavy.sort(key=lambda x: x[1])
        if path_heavy:
            lines.append("### Path-heaviest tools (lowest chars/token, harder to estimate)")
            lines.append("")
            for t, r in path_heavy[:5]:
                lines.append(f"- `{t}`: {r:.2f} chars/token")
            lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append("- MiniLM BertTokenizer ≠ Gemini's tokenizer. Gemini uses "
                 "SentencePiece BPE; relative differences in path-vs-prose "
                 "content should hold but absolute ratio may vary by ~20%.")
    lines.append("- `tool_result` content is JSON-serialized for measurement, "
                 "which adds quote+comma overhead vs raw output. Real LLM "
                 "payload tokens may be slightly lower.")
    lines.append("- Sample biased toward direct-eval tasks (build/verify-heavy). "
                 "Production chat may have different content mix.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by `scripts/qa/measure_token_char_ratio.py`*")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[token-ratio] median chars/token: {median_ratio:.2f}")
    print(f"[token-ratio] weighted: {overall_ratio:.2f}")
    print(f"[token-ratio] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
