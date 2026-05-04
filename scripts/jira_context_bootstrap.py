#!/usr/bin/env python3
"""Bootstrap a jiracontext directory: create the output dir, copy the
starting issue file, and write a jiracontext.md manifest.

The remaining issue files are NOT copied — a downstream skill/agent
decides which ones add documentation value and copies them in.

Usage:
    python3 scripts/jira_context_bootstrap.py
    python3 scripts/jira_context_bootstrap.py --input-dir artifacts/jiraexploration --output-dir artifacts/jiracontext
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime, timezone

import yaml

log = logging.getLogger("jira_context_bootstrap")

DEFAULT_INPUT_DIR = "artifacts/jiraexploration"
DEFAULT_OUTPUT_DIR = "artifacts/jiracontext"


def _parse_manifest(manifest_path):
    """Read a manifest markdown file and return (frontmatter_dict, body_str)."""
    with open(manifest_path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{manifest_path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap a jiracontext directory with the starting "
                    "issue and a manifest."
    )
    parser.add_argument("--input-dir", default=DEFAULT_INPUT_DIR,
                        help="Source directory with downloaded issue files "
                             f"(default: {DEFAULT_INPUT_DIR})")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="Destination directory for jiracontext "
                             f"(default: {DEFAULT_OUTPUT_DIR})")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    input_dir = args.input_dir
    output_dir = args.output_dir

    input_manifest = os.path.join(os.path.dirname(input_dir),
                                  "jiraexploration.md")
    output_manifest = os.path.join(os.path.dirname(output_dir),
                                   "jiracontext.md")

    if not os.path.isdir(input_dir):
        log.error("Input directory does not exist: %s", input_dir)
        sys.exit(1)
    if not os.path.isfile(input_manifest):
        log.error("Input manifest does not exist: %s", input_manifest)
        sys.exit(1)

    fm, body = _parse_manifest(input_manifest)
    log.info("Read manifest from %s (starting_issue=%s)",
             input_manifest, fm.get("starting_issue"))

    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    starting_issue = fm.get("starting_issue")
    if starting_issue:
        src = os.path.join(input_dir, f"{starting_issue}.md")
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(output_dir,
                                           f"{starting_issue}.md"))
            log.info("Copied starting issue %s.md", starting_issue)
        else:
            log.warning("Starting issue file not found: %s", src)

    log.info("Created %s", output_dir)

    ctx_fm = {
        "starting_issue": fm.get("starting_issue"),
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "output_directory": output_dir,
    }
    if fm.get("rhaistrat") is not None:
        ctx_fm["rhaistrat"] = fm["rhaistrat"]
    else:
        ctx_fm["rhaistrat"] = None
    if "hierarchy" in fm:
        ctx_fm["hierarchy"] = fm["hierarchy"]

    with open(output_manifest, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(ctx_fm, default_flow_style=False, sort_keys=False))
        f.write("---\n\n")
        if body:
            f.write(body + "\n")

    log.info("Wrote manifest: %s", output_manifest)
    log.info("Done — %s", output_dir)


if __name__ == "__main__":
    main()
