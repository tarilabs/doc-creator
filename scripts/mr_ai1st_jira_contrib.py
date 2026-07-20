#!/usr/bin/env python3
"""Process GitLab MRs and update related JIRA issues with MR links.

Can process a single MR URL or scan the docs repo for unprocessed bot MRs.

Extracts the JIRA key from the MR title, branch, or description, then:
- Adds the MR URL to the JIRA "Pull Request" custom field (if not already there)
- Posts a comment on the JIRA issue with the MR link
- Walks up to the RHAISTRAT parent and ensures a CCS/DOCS target exists
  (child Epic + Task, or a directly-linked "documented by" issue)
- Marks the MR as processed (label + comment marker) to avoid re-processing

Usage:
    python3 scripts/mr_ai1st_jira_contrib.py https://gitlab.cee.redhat.com/.../merge_requests/2683
    python3 scripts/mr_ai1st_jira_contrib.py https://gitlab.cee.redhat.com/.../merge_requests/2683 --force
    python3 scripts/mr_ai1st_jira_contrib.py --scan
    python3 scripts/mr_ai1st_jira_contrib.py --scan --scan-all-authors
"""

import argparse
import json
import logging
import re
import subprocess
import sys

from jira_utils import (require_env, get_issue, search_issues, create_issue,
                        add_comment, add_labels, markdown_to_adf,
                        api_call_with_retry)
from fetch_issue import _extract_urls_from_adf, FIELD_GIT_PULL_REQUEST

log = logging.getLogger("mr_ai1st_jira_contrib")

EXPECTED_AUTHOR = "AI_FIRST_TOKEN"
PROCESSED_LABEL = "ai1st-jira-contributed"
PROCESSED_MARKER = "<!-- ai1st-jira-contributed -->"
JIRA_LABEL = "ai1st-doc-contributed"
JIRA_KEY_RE = re.compile(r'(RHOAIENG|RHAISTRAT)-\d+', re.IGNORECASE)
MR_URL_RE = re.compile(
    r'^https?://(?P<host>[^/]+)/(?P<project>.+)/-/merge_requests/(?P<iid>\d+)')

DOCS_REPO_URL = ("https://gitlab.cee.redhat.com/"
                  "documentation-red-hat-openshift-data-science-documentation/"
                  "openshift-ai-documentation")
BOT_USERNAME = "project_82936_bot_1d4a93d2a0982292a1ce3611792e537a"


def parse_mr_url(url):
    m = MR_URL_RE.match(url)
    if not m:
        return None
    return m.group("host"), m.group("project"), int(m.group("iid"))


def glab_mr_list(repo_url, author=None):
    cmd = ["glab", "mr", "list", "--repo", repo_url,
           "--not-label", PROCESSED_LABEL,
           "--output", "json", "--per-page", "100"]
    if author:
        cmd.extend(["--author", author])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if author and "Failed to find user by name" in stderr:
            log.warning(
                "Author '%s' not found in GitLab (deleted/ghost user). "
                "Falling back to unfiltered scan. "
                "The hardcoded BOT_USERNAME likely needs updating — "
                "this usually means the project access token was refreshed "
                "on the target site.", author)
            return glab_mr_list(repo_url, author=None)
        log.error("glab mr list failed: %s", stderr)
        return []
    return json.loads(result.stdout)


