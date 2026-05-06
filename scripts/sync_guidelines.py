#!/usr/bin/env python3
"""Sync IBM-SG and RH-SSG style guidelines from redhat-docs-agent-tools.

Copies the SKILL.md files from the ibm-sg-* and rh-ssg-* skill directories
into .claude/skills/guidelines/ with simplified frontmatter.

The upstream files use ``- [ ]`` checkbox syntax (GitHub-flavored markdown)
for each checklist item.  These render as interactive checkboxes in a browser
but carry no semantic value for an LLM agent — the agent reads the rule text,
it doesn't toggle checkboxes.  Stripping them to plain ``- `` reduces token
noise across ~200 checklist items and keeps the rubric concise per the
pipeline principle "be conservative in what you send."

Usage:
    python3 scripts/sync_guidelines.py
    python3 scripts/sync_guidelines.py --source /path/to/redhat-docs-agent-tools
"""

import argparse
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("sync_guidelines")

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent.parent / "redhat-docs-agent-tools"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "guidelines"

SKILL_PATTERNS = ["ibm-sg-*", "rh-ssg-*"]
SKILLS_SUBDIR = Path("plugins") / "docs-tools" / "skills"

STRIP_FIELDS = {"context", "name"}


def _rewrite_frontmatter(content):
    """Strip unnecessary frontmatter fields, keep description."""
    if not content.startswith("---\n"):
        return content

    parts = content.split("---\n", 2)
    if len(parts) < 3:
        return content

    fm_lines = parts[1].strip().split("\n")
    kept = []
    for line in fm_lines:
        field_match = re.match(r"^(\w[\w-]*):", line)
        if field_match and field_match.group(1) in STRIP_FIELDS:
            continue
        kept.append(line)

    if not kept:
        return parts[2]

    return "---\n" + "\n".join(kept) + "\n---\n" + parts[2]


def _strip_checkboxes(content):
    """Replace ``- [ ]`` checkbox markers with plain ``- `` list items."""
    return re.sub(r"^- \[ \] ", "- ", content, flags=re.MULTILINE)


def sync(source_root, output_dir):
    """Copy and transform guideline files."""
    skills_dir = source_root / SKILLS_SUBDIR
    if not skills_dir.is_dir():
        log.error("Skills directory not found: %s", skills_dir)
        return [], []

    output_dir.mkdir(parents=True, exist_ok=True)

    copied = []
    missing = []

    for pattern in SKILL_PATTERNS:
        matches = sorted(skills_dir.glob(pattern))
        if not matches:
            log.warning("No directories matching %s in %s", pattern, skills_dir)
            missing.append(pattern)
            continue

        for skill_dir in matches:
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                log.warning("No SKILL.md in %s", skill_dir)
                missing.append(str(skill_dir.name))
                continue

            content = skill_file.read_text(encoding="utf-8")
            content = _rewrite_frontmatter(content)
            content = _strip_checkboxes(content)

            out_name = f"{skill_dir.name}.md"
            out_path = output_dir / out_name
            out_path.write_text(content, encoding="utf-8")
            copied.append(out_name)
            log.info("Copied %s", out_name)

    return copied, missing


def main():
    parser = argparse.ArgumentParser(
        description="Sync style guidelines from redhat-docs-agent-tools."
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE,
        help=f"Path to redhat-docs-agent-tools repo (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--output", type=Path, default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.source.is_dir():
        log.error("Source repo not found: %s", args.source)
        sys.exit(2)

    copied, missing = sync(args.source, args.output)

    log.info("Synced %d guideline files to %s", len(copied), args.output)
    if missing:
        log.warning("Missing: %s", ", ".join(missing))

    if not copied:
        log.error("No guidelines copied — check source path")
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
