#!/usr/bin/env python3
"""Filter noise hunks from raw PR patches.

Reads .patch files from a raw/ directory, strips hunks that match known
noise patterns (lock files, generated code, CI configs, images, and
whitespace-only changes), and writes the surviving hunks to a filtered/
directory.

Usage:
    python3 scripts/pr_context_filter.py
    python3 scripts/pr_context_filter.py --input-dir artifacts/prcontext/raw --output-dir artifacts/prcontext/filtered
"""

import argparse
import fnmatch
import logging
import os
import re
import sys

log = logging.getLogger("pr_context_filter")

# ── Noise patterns ────────────────────────────────────────────────────────────
# Each entry is a glob matched against the file path in the diff header.

NOISE_GLOBS = [
    # Lock / dependency files
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "go.sum", "poetry.lock", "uv.lock", "Pipfile.lock",
    "Cargo.lock", "Gemfile.lock", "composer.lock",
    # Generated / vendored
    "*.generated.*", "*_generated.*", "*_generated_*",
    "vendor/*", "node_modules/*",
    # Images and binary assets
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.svg",
    "*.woff", "*.woff2", "*.ttf", "*.eot",
    # Misc noise
    "*.map", "*.min.js", "*.min.css",
]

TEST_GLOBS = [
    "*_test.go", "test_*.py", "*_test.py",
    "*.spec.ts", "*.spec.tsx", "*.spec.js", "*.spec.jsx",
    "*.test.ts", "*.test.tsx", "*.test.js", "*.test.jsx",
    "__tests__/*", "tests/*", "test/*",
    "**/testdata/*", "**/fixtures/*",
]

DIFF_HEADER_RE = re.compile(r'^diff --git a/(.*) b/(.*)$')


def _is_noise(filepath):
    """Check if a file path matches any noise glob."""
    basename = os.path.basename(filepath)
    for pattern in NOISE_GLOBS:
        if fnmatch.fnmatch(basename, pattern):
            return True
        if fnmatch.fnmatch(filepath, pattern):
            return True
    return False


def _is_test(filepath):
    """Check if a file path matches any test glob."""
    basename = os.path.basename(filepath)
    for pattern in TEST_GLOBS:
        if fnmatch.fnmatch(basename, pattern):
            return True
        if fnmatch.fnmatch(filepath, pattern):
            return True
    return False


def _normalize_ws(s):
    """Collapse all whitespace to single spaces for comparison."""
    return " ".join(s.split())


def _is_whitespace_only(hunk_lines):
    """True if a hunk's added and removed lines differ only in whitespace."""
    added = []
    removed = []
    for line in hunk_lines:
        if line.startswith("+") and not line.startswith("+++"):
            added.append(_normalize_ws(line[1:]))
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(_normalize_ws(line[1:]))
    return added == removed


def parse_patch(text):
    """Parse a unified diff into a list of file entries.

    Each entry is a dict:
      - header: list of lines from 'diff --git' up to first hunk
      - filepath: the b/ path from the diff header
      - hunks: list of hunk dicts, each with 'header' (str) and 'lines' (list)
    """
    entries = []
    current = None
    current_hunk = None

    for line in text.splitlines(keepends=True):
        line_stripped = line.rstrip("\n")

        m = DIFF_HEADER_RE.match(line_stripped)
        if m:
            if current_hunk and current:
                current["hunks"].append(current_hunk)
                current_hunk = None
            if current:
                entries.append(current)
            current = {
                "header": [line],
                "filepath": m.group(2),
                "hunks": [],
            }
            current_hunk = None
            continue

        if current is None:
            continue

        if line_stripped.startswith("@@"):
            if current_hunk:
                current["hunks"].append(current_hunk)
            current_hunk = {"header": line, "lines": []}
            continue

        if current_hunk is not None:
            current_hunk["lines"].append(line)
        else:
            current["header"].append(line)

    if current_hunk and current:
        current["hunks"].append(current_hunk)
    if current:
        entries.append(current)

    return entries


def filter_patch(entries):
    """Filter parsed patch entries. Returns (kept, stats).

    If the PR is test-only (every file is a test), test files are kept.
    Otherwise, test files are dropped along with noise files.
    """
    all_test = all(_is_test(e["filepath"]) for e in entries) if entries else False

    kept = []
    stats = {"noise": 0, "test": 0, "whitespace": 0, "kept": 0}

    for entry in entries:
        filepath = entry["filepath"]

        if _is_noise(filepath):
            stats["noise"] += len(entry["hunks"])
            continue

        if _is_test(filepath) and not all_test:
            stats["test"] += len(entry["hunks"])
            continue

        surviving_hunks = []
        for hunk in entry["hunks"]:
            if _is_whitespace_only(hunk["lines"]):
                stats["whitespace"] += 1
            else:
                surviving_hunks.append(hunk)

        if surviving_hunks:
            kept.append({
                "header": entry["header"],
                "filepath": entry["filepath"],
                "hunks": surviving_hunks,
            })
            stats["kept"] += len(surviving_hunks)

    return kept, stats


def render_patch(entries):
    """Render filtered entries back to unified diff text."""
    parts = []
    for entry in entries:
        parts.extend(entry["header"])
        for hunk in entry["hunks"]:
            parts.append(hunk["header"])
            parts.extend(hunk["lines"])
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Filter noise hunks from raw PR patches.")
    parser.add_argument("--input-dir", default="artifacts/prcontext/raw",
                        help="Directory with raw .patch files")
    parser.add_argument("--output-dir", default="artifacts/prcontext/filtered",
                        help="Directory for filtered .patch files")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.path.isdir(args.input_dir):
        log.error("Input directory not found: %s", args.input_dir)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    patch_files = sorted(
        f for f in os.listdir(args.input_dir) if f.endswith(".patch"))

    if not patch_files:
        log.warning("No .patch files found in %s", args.input_dir)
        sys.exit(0)

    for name in patch_files:
        input_path = os.path.join(args.input_dir, name)
        output_path = os.path.join(args.output_dir, name)

        with open(input_path, encoding="utf-8", errors="replace") as f:
            text = f.read()

        entries = parse_patch(text)
        kept, stats = filter_patch(entries)

        total_original = sum(len(e["hunks"]) for e in entries)
        dropped_parts = []
        if stats["noise"]:
            dropped_parts.append(f"{stats['noise']} noise")
        if stats["test"]:
            dropped_parts.append(f"{stats['test']} test")
        if stats["whitespace"]:
            dropped_parts.append(f"{stats['whitespace']} whitespace")
        dropped_str = ", ".join(dropped_parts) if dropped_parts else "none"

        if kept:
            filtered_text = render_patch(kept)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(filtered_text)
        else:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("")

        log.info("%s: %d → %d hunks (dropped: %s)",
                 name, total_original, stats["kept"], dropped_str)

    log.info("Filtered %d patches to %s", len(patch_files), args.output_dir)


if __name__ == "__main__":
    main()