def glab_mr_view(project, iid, host):
    repo_url = f"https://{host}/{project}"
    cmd = ["glab", "mr", "view", str(iid), "--repo", repo_url,
           "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("glab mr view failed: %s", result.stderr.strip())
        return None
    return json.loads(result.stdout)


def glab_mr_note_list(project, iid, host):
    repo_url = f"https://{host}/{project}"
    cmd = ["glab", "mr", "note", "list", str(iid), "--repo", repo_url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.warning("glab mr note list failed: %s", result.stderr.strip())
        return ""
    return result.stdout


def glab_mr_update_label(project, iid, host, label):
    repo_url = f"https://{host}/{project}"
    cmd = ["glab", "mr", "update", str(iid), "--repo", repo_url,
           "--label", label]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.warning("Failed to add label %s: %s", label, result.stderr.strip())
        return False
    return True


def glab_mr_note_create(project, iid, host, body):
    repo_url = f"https://{host}/{project}"
    cmd = ["glab", "mr", "note", str(iid), "--repo", repo_url,
           "--message", body]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.warning("Failed to post MR note: %s", result.stderr.strip())
        return False
    return True


def is_already_processed(mr_data, project, iid, host):
    labels = mr_data.get("labels", [])
    if PROCESSED_LABEL in labels:
        log.info("MR !%d already has label '%s'", iid, PROCESSED_LABEL)
        return True
    notes_text = glab_mr_note_list(project, iid, host)
    if PROCESSED_MARKER in notes_text:
        log.info("MR !%d has marker in notes", iid)
        return True
    return False


def extract_jira_key(mr_data):
    sources = ("title", "source_branch", "description")
    found = {}
    for field_name in sources:
        value = mr_data.get(field_name, "") or ""
        seen = set()
        for m in JIRA_KEY_RE.finditer(value):
            key = m.group(0).upper()
            if key not in seen:
                seen.add(key)
                found.setdefault(key, []).append(field_name)

    for key, fields in found.items():
        if len(fields) >= 2:
            log.info("JIRA key %s confirmed in %s", key, ", ".join(fields))
            return key

    for key, fields in found.items():
        log.warning("JIRA key %s found only in %s (need 2+ sources)",
                    key, ", ".join(fields))
    return None


def update_jira_pr_field(server, user, token, jira_key, mr_url):
    """Add MR URL to the Git Pull Request custom field if not already there."""
    issue = get_issue(server, user, token, jira_key,
                      fields=[FIELD_GIT_PULL_REQUEST])
    existing_adf = issue.get("fields", {}).get(FIELD_GIT_PULL_REQUEST)
    existing_urls = _extract_urls_from_adf(existing_adf)

    if mr_url in existing_urls:
        log.info("MR URL already in Pull Request field, skipping update")
        return True

    new_card = {"type": "inlineCard", "attrs": {"url": mr_url}}
    if existing_adf and isinstance(existing_adf, dict):
        content = existing_adf.get("content", [])
        content.append({"type": "paragraph", "content": [new_card]})
        updated_adf = {**existing_adf, "content": content}
    else:
        updated_adf = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [new_card]}],
        }

    try:
        body = {"fields": {FIELD_GIT_PULL_REQUEST: updated_adf}}
        api_call_with_retry(server, f"/issue/{jira_key}", user, token,
                            body=body, method="PUT")
        log.info("Updated Pull Request field on %s", jira_key)
        return True
    except Exception as e:
        log.warning("Could not update Pull Request field on %s: %s "
                    "(field may be read-only)", jira_key, e)
        return False


def walk_to_rhaistrat(server, user, token, jira_key):
    if jira_key.startswith("RHAISTRAT-"):
        log.info("%s is already a RHAISTRAT", jira_key)
        return jira_key
    current = jira_key
    for _ in range(10):
        try:
            issue = get_issue(server, user, token, current,
                              fields=["summary", "parent"])
        except Exception:
            log.warning("Failed to fetch %s, stopping parent walk", current)
            break
        parent = issue.get("fields", {}).get("parent")
        if not parent:
            log.info("%s has no parent, end of chain", current)
            break
        parent_key = parent.get("key", "")
        parent_summary = parent.get("fields", {}).get("summary", "")
        log.info("  %s → %s (%s)", current, parent_key, parent_summary)
        if parent_key.startswith("RHAISTRAT-"):
            log.info("Found RHAISTRAT ancestor: %s", parent_key)
            return parent_key
        current = parent_key
    log.warning("No RHAISTRAT ancestor found for %s", jira_key)
    return None


def _find_documented_by_link(issuelinks):
    """Find a linked issue via a 'Document' link type on a STRAT."""
    for link in issuelinks:
        type_name = link.get("type", {}).get("name", "")
        if "document" not in type_name.lower():
            continue
        linked = link.get("inwardIssue") or link.get("outwardIssue") or {}
        linked_key = linked.get("key", "")
        if linked_key:
            linked_summary = (linked.get("fields") or {}).get("summary", "")
            return linked_key, linked_summary
    return None, None


