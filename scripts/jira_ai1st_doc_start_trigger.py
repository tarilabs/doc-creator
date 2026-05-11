#!/usr/bin/env python3
"""Search JIRA for RHOAIENG/RHAISTRAT issues with a specific label.

Optionally trigger a GitLab CI pipeline for each issue found.

Usage:
    python3 scripts/jira_label_search.py
    python3 scripts/jira_label_search.py --trigger
    python3 scripts/jira_label_search.py --trigger --job doc-creator
"""

import argparse
import logging
import subprocess
import sys

from jira_utils import require_env, search_issues

log = logging.getLogger("jira_label_search")

DEFAULT_PROJECTS = ["RHOAIENG", "RHAISTRAT"]
DEFAULT_LABEL = "ai1st-doc-start"
GITLAB_REPO = "redhat/rhel-ai/agentic-ci/doc-pipeline"


def build_jql(projects, label):
    project_clause = ", ".join(projects)
    return (f'project IN ({project_clause}) '
            f'AND labels = "{label}" '
            f'ORDER BY project ASC, key ASC')


def trigger_pipeline(jira_key, job):
    cmd = [
        "glab", "ci", "run", "-b", "main",
        "--variables", f"JIRA_KEY:{jira_key}",
        "--variables", f"JOB:{job}",
        "--repo", GITLAB_REPO,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        log.info("  -> triggered %s for %s", job, jira_key)
        return True
    log.error("  -> FAILED to trigger %s for %s: %s",
              job, jira_key, result.stderr.strip())
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Search JIRA for issues with a given label."
    )
    parser.add_argument("--projects", nargs="+", default=DEFAULT_PROJECTS,
                        help=f"JIRA projects to search (default: "
                             f"{' '.join(DEFAULT_PROJECTS)})")
    parser.add_argument("--label", default=DEFAULT_LABEL,
                        help=f"Label to filter on (default: {DEFAULT_LABEL})")
    parser.add_argument("--trigger", action="store_true",
                        help="Trigger a GitLab CI pipeline for each issue")
    parser.add_argument("--job", default="doc-pipeline",
                        help="Pipeline job to trigger (default: doc-pipeline)")
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

    jql = build_jql(args.projects, args.label)
    log.info("JQL: %s", jql)

    fields = ["summary", "status", "issuetype", "priority"]
    issues = search_issues(server, user, token, jql,
                           fields=fields, max_results=500)

    log.info("Found %d issue(s)", len(issues))

    keys = []
    for issue in issues:
        f = issue.get("fields", {})
        key = issue.get("key", "")
        keys.append(key)
        summary = f.get("summary", "")
        status = f.get("status", {})
        status_name = status.get("name", "") if isinstance(status, dict) else ""
        issue_type = f.get("issuetype", {})
        type_name = issue_type.get("name", "") if isinstance(issue_type, dict) else ""
        priority = f.get("priority", {})
        prio_name = priority.get("name", "") if isinstance(priority, dict) else ""
        log.info("  %-20s %-12s %-10s %-8s %s",
                 key, status_name, type_name, prio_name, summary)

    if not args.trigger or not keys:
        return

    log.info("Triggering %s for %d issue(s)...", args.job, len(keys))
    failed = 0
    for key in keys:
        if not trigger_pipeline(key, args.job):
            failed += 1

    log.info("Triggered %d/%d pipelines", len(keys) - failed, len(keys))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
