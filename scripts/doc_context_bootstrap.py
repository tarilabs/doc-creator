#!/usr/bin/env python3
"""Bootstrap a doccontext manifest by consolidating jiracontext, prcontext, and codecontext.

Reads the jiracontext.md manifest and merges references from all three
upstream context phases into a single artifacts/doccontext.md manifest.
For each phase it resolves what actually exists on disk:

- jira_issues: scans artifacts/jiracontext/*.md for jira_key frontmatter
- code_repositories: cross-references jiracontext repo URLs with cloned
  directories in artifacts/codecontext/
- pull_requests: enriches prcontext.md entries with verdict and gist from
  per-PR summary files

The documentation target body is copied verbatim from jiracontext.md.

Files read (not modified)
    artifacts/jiracontext.md — source manifest
    artifacts/jiracontext/*.md — JIRA issue files (read frontmatter for jira_key)
    artifacts/prcontext.md — PR manifest (read pull_requests list for url, file, title, status)
    artifacts/prcontext/*.md — PR summary files (read frontmatter for verdict)
    artifacts/codecontext/ — directory listing to discover cloned repos
File written
    artifacts/doccontext.md — the consolidated manifest

Usage:
    python3 scripts/doc_context_bootstrap.py
    python3 scripts/doc_context_bootstrap.py --jiracontext-manifest artifacts/jiracontext.md
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

log = logging.getLogger("doc_context_bootstrap")

DEFAULT_JIRACONTEXT_MANIFEST = "artifacts/jiracontext.md"
DEFAULT_PRCONTEXT_MANIFEST = "artifacts/prcontext.md"
DEFAULT_CODECONTEXT_DIR = "artifacts/codecontext"
DEFAULT_OUTPUT = "artifacts/doccontext.md"


def _parse_manifest(manifest_path):
    """Read a manifest markdown file and return (frontmatter_dict, body_str)."""
    with open(manifest_path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{manifest_path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _build_jira_issues(jiracontext_dir):
    """Scan jiracontext directory and return list of {key, path} dicts."""
    issues = []
    for md_file in sorted(Path(jiracontext_dir).glob("*.md")):
        try:
            fm, _ = _parse_manifest(md_file)
        except (ValueError, Exception):
            log.warning("Skipping %s: cannot parse frontmatter", md_file.name)
            continue
        jira_key = fm.get("jira_key")
        if not jira_key:
            log.warning("Skipping %s: no jira_key in frontmatter", md_file.name)
            continue
        issues.append({"key": jira_key, "path": str(md_file)})
    return issues


def _repo_url_to_dirname(url):
    """Convert a repo URL to the codecontext directory name convention (owner--repo)."""
    url = url.rstrip("/")
    parts = url.split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}--{parts[-1]}"
    return None


def _build_code_repositories(repo_urls, codecontext_dir):
    """Return list of {url, path} for repos that were actually cloned."""
    repos = []
    cc_path = Path(codecontext_dir)
    for url in repo_urls:
        dirname = _repo_url_to_dirname(url)
        if dirname and (cc_path / dirname).is_dir():
            repos.append({"url": url, "path": str(cc_path / dirname)})
        else:
            log.info("Skipping repo %s: not found in %s", url, codecontext_dir)
    return repos


def _build_pull_requests(pr_manifest_fm, prcontext_dir):
    """Build enriched PR list from prcontext manifest + per-PR summary files."""
    entries = pr_manifest_fm.get("pull_requests", [])
    prs = []
    for entry in entries:
        file_stem = entry.get("file")
        if not file_stem:
            continue

        summary_path = Path(prcontext_dir) / f"{file_stem}.md"
        verdict = None
        gist = None
        if summary_path.exists():
            try:
                sfm, _ = _parse_manifest(summary_path)
                verdict = sfm.get("verdict")
                gist = sfm.get("gist")
            except (ValueError, Exception):
                log.warning("Cannot parse summary for %s", file_stem)

        pr_obj = {
            "url": entry.get("url"),
            "title": entry.get("title", ""),
            "verdict": verdict,
            "gist": gist,
            "filtered_patch": str(Path(prcontext_dir) / "filtered" / f"{file_stem}.patch"),
            "raw_metadata": str(Path(prcontext_dir) / "raw" / f"{file_stem}.meta.yaml"),
        }
        prs.append(pr_obj)
    return prs


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap a doccontext manifest from jiracontext, prcontext, and codecontext."
    )
    parser.add_argument(
        "--jiracontext-manifest", default=DEFAULT_JIRACONTEXT_MANIFEST,
        help=f"Path to jiracontext manifest (default: {DEFAULT_JIRACONTEXT_MANIFEST})",
    )
    parser.add_argument(
        "--prcontext-manifest", default=DEFAULT_PRCONTEXT_MANIFEST,
        help=f"Path to prcontext manifest (default: {DEFAULT_PRCONTEXT_MANIFEST})",
    )
    parser.add_argument(
        "--codecontext-dir", default=DEFAULT_CODECONTEXT_DIR,
        help=f"Path to codecontext directory (default: {DEFAULT_CODECONTEXT_DIR})",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT,
        help=f"Output manifest path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not Path(args.jiracontext_manifest).exists():
        log.error("Jiracontext manifest not found: %s", args.jiracontext_manifest)
        sys.exit(2)

    jc_fm, body = _parse_manifest(args.jiracontext_manifest)
    jiracontext_dir = jc_fm.get("output_directory", "artifacts/jiracontext")

    jira_issues = _build_jira_issues(jiracontext_dir)
    log.info("Found %d JIRA issues in %s", len(jira_issues), jiracontext_dir)

    repo_urls = jc_fm.get("code_repositories", [])
    code_repos = _build_code_repositories(repo_urls, args.codecontext_dir)
    log.info("Found %d cloned code repositories in %s", len(code_repos), args.codecontext_dir)

    prs = []
    if Path(args.prcontext_manifest).exists():
        pr_fm, _ = _parse_manifest(args.prcontext_manifest)
        prcontext_dir = pr_fm.get("output_directory", "artifacts/prcontext")
        prs = _build_pull_requests(pr_fm, prcontext_dir)
        log.info("Found %d pull requests in %s", len(prs), args.prcontext_manifest)
    else:
        log.warning("No prcontext manifest at %s, skipping PRs", args.prcontext_manifest)

    fm = {
        "starting_issue": jc_fm.get("starting_issue"),
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rhaistrat": jc_fm.get("rhaistrat"),
        "jira_issues": jira_issues,
        "code_repositories": code_repos,
        "pull_requests": prs,
        "additional_links": jc_fm.get("additional_links", []),
    }

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True))
        f.write("---\n\n")
        if body:
            f.write(body + "\n")

    log.info("Wrote %s", args.output)


if __name__ == "__main__":
    main()