def find_or_create_ccs_target(server, user, token, strat_key):
    """Find (or create) the CCS/DOCS target issue under a STRAT.

    Returns (target_key, is_direct) where is_direct=True means the target
    is a linked issue that should receive the MR directly (no child Task
    needed), and False means it's a child Epic that needs a Task underneath.
    """
    strat_issue = get_issue(server, user, token, strat_key,
                            fields=["summary", "issuelinks"])
    strat_summary = strat_issue.get("fields", {}).get("summary", "")

    children = search_issues(
        server, user, token,
        f"parent = {strat_key} ORDER BY key ASC",
        fields=["summary", "issuetype"], max_results=200)

    epics = []
    for child in children:
        f = child.get("fields", {})
        it = f.get("issuetype", {})
        if isinstance(it, dict) and it.get("name") == "Epic":
            epics.append((child.get("key", ""), f.get("summary", "")))

    ccs_epic = None
    docs_epic = None
    for key, summary in epics:
        upper = summary.upper()
        if "CCS" in upper:
            ccs_epic = key
        elif "DOCS" in upper and not docs_epic:
            docs_epic = key

    if ccs_epic:
        log.info("Found CCS Epic: %s", ccs_epic)
        return ccs_epic, False
    if docs_epic:
        log.info("Found DOCS Epic: %s", docs_epic)
        return docs_epic, False

    issuelinks = strat_issue.get("fields", {}).get("issuelinks", [])
    linked_key, linked_summary = _find_documented_by_link(issuelinks)
    if linked_key:
        log.info("Found 'documented by' linked issue: %s (%s)",
                 linked_key, linked_summary)
        return linked_key, True

    desc = markdown_to_adf(
        "Generated by AI-First automatically as no existing "
        "CCS/DOCS Epic has been identified under this strategy.")
    new_key = create_issue(
        server, user, token,
        project="RHOAIENG", issue_type="Epic",
        summary=f"[CCS] Documentation - {strat_summary}",
        description_adf=desc, priority="Normal",
        labels=[JIRA_LABEL], parent_key=strat_key)
    log.info("Created CCS Epic: %s", new_key)
    return new_key, False


def find_or_create_task(server, user, token, epic_key, mr_url, mr_iid):
    children = search_issues(
        server, user, token,
        f"parent = {epic_key} ORDER BY key ASC",
        fields=["summary", FIELD_GIT_PULL_REQUEST], max_results=200)

    for child in children:
        f = child.get("fields", {})
        urls = _extract_urls_from_adf(f.get(FIELD_GIT_PULL_REQUEST))
        if mr_url in urls:
            child_key = child.get("key", "")
            log.info("MR already tracked in %s", child_key)
            try:
                add_labels(server, user, token, child_key, [JIRA_LABEL])
            except Exception:
                pass
            return child_key

    desc = markdown_to_adf(
        f"Generated by AI-First automatically to capture the proposed "
        f"Documentation in MR [{mr_url}]({mr_url}).")
    new_key = create_issue(
        server, user, token,
        project="RHOAIENG", issue_type="Task",
        summary=f"Documentation contributed in MR !{mr_iid}",
        description_adf=desc, priority="Normal",
        labels=[JIRA_LABEL], parent_key=epic_key)
    log.info("Created Task: %s", new_key)

    update_jira_pr_field(server, user, token, new_key, mr_url)
    return new_key


