#!/usr/bin/env python3
"""Extract and classify links from jiracontext files into the manifest.

Scans artifacts/jiracontext/*.md bodies and the jiraexploration.md
pull_requests field, then writes three deduplicated lists into the
jiracontext.md frontmatter: pull_requests, code_repositories, and
additional_links.

Usage:
    python3 scripts/jira_context_links.py
    python3 scripts/jira_context_links.py --context-dir artifacts/jiracontext --manifest artifacts/jiracontext.md
"""

import argparse
import logging
import os
import re
import sys

import yaml

log = logging.getLogger("jira_context_links")

PR_URL_RE = re.compile(
    r'https?://(?:github\.com|gitlab[.\w]*)/[^\s"<>]+/pull/\d+'
    r'|https?://(?:github\.com|gitlab[.\w]*)/[^\s"<>]+/merge_requests/\d+')

REPO_URL_RE = re.compile(
    r'https?://(?:github\.com|gitlab[.\w]*)/[^/\s"<>]+/[^/\s"<>]+')

MD_LINK_RE = re.compile(r'\[[^\]]*\]\((https?://[^)]+)\)')

BARE_URL_RE = re.compile(r'(?<!\()(https?://[^\s)<>"]+)')


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _extract_body(path):
    """Return the markdown body after YAML frontmatter."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def _extract_urls_from_body(body):
    urls = set()
    for m in MD_LINK_RE.finditer(body):
        urls.add(m.group(1))
    for m in BARE_URL_RE.finditer(body):
        urls.add(m.group(1))
    return urls


def _strip_pr_annotation(entry):
    """Strip ' (JIRA-KEY: summary)' suffix from exploration PR entries."""
    m = re.match(r'^(https?://\S+)\s+\(', entry)
    return m.group(1) if m else entry.strip()


def _classify(url):
    """Return 'pr', 'repo', or 'other'."""
    if PR_URL_RE.match(url):
        return "pr"
    if REPO_URL_RE.match(url):
        return "repo"
    return "other"


def main():
    parser = argparse.ArgumentParser(
        description="Extract and classify links from jiracontext files.")
    parser.add_argument("--context-dir", default="artifacts/jiracontext",
                        help="Directory with context issue .md files")
    parser.add_argument("--manifest", default="artifacts/jiracontext.md",
                        help="Manifest to update")
    parser.add_argument("--exploration-manifest",
                        default="artifacts/jiraexploration.md",
                        help="Source for pre-collected pull_requests")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.path.isfile(args.manifest):
        log.error("Manifest not found: %s", args.manifest)
        sys.exit(1)

    all_urls = set()

    if os.path.isfile(args.exploration_manifest):
        explore_fm, _ = _parse_manifest(args.exploration_manifest)
        for entry in explore_fm.get("pull_requests", []):
            all_urls.add(_strip_pr_annotation(entry))
        log.info("Read %d PR entries from %s",
                 len(explore_fm.get("pull_requests", [])),
                 args.exploration_manifest)
    else:
        log.info("No exploration manifest at %s, skipping",
                 args.exploration_manifest)

    if os.path.isdir(args.context_dir):
        for name in sorted(os.listdir(args.context_dir)):
            if not name.endswith(".md"):
                continue
            body = _extract_body(os.path.join(args.context_dir, name))
            file_urls = _extract_urls_from_body(body)
            all_urls.update(file_urls)
            if file_urls:
                log.info("  %s: %d URLs", name, len(file_urls))

    prs = set()
    repos = set()
    additional = set()
    for url in all_urls:
        kind = _classify(url)
        if kind == "pr":
            prs.add(url)
        elif kind == "repo":
            repos.add(url)
        else:
            additional.add(url)

    for pr_url in prs:
        m = re.match(
            r'(https?://(?:github\.com|gitlab[.\w]*)/[^/\s"<>]+/[^/\s"<>]+)',
            pr_url)
        if m:
            repos.add(m.group(1))

    repos -= prs

    log.info("Classified: %d PRs, %d repos, %d additional",
             len(prs), len(repos), len(additional))

    fm, body = _parse_manifest(args.manifest)
    if prs:
        fm["pull_requests"] = sorted(prs)
    if repos:
        fm["code_repositories"] = sorted(repos)
    if additional:
        fm["additional_links"] = sorted(additional)

    with open(args.manifest, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(fm, default_flow_style=False, sort_keys=False))
        f.write("---\n\n")
        if body:
            f.write(body + "\n")

    log.info("Updated %s", args.manifest)


if __name__ == "__main__":
    main()
