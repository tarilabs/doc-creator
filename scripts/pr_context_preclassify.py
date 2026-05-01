#!/usr/bin/env python3
"""Deterministic pre-classification of PR entries before LLM summarisation.

Reads the prcontext manifest and adds a ``hint`` field to each fetched
entry based on title patterns and file-level analysis.  The hint is
advisory — the LLM subagent may override it.

Usage:
    python3 scripts/pr_context_preclassify.py
    python3 scripts/pr_context_preclassify.py --manifest artifacts/prcontext.md
"""

import argparse
import logging
import os
import re
import sys

import yaml

from pr_context_filter import TEST_GLOBS, _is_test

log = logging.getLogger("pr_context_preclassify")

# ── Title-based heuristics ───────────────────────────────────────────────────

PERIPHERAL_TITLE_PREFIXES = [
    "fix:", "fix(",
    "test:", "test(",
    "chore:", "chore(",
    "refactor:", "refactor(",
]

PERIPHERAL_TITLE_PATTERNS = [
    re.compile(r"address review comments", re.IGNORECASE),
    re.compile(r"review feedback", re.IGNORECASE),
]

REVERT_RE = re.compile(r"\brevert\b", re.IGNORECASE)


def classify_by_title(title):
    """Return (hint, reason) based on PR title, or (None, None)."""
    lower = title.lower().strip()
    for prefix in PERIPHERAL_TITLE_PREFIXES:
        if lower.startswith(prefix):
            return "candidate-peripheral", f"title prefix {prefix}"
    for pattern in PERIPHERAL_TITLE_PATTERNS:
        if pattern.search(title):
            return "candidate-peripheral", f"title contains '{pattern.pattern}'"
    if REVERT_RE.search(title):
        return "candidate-peripheral", "title contains revert"
    return None, None


def classify_by_files(meta_path, filtered_patch_path):
    """Return (hint, reason) based on file-level analysis, or (None, None).

    Checks:
    1. If the filtered patch is 0 bytes → candidate-noise.
    2. If ALL changed files in meta.yaml match TEST_GLOBS → candidate-peripheral.
    """
    if (os.path.isfile(filtered_patch_path)
            and os.path.getsize(filtered_patch_path) == 0):
        return "candidate-noise", "filtered patch empty"

    if not os.path.isfile(meta_path):
        return None, None

    with open(meta_path, encoding="utf-8") as f:
        meta = yaml.safe_load(f)

    files = meta.get("files") or []
    if not files:
        return None, None

    paths = [entry.get("path", "") for entry in files if isinstance(entry, dict)]
    if not paths:
        return None, None

    if all(_is_test(p) for p in paths):
        return "candidate-peripheral", "all files match test globs"

    return None, None


def expand_hint_text(hint, hint_reason):
    """Expand a hint/reason pair into the full text shown to the reviewer."""
    if not hint or hint == "no-hint":
        return None
    if hint == "candidate-peripheral":
        return (
            f"DETERMINISTIC HINT: This PR's metadata suggests it is "
            f"peripheral (reason: {hint_reason}). Evaluate this critically "
            f"— override if the PR genuinely changes documented behavior."
        )
    if hint == "candidate-noise":
        return (
            f"DETERMINISTIC HINT: This PR's metadata suggests it is "
            f"noise (reason: {hint_reason}). Evaluate this critically."
        )
    return None


def classify_entry(entry, raw_dir, filtered_dir):
    """Return (hint, hint_reason) for a single manifest entry."""
    if entry.get("status") != "fetched":
        return "no-hint", None

    stem = entry.get("file", "")
    meta_path = os.path.join(raw_dir, f"{stem}.meta.yaml")
    filtered_path = os.path.join(filtered_dir, f"{stem}.patch")

    file_hint, file_reason = classify_by_files(meta_path, filtered_path)
    if file_hint == "candidate-noise":
        return file_hint, file_reason
    if file_hint == "candidate-peripheral":
        return file_hint, file_reason

    title = entry.get("title", "")
    title_hint, title_reason = classify_by_title(title)
    if title_hint:
        return title_hint, title_reason

    return "no-hint", None


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def main():
    parser = argparse.ArgumentParser(
        description="Pre-classify PR entries with deterministic hints.")
    parser.add_argument("--manifest", default="artifacts/prcontext.md",
                        help="Path to the prcontext manifest")
    parser.add_argument("--raw-dir", default=None,
                        help="Directory with raw .meta.yaml files "
                             "(default: {manifest_parent}/raw)")
    parser.add_argument("--filtered-dir", default=None,
                        help="Directory with filtered .patch files "
                             "(default: {manifest_parent}/filtered)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.path.isfile(args.manifest):
        log.error("Manifest not found: %s", args.manifest)
        sys.exit(1)

    fm, body = _parse_manifest(args.manifest)

    data_dir = fm.get("output_directory") or os.path.splitext(args.manifest)[0]
    raw_dir = args.raw_dir or os.path.join(data_dir, "raw")
    filtered_dir = args.filtered_dir or os.path.join(data_dir, "filtered")

    entries = fm.get("pull_requests", [])
    counts = {"candidate-peripheral": 0, "candidate-noise": 0, "no-hint": 0}

    for entry in entries:
        hint, reason = classify_entry(entry, raw_dir, filtered_dir)
        entry["hint"] = hint
        if reason:
            entry["hint_reason"] = reason
        hint_text = expand_hint_text(hint, reason)
        if hint_text:
            entry["hint_text"] = hint_text
        else:
            entry.pop("hint_text", None)
        counts[hint] = counts.get(hint, 0) + 1
        if hint != "no-hint":
            log.info("%s → %s (%s)", entry.get("file", "?"), hint, reason)

    with open(args.manifest, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(fm, default_flow_style=False,
                          sort_keys=False, allow_unicode=True))
        f.write("---\n")
        if body:
            f.write(body + "\n")

    log.info("Pre-classified %d entries: %d candidate-peripheral, "
             "%d candidate-noise, %d no-hint",
             len(entries), counts["candidate-peripheral"],
             counts["candidate-noise"], counts["no-hint"])


if __name__ == "__main__":
    main()