def process_mr(host, project, iid, mr_data, server, user, token, force=False):
    """Process a single MR: update JIRA, walk hierarchy, mark MR as processed.

    Returns True on success, False on failure.
    """
    log.info("--- Processing MR !%d ---", iid)

    if not force and is_already_processed(mr_data, project, iid, host):
        log.info("Already processed, skipping")
        return True

    author = mr_data.get("author") or {}
    author_name = author.get("name", "")
    author_username = author.get("username", "")
    if not author_name and not author_username:
        log.info("MR !%d has a ghost user author (deleted bot user)", iid)
    elif author_name != EXPECTED_AUTHOR:
        log.warning("MR author is '%s' (%s), expected '%s'",
                    author_name, author_username, EXPECTED_AUTHOR)

    jira_key = extract_jira_key(mr_data)
    if not jira_key:
        log.error("No JIRA key found in MR !%d", iid)
        return False

    mr_url = mr_data.get("web_url", "")
    mr_title = mr_data.get("title", "").replace("[", "(").replace("]", ")")

    update_jira_pr_field(server, user, token, jira_key, mr_url)

    if not author_name and not author_username:
        author_label = "(ghost user)"
    else:
        author_label = f"{author_name} (`{author_username}`)"
    comment = (f"MR contributed: [{mr_title}]({mr_url})\n\n"
               f"Author: {author_label}")
    try:
        add_comment(server, user, token, jira_key,
                    markdown_to_adf(comment))
        log.info("Commented on %s", jira_key)
    except Exception as e:
        log.warning("Failed to comment on %s: %s", jira_key, e)

    try:
        add_labels(server, user, token, jira_key, [JIRA_LABEL])
        log.info("Added label '%s' to %s", JIRA_LABEL, jira_key)
    except Exception as e:
        log.warning("Failed to add label to %s: %s", jira_key, e)

    log.info("Walking up to RHAISTRAT parent...")
    strat_key = walk_to_rhaistrat(server, user, token, jira_key)
    if strat_key:
        try:
            add_labels(server, user, token, strat_key, [JIRA_LABEL])
            log.info("Added label '%s' to %s", JIRA_LABEL, strat_key)
        except Exception as e:
            log.warning("Failed to add label to %s: %s", strat_key, e)

        target_key, is_direct = find_or_create_ccs_target(
            server, user, token, strat_key)
        try:
            add_labels(server, user, token, target_key, [JIRA_LABEL])
            log.info("Added label '%s' to %s", JIRA_LABEL, target_key)
        except Exception as e:
            log.warning("Failed to add label to %s: %s", target_key, e)

        if is_direct:
            update_jira_pr_field(server, user, token, target_key, mr_url)
            try:
                add_comment(server, user, token, target_key,
                            markdown_to_adf(comment))
                log.info("Commented on linked issue %s", target_key)
            except Exception as e:
                log.warning("Failed to comment on %s: %s", target_key, e)
        else:
            find_or_create_task(server, user, token, target_key, mr_url, iid)

    glab_mr_update_label(project, iid, host, PROCESSED_LABEL)
    log.info("Added label '%s' to MR !%d", PROCESSED_LABEL, iid)

    note_body = (f"{PROCESSED_MARKER}\n\n"
                 f"JIRA [{jira_key}](https://redhat.atlassian.net/browse/"
                 f"{jira_key}) updated with this MR link.")
    glab_mr_note_create(project, iid, host, note_body)
    log.info("Posted marker note on MR !%d", iid)

    log.info("Done — %s updated from MR !%d", jira_key, iid)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Process GitLab MRs and update related JIRA issues."
    )
    parser.add_argument("mr_url", nargs="?", default=None,
                        help="GitLab merge request URL (single-MR mode)")
    parser.add_argument("--scan", action="store_true",
                        help="Scan the docs repo for unprocessed bot MRs")
    parser.add_argument("--scan-all-authors", action="store_true",
                        help="Include non-bot MRs in scan")
    parser.add_argument("--force", action="store_true",
                        help="Skip idempotency check and re-process")
    args = parser.parse_args()

    if not args.scan and not args.mr_url:
        parser.error("provide an MR URL or use --scan")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    server, user, token = require_env()
    if not all([server, user, token]):
        log.error("JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.")
        sys.exit(2)

    if args.scan:
        author = None if args.scan_all_authors else BOT_USERNAME
        log.info("Scanning %s for unprocessed MRs%s...",
                 DOCS_REPO_URL,
                 "" if author else " (all authors)")
        mrs = glab_mr_list(DOCS_REPO_URL, author=author)
        log.info("Found %d unprocessed MR(s)", len(mrs))

        if not args.scan_all_authors:
            mrs = [m for m in mrs
                   if (m.get("author") or {}).get("name") == EXPECTED_AUTHOR]
            log.info("After author filter: %d MR(s) from '%s'",
                     len(mrs), EXPECTED_AUTHOR)

        if not mrs:
            return

        parsed = parse_mr_url(DOCS_REPO_URL + "/-/merge_requests/1")
        host, project, _ = parsed

        # Sequential to avoid duplicate CCS Epic creation when multiple MRs
        # share the same RHAISTRAT parent (find_or_create_ccs_target is not
        # concurrency-safe). Revisit if scan volume warrants parallelism.
        succeeded = 0
        for mr_data in mrs:
            iid = mr_data.get("iid")
            title = mr_data.get("title", "")
            log.info("  !%-6d %s", iid, title[:70])
            try:
                if process_mr(host, project, iid, mr_data,
                              server, user, token, force=args.force):
                    succeeded += 1
            except Exception as e:
                log.error("  Unexpected error on MR !%d: %s", iid, e)

        log.info("Processed %d/%d MRs", succeeded, len(mrs))
        if succeeded < len(mrs):
            sys.exit(1)
    else:
        parsed = parse_mr_url(args.mr_url)
        if not parsed:
            log.error("Invalid MR URL: %s", args.mr_url)
            sys.exit(1)
        host, project, iid = parsed
        log.info("MR !%d in %s on %s", iid, project, host)

        mr_data = glab_mr_view(project, iid, host)
        if not mr_data:
            sys.exit(1)

        if not process_mr(host, project, iid, mr_data,
                          server, user, token, force=args.force):
            sys.exit(1)


if __name__ == "__main__":
    main()
