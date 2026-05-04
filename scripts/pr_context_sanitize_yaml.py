#!/usr/bin/env python3
"""Sanitize YAML frontmatter in PR summary files.

Fixes unquoted string values containing colons, which break
yaml.safe_load(). Runs after subagents write summaries (Step 5)
and before the verdict check (Step 6).

Usage:
    python3 scripts/pr_context_sanitize_yaml.py
    python3 scripts/pr_context_sanitize_yaml.py --manifest artifacts/prcontext.md
"""

import argparse
import logging
import os
import re
import sys

import yaml

log = logging.getLogger("pr_context_sanitize_yaml")

YAML_LINE = re.compile(r"^([a-z_]+): (.+)$")


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def fix_frontmatter_line(line):
    """Quote a YAML value if it contains a colon and isn't already quoted."""
    m = YAML_LINE.match(line)
    if not m:
        return line
    key, value = m.group(1), m.group(2)
    if value.startswith('"') or value.startswith("'"):
        return line
    if ":" not in value:
        return line
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}: "{escaped}"'


def sanitize_file(path):
    """Fix YAML frontmatter quoting in a single summary file.

    Returns the key(s) that were repaired, or an empty list if clean.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        return []

    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return []

    fm_lines = parts[1].rstrip("\n").split("\n")
    repaired_keys = []
    new_lines = []
    for line in fm_lines:
        fixed = fix_frontmatter_line(line)
        if fixed != line:
            m = YAML_LINE.match(line)
            if m:
                repaired_keys.append(m.group(1))
        new_lines.append(fixed)

    if not repaired_keys:
        return []

    new_fm = "\n".join(new_lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(new_fm)
        f.write("---\n")
        f.write(parts[2])

    return repaired_keys


def main():
    parser = argparse.ArgumentParser(
        description="Sanitize YAML frontmatter in PR summary files.")
    parser.add_argument("--manifest", default="artifacts/prcontext.md",
                        help="Path to the prcontext manifest")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.path.isfile(args.manifest):
        log.error("Manifest not found: %s", args.manifest)
        sys.exit(2)

    fm, _body = _parse_manifest(args.manifest)
    output_dir = fm.get("output_directory") or os.path.splitext(args.manifest)[0]

    entries = fm.get("pull_requests", [])
    fetched = [e for e in entries if e.get("status") == "fetched"]

    total_repaired = 0
    for entry in fetched:
        file_key = entry.get("file", "")
        summary_path = os.path.join(output_dir, f"{file_key}.md")
        if not os.path.isfile(summary_path):
            continue
        repaired = sanitize_file(summary_path)
        if repaired:
            total_repaired += 1
            log.info("Repaired %s: %s", file_key, ", ".join(repaired))

    log.info("Sanitized %d of %d summary files",
             total_repaired, len(fetched))


if __name__ == "__main__":
    main()
