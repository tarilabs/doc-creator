#!/usr/bin/env python3
"""Prepare subagent prompt files for PR review batches.

Reads the prcontext manifest, writes noise summaries for empty patches,
groups remaining entries into batches, fills the prompt template, and
writes one prompt file per batch.

Outputs a JSON summary to stdout:
  {"batches": ["artifacts/prcontext/batch_0.prompt.md", ...], "noise_written": 2}

Usage:
    python3 scripts/pr_context_prepare.py
    python3 scripts/pr_context_prepare.py --manifest artifacts/prcontext.md
"""

import argparse
import json
import logging
import math
import os
import sys

import yaml

from pr_context_fetch import parse_pr_url

log = logging.getLogger("pr_context_prepare")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATE = os.path.join(
    SCRIPT_DIR, os.pardir,
    ".claude", "skills", "prcontext-populate", "prompt-template.md")


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def write_noise_summary(output_dir, file_key):
    """Write a verdict: noise summary for an entry whose patch was all noise."""
    path = os.path.join(output_dir, f"{file_key}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump({
            "verdict": "noise",
            "gist": "All changes were filtered as noise.",
        }, default_flow_style=False, sort_keys=False))
        f.write("---\n\n")
        f.write("All changes in this PR were removed by the noise filter "
                "(lock files, generated code, images, or whitespace-only).\n")
    return path


def build_pr_entry_block(entry, output_dir, index, batch_size):
    """Build the text block for one PR entry in a batch prompt."""
    url = entry["url"]
    parsed = parse_pr_url(url)
    if not parsed:
        return None
    owner, repo_name, pr_number = parsed
    repo = f"{owner}/{repo_name}"
    file_key = entry["file"]
    title = entry.get("title", "")
    hint_text = entry.get("hint_text", "(none)")

    abs_output_dir = os.path.abspath(output_dir)

    lines = [
        f"### PR {index} of {batch_size}",
        f'- pr_title: "{title}"',
        f"- pr_url: {url}",
        f"- repo: {repo}",
        f"- pr_number: {pr_number}",
        f"- meta_yaml_path: {abs_output_dir}/raw/{file_key}.meta.yaml",
        f"- filtered_patch_path: {abs_output_dir}/filtered/{file_key}.patch",
        f"- output_file: {abs_output_dir}/{file_key}.md",
        f"- hint_block: {hint_text}",
    ]
    return "\n".join(lines)


def group_into_batches(entries, max_batches=5):
    """Group entries into at most max_batches batches."""
    n = len(entries)
    if n == 0:
        return []
    batch_size = max(1, math.ceil(n / max_batches))
    batches = []
    for i in range(0, n, batch_size):
        batches.append(entries[i:i + batch_size])
    return batches


def main():
    parser = argparse.ArgumentParser(
        description="Prepare subagent prompt files for PR review batches.")
    parser.add_argument("--manifest", default="artifacts/prcontext.md",
                        help="Path to the prcontext manifest")
    parser.add_argument("--target", default="artifacts/jiracontext.md",
                        help="Path to the documentation target file")
    parser.add_argument("--template", default=None,
                        help="Path to the prompt template "
                             "(default: skill's prompt-template.md)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.path.isfile(args.manifest):
        log.error("Manifest not found: %s", args.manifest)
        sys.exit(2)

    template_path = args.template or os.path.normpath(DEFAULT_TEMPLATE)
    if not os.path.isfile(template_path):
        log.error("Prompt template not found: %s", template_path)
        sys.exit(2)

    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    fm, _body = _parse_manifest(args.manifest)
    output_dir = fm.get("output_directory") or os.path.splitext(args.manifest)[0]
    filtered_dir = os.path.join(output_dir, "filtered")

    entries = fm.get("pull_requests", [])
    fetched = [e for e in entries if e.get("status") == "fetched"]

    noise_written = 0
    to_evaluate = []

    for entry in fetched:
        file_key = entry.get("file", "")
        patch_path = os.path.join(filtered_dir, f"{file_key}.patch")
        if os.path.isfile(patch_path) and os.path.getsize(patch_path) == 0:
            write_noise_summary(output_dir, file_key)
            noise_written += 1
            log.info("Noise summary written for %s (empty patch)", file_key)
        else:
            to_evaluate.append(entry)

    batches = group_into_batches(to_evaluate)
    abs_target = os.path.abspath(args.target)
    batch_paths = []

    for batch_idx, batch in enumerate(batches):
        pr_blocks = []
        for entry_idx, entry in enumerate(batch, start=1):
            block = build_pr_entry_block(
                entry, output_dir, entry_idx, len(batch))
            if block:
                pr_blocks.append(block)

        pr_entries_text = "\n\n".join(pr_blocks)
        prompt = template.replace(
            "{documentation_target_file}", abs_target
        ).replace(
            "{pr_entries}", pr_entries_text
        )

        batch_file = os.path.join(output_dir, f"batch_{batch_idx}.prompt.md")
        with open(batch_file, "w", encoding="utf-8") as f:
            f.write(prompt)
        batch_paths.append(batch_file)
        log.info("Wrote batch %d: %d PRs → %s",
                 batch_idx, len(batch), batch_file)

    result = {"batches": batch_paths, "noise_written": noise_written}
    print(json.dumps(result))

    log.info("Prepared %d batch(es), %d noise summary(ies)",
             len(batch_paths), noise_written)


if __name__ == "__main__":
    main()
