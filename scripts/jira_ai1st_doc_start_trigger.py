#!/usr/bin/env python3
"""Search JIRA for RHOAIENG/RHAISTRAT issues with the ai1st-doc-start label
and trigger a GitLab CI doc-pipeline for each one.

Idempotent: after a successful trigger, the label is swapped from
ai1st-doc-start → ai1st-doc-invoked so the issue won't be picked up again.
If the trigger fails, the label stays unchanged for retry on the next run.

Usage:
    python3 scripts/jira_ai1st_doc_start_trigger.py
    python3 scripts/jira_ai1st_doc_start_trigger.py --trigger
    python3 scripts/jira_ai1st_doc_start_trigger.py --trigger --job doc-creator
"""

import argparse
import logging
import re
import subprocess
import sys

from jira_utils import (require_env, search_issues, swap_labels,
                        add_comment, markdown_to_adf)

log = logging.getLogger("jira_ai1st_doc_start_trigger")

DEFAULT_PROJECTS = ["RHOAIENG", "RHAISTRAT"]
LABEL_START = "ai1st-doc-start"
LABEL_INVOKED = "ai1st-doc-invoked"
GITLAB_REPO = "redhat/rhel-ai/agentic-ci/doc-pipeline"


def build_jql(projects, label):
    project_clause = ", ".join(projects)
    return (f'project IN ({project_clause}) '
            f'AND labels = "{label}" '
            f'ORDER BY project ASC, key ASC')


_WEBURL_RE = re.compile(r'weburl:\s*(\S+)')


def trigger_pipeline(jira_key, job):
    cmd = [
        "glab", "ci", "run", "-b", "main",
        "--variables", f"JIRA_KEY:{jira_key}",
        "--variables", f"JOB:{job}",
        "--repo", GITLAB_REPO,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        m = _WEBURL_RE.search(result.stdout)
        url = m.group(1) if m else None
        log.info("  -> triggered %s for %s (%s)", job, jira_key, url or "no URL")
        return url
    log.error("  -> FAILED to trigger %s for %s: %s",
              job, jira_key, result.stderr.strip())
    return None


def swap_label(server, user, token, jira_key):
    try:
        swap_labels(server, user, token, jira_key,
                    add=[LABEL_INVOKED], remove=[LABEL_START])
        log.info("  -> label swapped: %s → %s", LABEL_START, LABEL_INVOKED)
        return True
    except Exception as e:
        log.warning("  -> label swap failed for %s: %s (pipeline was triggered)",
                    jira_key, e)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Find ai1st-doc-start issues and trigger doc-pipeline."
    )
    parser.add_argument("--projects", nargs="+", default=DEFAULT_PROJECTS,
                        help=f"JIRA projects to search (default: "
                             f"{' '.join(DEFAULT_PROJECTS)})")
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

    jql = build_jql(args.projects, LABEL_START)
    log.info("JQL: %s", jql)

    fields = ["summary", "status", "issuetype", "priority", "labels"]
    issues = search_issues(server, user, token, jql,
                           fields=fields, max_results=500)

    log.info("Found %d issue(s)", len(issues))

    keys = []
    for issue in issues:
        f = issue.get("fields", {})
        key = issue.get("key", "")
        labels = f.get("labels", [])
        if LABEL_INVOKED in labels:
            log.warning("  %-20s SKIPPED — has both %s and %s, "
                        "remove %s to re-trigger",
                        key, LABEL_START, LABEL_INVOKED, LABEL_INVOKED)
            continue
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
        pipeline_url = trigger_pipeline(key, args.job)
        if pipeline_url:
            comment = (f"Pipeline **{args.job}** triggered: "
                       f"[{pipeline_url}]({pipeline_url})\n\n"
                       f"Discovered by label `{LABEL_START}`.")
            try:
                add_comment(server, user, token, key,
                            markdown_to_adf(comment))
                log.info("  -> commented on %s", key)
            except Exception as e:
                log.warning("  -> comment failed for %s: %s", key, e)
            swap_label(server, user, token, key)
        else:
            failed += 1

    log.info("Triggered %d/%d pipelines", len(keys) - failed, len(keys))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
