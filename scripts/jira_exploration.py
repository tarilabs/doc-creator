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
from jira_utils import require_env


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
    with open(manifest, "w", encoding="utf-8") as f:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.write(f"# JIRA Exploration\n\n")
        f.write(f"- **Starting issue**: {args.issue_key}\n")
        f.write(f"- **Started at**: {ts}\n")
        f.write(f"- **Link filter**: {args.link_filter or '(none)'}\n")
        f.write(f"- **Output directory**: {args.output_dir}\n")

    rc = _fetch_all(args.issue_key, args.output_dir, server, user, token,
                    link_filter=args.link_filter)
    if rc != 0:
        sys.exit(rc)

    issue_md = os.path.join(args.output_dir, f"{args.issue_key}.md")
    if os.path.isfile(issue_md):
        with open(issue_md, encoding="utf-8") as f:
            text = f.read()
        # Body is everything after the closing "---\n" of the frontmatter
        parts = text.split("---\n", 2)
        body = parts[2].strip() if len(parts) >= 3 else text.strip()
        with open(manifest, "a", encoding="utf-8") as f:
            f.write(f"\n## {args.issue_key}\n\n")
            f.write(body + "\n")


if __name__ == "__main__":
    main()
