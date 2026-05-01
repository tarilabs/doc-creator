#!/usr/bin/env python3
"""Generate the PR context report table from summary frontmatter.

Reads the prcontext manifest and per-PR summary files, extracts verdict
and gist from YAML frontmatter, and writes a markdown report table into
the manifest body.

Usage:
    python3 scripts/pr_context_report.py
    python3 scripts/pr_context_report.py --manifest artifacts/prcontext.md
"""

import argparse
import logging
import os
import re
import sys

import yaml

log = logging.getLogger("pr_context_report")

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)$")
_REPO_RE = re.compile(r"github\.com/([^/]+/[^/]+)")


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _parse_summary_frontmatter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        return {}
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def _pr_link(entry):
    url = entry.get("url", "")
    m = _PR_NUMBER_RE.search(url)
    number = m.group(1) if m else entry.get("file", "?")
    return f"[#{number}]({url})"


def _repo_from_entry(entry):
    url = entry.get("url", "")
    m = _REPO_RE.search(url)
    if m:
        return m.group(1)
    stem = entry.get("file", "")
    parts = stem.rsplit("__", 1)
    if len(parts) == 2:
        return parts[0].replace("__", "/")
    return stem


def _read_verdict_check_flags(output_dir):
    vc_path = os.path.join(output_dir, "verdict_check.md")
    if not os.path.isfile(vc_path):
        return []
    try:
        fm, body = _parse_manifest(vc_path)
    except (ValueError, OSError):
        return []
    if fm.get("status") != "flagged":
        return []
    flags = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("- "):
            flags.append(line[2:])
    return flags


def build_report(entries, output_dir):
    """Build the markdown report table from manifest entries and summary files.

    Returns (report_body, counts_dict).
    """
    counts = {"relevant": 0, "peripheral": 0, "noise": 0,
              "skipped": 0, "missing": 0}

    rows = []
    for entry in entries:
        status = entry.get("status", "")
        if status == "skipped":
            counts["skipped"] += 1
            continue

        stem = entry.get("file", "")
        summary_path = os.path.join(output_dir, f"{stem}.md")

        if not os.path.isfile(summary_path):
            counts["missing"] += 1
            continue

        sfm = _parse_summary_frontmatter(summary_path)
        verdict = sfm.get("verdict", "unknown")
        gist = sfm.get("gist", entry.get("title", ""))
        hint = entry.get("hint", "no-hint")
        repo = _repo_from_entry(entry)
        link = _pr_link(entry)

        if verdict in counts:
            counts[verdict] += 1

        rows.append({
            "link": link,
            "repo": repo,
            "verdict": verdict,
            "hint": hint,
            "gist": gist,
        })

    lines = []
    lines.append("| PR | Repo | Verdict | Hint | Gist |")
    lines.append("|---|---|---|---|---|")
    for row in rows:
        lines.append(
            f"| {row['link']} | {row['repo']} "
            f"| {row['verdict']} | {row['hint']} | {row['gist']} |"
        )

    total_fetched = counts["relevant"] + counts["peripheral"] + counts["noise"]
    parts = [f"{counts[v]} {v}" for v in ("relevant", "peripheral", "noise")]
    total_line = ", ".join(parts)
    if counts["skipped"]:
        total_line += f" ({counts['skipped']} skipped)"
    lines.append("")
    lines.append(f"**Totals:** {total_line}")

    flags = _read_verdict_check_flags(output_dir)
    if flags:
        lines.append("")
        lines.append("## Flags")
        lines.append("")
        for flag in flags:
            lines.append(f"- {flag}")

    return "\n".join(lines), counts


def main():
    parser = argparse.ArgumentParser(
        description="Generate PR context report table from summary frontmatter.")
    parser.add_argument("--manifest", default="artifacts/prcontext.md",
                        help="Path to the prcontext manifest")
    parser.add_argument("--output-dir", default=None,
                        help="Directory with summary .md files "
                             "(default: derived from manifest)")
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
    output_dir = (args.output_dir
                  or fm.get("output_directory")
                  or os.path.splitext(args.manifest)[0])
    entries = fm.get("pull_requests", [])

    report_body, counts = build_report(entries, output_dir)

    with open(args.manifest, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(fm, default_flow_style=False,
                          sort_keys=False, allow_unicode=True))
        f.write("---\n\n")
        f.write(report_body)
        f.write("\n")

    total = counts["relevant"] + counts["peripheral"] + counts["noise"]
    log.info("Report: %d relevant, %d peripheral, %d noise "
             "(of %d fetched, %d skipped)",
             counts["relevant"], counts["peripheral"], counts["noise"],
             total, counts["skipped"])


if __name__ == "__main__":
    main()
