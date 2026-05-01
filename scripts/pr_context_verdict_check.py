#!/usr/bin/env python3
"""Post-hoc sanity check on PR verdict distribution.

Reads summary files produced by the prcontext-populate skill and flags
suspicious patterns: uniform verdicts across large batches, hint/verdict
mismatches, or missing summaries.

Usage:
    python3 scripts/pr_context_verdict_check.py
    python3 scripts/pr_context_verdict_check.py --manifest artifacts/prcontext/prcontext.md
"""

import argparse
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

import yaml

log = logging.getLogger("pr_context_verdict_check")

SKEW_THRESHOLD = 0.80
SKEW_MIN_ENTRIES = 5


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _parse_summary_frontmatter(path):
    """Extract YAML frontmatter from a summary .md file."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        return {}
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def check_distribution(verdicts, total):
    """Flag if any single verdict exceeds the skew threshold."""
    flags = []
    if total < SKEW_MIN_ENTRIES:
        return flags
    for verdict, count in verdicts.items():
        if count / total > SKEW_THRESHOLD:
            flags.append(
                f"Distribution skew: {count}/{total} PRs have verdict "
                f"'{verdict}' (>{int(SKEW_THRESHOLD * 100)}%)")
    return flags


def check_hint_overrides(entries, summaries):
    """Flag entries where a peripheral hint was overridden to relevant."""
    flags = []
    for entry in entries:
        hint = entry.get("hint", "no-hint")
        if hint != "candidate-peripheral":
            continue
        stem = entry.get("file", "")
        summary = summaries.get(stem, {})
        if summary.get("verdict") == "relevant":
            reason = entry.get("hint_reason", "unknown")
            url = entry.get("url", stem)
            flags.append(
                f"Hint override: {url} has hint=candidate-peripheral "
                f"({reason}) but verdict=relevant")
    return flags


def check_missing_summaries(entries, output_dir):
    """Flag fetched entries that lack a summary file."""
    flags = []
    for entry in entries:
        if entry.get("status") != "fetched":
            continue
        stem = entry.get("file", "")
        summary_path = os.path.join(output_dir, f"{stem}.md")
        if not os.path.isfile(summary_path):
            flags.append(f"Missing summary: {stem}.md not found")
    return flags


def main():
    parser = argparse.ArgumentParser(
        description="Sanity-check verdict distribution across PR summaries.")
    parser.add_argument("--manifest", default="artifacts/prcontext/prcontext.md",
                        help="Path to the prcontext manifest")
    parser.add_argument("--output-dir", default=None,
                        help="Directory with summary .md files "
                             "(default: manifest parent directory)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.path.isfile(args.manifest):
        log.error("Manifest not found: %s", args.manifest)
        sys.exit(2)

    fm, _ = _parse_manifest(args.manifest)
    output_dir = args.output_dir or os.path.dirname(args.manifest)
    entries = fm.get("pull_requests", [])

    fetched = [e for e in entries if e.get("status") == "fetched"]

    summaries = {}
    verdicts = Counter()
    for entry in fetched:
        stem = entry.get("file", "")
        summary_path = os.path.join(output_dir, f"{stem}.md")
        if os.path.isfile(summary_path):
            sfm = _parse_summary_frontmatter(summary_path)
            summaries[stem] = sfm
            verdict = sfm.get("verdict", "unknown")
            verdicts[verdict] += 1

    total = len(fetched)
    all_flags = []
    all_flags.extend(check_missing_summaries(entries, output_dir))
    all_flags.extend(check_distribution(dict(verdicts), total))
    all_flags.extend(check_hint_overrides(entries, summaries))

    status = "flagged" if all_flags else "clean"

    report_fm = {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_fetched": total,
        "verdicts": dict(verdicts),
        "flags": len(all_flags),
        "status": status,
    }

    report_path = os.path.join(output_dir, "verdict_check.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(report_fm, default_flow_style=False,
                          sort_keys=False, allow_unicode=True))
        f.write("---\n\n")

        f.write("## Verdict distribution\n\n")
        for v in ["relevant", "peripheral", "noise"]:
            f.write(f"- {v}: {verdicts.get(v, 0)}\n")
        if verdicts.get("unknown"):
            f.write(f"- unknown: {verdicts['unknown']}\n")

        if all_flags:
            f.write("\n## Flags\n\n")
            for flag in all_flags:
                f.write(f"- {flag}\n")

    if all_flags:
        for flag in all_flags:
            log.warning("FLAG: %s", flag)
        log.info("Verdict check: %d flag(s) raised → %s", len(all_flags),
                 report_path)
        sys.exit(1)
    else:
        log.info("Verdict check: clean (%d entries, %s)",
                 total, ", ".join(f"{v}={c}" for v, c in verdicts.items()))
        sys.exit(0)


if __name__ == "__main__":
    main()
