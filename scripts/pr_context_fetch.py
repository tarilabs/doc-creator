#!/usr/bin/env python3
"""Fetch PR patches and metadata from GitHub using the gh CLI.

Reads pull_requests from a jiracontext.md manifest, downloads each PR's
patch and metadata via `gh pr view` / `gh pr diff`, and writes a
prcontext.md manifest summarising what was fetched.

Usage:
    python3 scripts/pr_context_fetch.py
    python3 scripts/pr_context_fetch.py --manifest artifacts/jiracontext.md --output-dir artifacts/prcontext
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import yaml

log = logging.getLogger("pr_context_fetch")

GITHUB_PR_RE = re.compile(
    r'^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)$')

GITLAB_MR_RE = re.compile(
    r'^https?://(?:gitlab[.\w]*)/.*/-/merge_requests/\d+$')


def parse_pr_url(url):
    """Extract (owner, repo, number) from a GitHub PR URL, or None."""
    m = GITHUB_PR_RE.match(url.strip())
    if m:
        return m.group("owner"), m.group("repo"), int(m.group("number"))
    return None


def pr_file_stem(owner, repo, number):
    """Build the file stem: owner__repo__number."""
    return f"{owner}__{repo}__{number}"


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _fetch_github_pr(url, owner, repo, number, raw_dir):
    """Fetch metadata and patch for a single GitHub PR. Returns a dict."""
    stem = pr_file_stem(owner, repo, number)
    patch_path = os.path.join(raw_dir, f"{stem}.patch")
    meta_path = os.path.join(raw_dir, f"{stem}.meta.yaml")

    if os.path.exists(patch_path) and os.path.exists(meta_path):
        log.info("  already fetched, skipping: %s", stem)
        with open(meta_path, encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        return {
            "url": url,
            "file": stem,
            "status": "fetched",
            "title": meta.get("title", ""),
            "additions": meta.get("additions", 0),
            "deletions": meta.get("deletions", 0),
        }

    entry = {"url": url, "file": stem}

    meta_cmd = [
        "gh", "pr", "view", url,
        "--json", "title,body,state,labels,additions,deletions,changedFiles,files",
    ]
    meta_result = subprocess.run(
        meta_cmd, capture_output=True, text=True, timeout=60)
    if meta_result.returncode != 0:
        log.error("  gh pr view failed for %s: %s",
                  url, meta_result.stderr.strip())
        entry["status"] = "failed"
        entry["error"] = meta_result.stderr.strip()[:200]
        return entry

    meta = json.loads(meta_result.stdout)
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)

    diff_cmd = ["gh", "pr", "diff", url, "--patch"]
    diff_result = subprocess.run(
        diff_cmd, capture_output=True, text=True, timeout=120)
    if diff_result.returncode != 0:
        log.error("  gh pr diff failed for %s: %s",
                  url, diff_result.stderr.strip())
        entry["status"] = "failed"
        entry["error"] = diff_result.stderr.strip()[:200]
        return entry

    with open(patch_path, "w", encoding="utf-8") as f:
        f.write(diff_result.stdout)

    log.info("  fetched: %s (%d additions, %d deletions)",
             stem, meta.get("additions", 0), meta.get("deletions", 0))

    entry["status"] = "fetched"
    entry["title"] = meta.get("title", "")
    entry["additions"] = meta.get("additions", 0)
    entry["deletions"] = meta.get("deletions", 0)
    return entry


def main():
    parser = argparse.ArgumentParser(
        description="Fetch PR patches and metadata from GitHub.")
    parser.add_argument("--manifest", default="artifacts/jiracontext.md",
                        help="Source manifest with pull_requests list")
    parser.add_argument("--output-dir", default="artifacts/prcontext",
                        help="Output directory for patches and manifest")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Max parallel PR fetches (default: 4)")
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
    pr_urls = fm.get("pull_requests", [])
    if not pr_urls:
        log.warning("No pull_requests in manifest")
        sys.exit(0)

    raw_dir = os.path.join(args.output_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    entries = [None] * len(pr_urls)
    failures = 0

    github_tasks = []
    for idx, url in enumerate(pr_urls):
        url = url.strip()
        parsed = parse_pr_url(url)
        if parsed:
            github_tasks.append((idx, url, *parsed))
        elif GITLAB_MR_RE.match(url):
            log.warning("Skipping GitLab MR (not yet supported): %s", url)
            entries[idx] = {
                "url": url,
                "file": None,
                "status": "skipped",
                "reason": "GitLab MR not yet supported",
            }
        else:
            log.warning("Unrecognised PR URL format: %s", url)
            entries[idx] = {
                "url": url,
                "file": None,
                "status": "skipped",
                "reason": "unrecognised URL format",
            }

    workers = min(args.concurrency, len(github_tasks)) if github_tasks else 1
    log.info("Fetching %d GitHub PRs with concurrency=%d", len(github_tasks), workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {}
        for idx, url, owner, repo, number in github_tasks:
            log.info("Queuing GitHub PR: %s/%s#%d", owner, repo, number)
            fut = pool.submit(_fetch_github_pr, url, owner, repo, number, raw_dir)
            future_to_idx[fut] = idx
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            entry = fut.result()
            entries[idx] = entry
            if entry["status"] == "failed":
                failures += 1

    manifest_path = os.path.normpath(args.output_dir) + ".md"
    manifest_fm = {
        "started_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "source_manifest": args.manifest,
        "output_directory": args.output_dir,
        "pull_requests": entries,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(manifest_fm, default_flow_style=False,
                          sort_keys=False, allow_unicode=True))
        f.write("---\n")

    fetched = sum(1 for e in entries if e["status"] == "fetched")
    skipped = sum(1 for e in entries if e["status"] == "skipped")
    log.info("Done: %d fetched, %d skipped, %d failed (of %d total)",
             fetched, skipped, failures, len(entries))

    if failures > 0 and fetched == 0:
        sys.exit(2)
    if failures > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
