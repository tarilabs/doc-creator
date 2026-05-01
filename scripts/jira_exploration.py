#!/usr/bin/env python3
"""Download JIRA issues and save them as markdown to artifacts/jiraexploration/.

Usage:
    python3 scripts/jira_exploration.py RHOAIENG-53404 RHOAIENG-60547
    python3 scripts/jira_exploration.py RHAISTRAT-1084 --link-filter RHOAI
    python3 scripts/jira_exploration.py RHOAIENG-53404 --output-dir /tmp/jiras
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from fetch_issue import _fetch_all
from jira_utils import require_env, get_issue


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

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.",
              file=sys.stderr)
        sys.exit(2)

    os.makedirs(args.output_dir, exist_ok=True)
    manifest = os.path.join(os.path.dirname(args.output_dir),
                            "jiraexploration.md")

    # Walk up the parent chain to find the RHAISTRAT ancestor
    is_strat = args.issue_key.startswith("RHAISTRAT-")
    chain = []
    if not is_strat:
        current = args.issue_key
        for _ in range(10):
            try:
                issue = get_issue(server, user, token, current,
                                  fields=["summary", "parent"])
            except Exception:
                break
            parent = issue.get("fields", {}).get("parent")
            if not parent:
                break
            parent_key = parent.get("key", "")
            parent_summary = parent.get("fields", {}).get("summary", "")
            chain.append((parent_key, parent_summary))
            print(f"  Parent: {parent_key} — {parent_summary}",
                  file=sys.stderr)
            if parent_key.startswith("RHAISTRAT-"):
                break
            current = parent_key

    strat_key = None
    if is_strat:
        strat_key = args.issue_key
    elif chain and chain[-1][0].startswith("RHAISTRAT-"):
        strat_key = chain[-1][0]

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

    # Fetch the RHAISTRAT if found and different from the starting issue
    if strat_key and strat_key != args.issue_key:
        rc = _fetch_all(strat_key, args.output_dir, server, user, token,
                        link_filter=args.link_filter)
        if rc != 0:
            sys.exit(rc)

    # Fetch the starting issue
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


if __name__ == "__main__":
    main()
