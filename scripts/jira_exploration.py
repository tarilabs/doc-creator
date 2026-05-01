#!/usr/bin/env python3
"""Download JIRA issues and save them as markdown to artifacts/jiraexploration/.

Usage:
    python3 scripts/jira_exploration.py RHOAIENG-53404 RHOAIENG-60547
    python3 scripts/jira_exploration.py RHAISTRAT-1084 --link-filter RHOAI
    python3 scripts/jira_exploration.py RHOAIENG-53404 --output-dir /tmp/jiras
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import json
import re

import yaml

from fetch_issue import _fetch_all, _extract_urls_from_adf, FIELD_GIT_PULL_REQUEST
from jira_utils import require_env, get_issue, search_issues

log = logging.getLogger("jira_exploration")

_PR_URL_RE = re.compile(r'https?://(?:github\.com|gitlab[.\w]*)/[^\s"<>]+/pull/\d+|'
                         r'https?://(?:github\.com|gitlab[.\w]*)/[^\s"<>]+/merge_requests/\d+')


def _extract_urls_from_description(desc_adf):
    """Scan an ADF description for GitHub/GitLab PR/MR URLs."""
    if not isinstance(desc_adf, dict):
        return []
    return _PR_URL_RE.findall(json.dumps(desc_adf))


def _collect_strat_prs(strat_key, output_dir, server, user, token):
    """Detect Epic children from disk, batch-fetch grandchildren PRs.

    Returns a list of (task_key, summary, [url, ...]) tuples.
    """
    # 1. Read downloaded files to find Epics (zero API calls)
    epic_keys = []
    for filename in os.listdir(output_dir):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(output_dir, filename)
        with open(filepath, encoding="utf-8") as f:
            text = f.read()
        if not text.startswith("---\n"):
            continue
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            continue
        try:
            fm = yaml.safe_load(parts[1])
        except yaml.YAMLError:
            continue
        if fm and fm.get("issue_type") == "Epic":
            epic_keys.append(fm["jira_key"])

    if not epic_keys:
        log.info("No Epic children found for %s, skipping PR collection",
                 strat_key)
        return []

    log.info("Found %d Epic children: %s", len(epic_keys),
             ", ".join(epic_keys))

    # 2. Batch-fetch all grandchildren with PRs + descriptions
    gc_fields = ["summary", "description", FIELD_GIT_PULL_REQUEST]
    grandchildren = []
    try:
        jql = f"parent in ({','.join(epic_keys)}) ORDER BY key ASC"
        grandchildren = search_issues(server, user, token, jql,
                                      fields=gc_fields, max_results=500)
        log.info("Fetched %d grandchildren in 1 batch call", len(grandchildren))
    except Exception:
        log.warning("Batch parent-in query not supported, falling back to "
                    "per-Epic queries")
        for ek in epic_keys:
            batch = search_issues(
                server, user, token,
                f"parent = {ek} ORDER BY key ASC",
                fields=gc_fields, max_results=200)
            grandchildren.extend(batch)
        log.info("Fetched %d grandchildren across %d Epic queries",
                 len(grandchildren), len(epic_keys))

    # 3. Extract PR URLs from each grandchild
    results = []
    for gc in grandchildren:
        key = gc.get("key", "")
        fields = gc.get("fields", {})
        summary = fields.get("summary", "")
        urls = set()

        # From Git Pull Request custom field
        urls.update(_extract_urls_from_adf(
            fields.get(FIELD_GIT_PULL_REQUEST)))

        # From description ADF
        urls.update(_extract_urls_from_description(
            fields.get("description")))

        if urls:
            results.append((key, summary, sorted(urls)))

    log.info("Found %d tasks with PR URLs (%d unique URLs total)",
             len(results),
             len({u for _, _, urls in results for u in urls}))
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Download JIRA issues and linked/child issues as markdown."
    )
    parser.add_argument("issue_key",
                        help="JIRA issue key to fetch (e.g. RHOAIENG-53404).")
    parser.add_argument("--output-dir", default="artifacts/jiraexploration",
                        help="Output directory (default: artifacts/jiraexploration)")
    parser.add_argument("--link-filter", default="UX",
                        help="Only download linked issues whose key contains "
                             "this substring (default: UX). "
                             "Pass empty string to download all links.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    server, user, token = require_env()
    if not all([server, user, token]):
        log.error("JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.")
        sys.exit(2)

    log.info("Starting exploration from %s", args.issue_key)

    os.makedirs(args.output_dir, exist_ok=True)
    manifest = os.path.join(os.path.dirname(args.output_dir),
                            "jiraexploration.md")

    # Walk up the parent chain to find the RHAISTRAT ancestor
    is_strat = args.issue_key.startswith("RHAISTRAT-")
    chain = []
    if is_strat:
        log.info("%s is already a RHAISTRAT, skipping parent walk",
                 args.issue_key)
    else:
        log.info("Walking up parent chain from %s ...", args.issue_key)
        current = args.issue_key
        for _ in range(10):
            try:
                issue = get_issue(server, user, token, current,
                                  fields=["summary", "parent"])
            except Exception:
                log.warning("Failed to fetch %s, stopping parent walk",
                            current)
                break
            parent = issue.get("fields", {}).get("parent")
            if not parent:
                log.info("%s has no parent, end of chain", current)
                break
            parent_key = parent.get("key", "")
            parent_summary = parent.get("fields", {}).get("summary", "")
            chain.append((parent_key, parent_summary))
            log.info("  %s → %s (%s)", current, parent_key, parent_summary)
            if parent_key.startswith("RHAISTRAT-"):
                log.info("Found RHAISTRAT ancestor: %s", parent_key)
                break
            current = parent_key

    strat_key = None
    if is_strat:
        strat_key = args.issue_key
    elif chain and chain[-1][0].startswith("RHAISTRAT-"):
        strat_key = chain[-1][0]
    else:
        log.warning("No RHAISTRAT ancestor found in parent chain")

    # Write manifest header
    with open(manifest, "w", encoding="utf-8") as f:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.write("# JIRA Exploration\n\n")
        f.write(f"- **Starting issue**: {args.issue_key}\n")
        f.write(f"- **Started at**: {ts}\n")
        f.write(f"- **Link filter**: {args.link_filter or '(none)'}\n")
        f.write(f"- **Output directory**: {args.output_dir}\n")
        if is_strat:
            f.write(f"- **RHAISTRAT**: {args.issue_key} (starting issue is a STRAT)\n")
        elif strat_key:
            hierarchy = " → ".join(
                [args.issue_key] + [k for k, _ in chain])
            f.write(f"- **RHAISTRAT**: {strat_key}\n")
            f.write(f"- **Hierarchy**: {hierarchy}\n")
        else:
            hierarchy = " → ".join(
                [args.issue_key] + [k for k, _ in chain])
            f.write(f"- **RHAISTRAT**: not found\n")
            f.write(f"- **Hierarchy**: {hierarchy} (no RHAISTRAT ancestor)\n")
    log.info("Wrote manifest: %s", manifest)

    # Fetch the RHAISTRAT if found and different from the starting issue
    if strat_key and strat_key != args.issue_key:
        log.info("Fetching RHAISTRAT %s and its children/links ...", strat_key)
        rc = _fetch_all(strat_key, args.output_dir, server, user, token,
                        link_filter=args.link_filter)
        if rc != 0:
            sys.exit(rc)

    # Fetch the starting issue
    log.info("Fetching starting issue %s and its children/links ...",
             args.issue_key)
    rc = _fetch_all(args.issue_key, args.output_dir, server, user, token,
                    link_filter=args.link_filter)
    if rc != 0:
        sys.exit(rc)

    # Append the starting issue description to the manifest
    issue_md = os.path.join(args.output_dir, f"{args.issue_key}.md")
    if os.path.isfile(issue_md):
        with open(issue_md, encoding="utf-8") as f:
            text = f.read()
        parts = text.split("---\n", 2)
        body = parts[2].strip() if len(parts) >= 3 else text.strip()
        with open(manifest, "a", encoding="utf-8") as f:
            f.write(f"\n## {args.issue_key}\n\n")
            f.write(body + "\n")

    # Collect PR URLs from Epic grandchildren
    if strat_key:
        log.info("Collecting PR URLs from Epic grandchildren of %s ...",
                 strat_key)
        pr_results = _collect_strat_prs(strat_key, args.output_dir,
                                        server, user, token)
        if pr_results:
            all_urls = {u for _, _, urls in pr_results for u in urls}
            with open(manifest, "a", encoding="utf-8") as f:
                f.write(f"\n## Pull Requests\n\n")
                f.write(f"Collected from {len(pr_results)} tasks across "
                        f"Epics under {strat_key}.\n\n")
                for key, summary, urls in pr_results:
                    for url in urls:
                        f.write(f"- {url} ({key}: {summary})\n")
            log.info("Appended %d PR URLs to manifest", len(all_urls))

    log.info("Done — %s", args.output_dir)


if __name__ == "__main__":
    main()
