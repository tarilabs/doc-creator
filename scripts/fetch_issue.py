#!/usr/bin/env python3
"""Fetch a Jira issue and print its fields as JSON.

Lightweight read utility for skills that need to fetch issues when the
Atlassian MCP server is unavailable. Outputs JSON to stdout for the
calling skill to parse.

Usage:
    python3 scripts/fetch_issue.py RHAIRFE-1234 [--fields summary,description,comment,priority,labels,status] [--markdown]

    # Fetch everything and write all artifact files at once
    python3 scripts/fetch_issue.py RHAIRFE-1234 --fetch-all artifacts

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token

Exit codes:
    0  Success
    1  API/network/script error
    2  Missing JIRA credentials (caller should try MCP fallback)
"""

import argparse
import base64
import json
import os
import re
import shutil
import sys
import urllib.request

from jira_utils import (require_env, get_issue, get_comments,
                        search_issues, api_call_with_retry,
                        adf_to_markdown)


FIELD_GIT_PULL_REQUEST = "customfield_10875"  # "Git Pull Request" custom field

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

TEXT_EXTENSIONS = {
    ".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".xml",
    ".html", ".htm", ".rst", ".adoc", ".log", ".conf", ".cfg",
    ".ini", ".toml", ".properties", ".sh", ".bash", ".py",
    ".java", ".go", ".js", ".ts",
}

TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml",
                      "application/yaml", "application/x-yaml")


def _desc_to_markdown(desc_raw):
    """Convert a raw description field (ADF dict or string) to markdown."""
    if isinstance(desc_raw, dict):
        return adf_to_markdown(desc_raw).strip()
    elif desc_raw is not None:
        return str(desc_raw).strip()
    return ""


def _format_comment_date(iso_date):
    """Format an ISO timestamp to a human-readable date string."""
    # Jira dates look like "2025-01-15T10:30:00.000+0000"
    if not iso_date:
        return "Unknown date"
    return iso_date[:10]


def _sanitize_filename(name):
    """Remove path traversal characters and unsafe chars from a filename."""
    name = os.path.basename(name)
    name = re.sub(r'[^\w.\-() ]', '_', name)
    return name or "unnamed"


def _is_text_attachment(attachment):
    """Check if an attachment is text-based and within size limits."""
    size = attachment.get("size", 0)
    if size > MAX_ATTACHMENT_BYTES:
        return False
    filename = attachment.get("filename", "")
    mime = attachment.get("mimeType", "")
    _, ext = os.path.splitext(filename.lower())
    if ext in TEXT_EXTENSIONS:
        return True
    if any(mime.startswith(p) for p in TEXT_MIME_PREFIXES):
        return True
    return False


def _download_attachment(url, dest_path, user, token):
    """Download a Jira attachment file using basic auth."""
    credentials = base64.b64encode(f"{user}:{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(dest_path, "wb") as f:
            f.write(resp.read())


def _fetch_attachments(attachments, issue_key, artifacts_dir, user, token):
    """Download text-based attachments to artifacts/attachments/{key}/."""
    if not attachments:
        return
    att_dir = os.path.join(artifacts_dir, "attachments", issue_key)
    os.makedirs(att_dir, exist_ok=True)
    count = 0
    for att in attachments:
        if not _is_text_attachment(att):
            filename = att.get("filename", "?")
            size_kb = att.get("size", 0) // 1024
            print(f"  Skipping attachment {filename} "
                  f"(type={att.get('mimeType')}, {size_kb}KB)",
                  file=sys.stderr)
            continue
        filename = _sanitize_filename(att.get("filename", "unnamed"))
        dest = os.path.join(att_dir, filename)
        content_url = att.get("content", "")
        if not content_url:
            continue
        try:
            _download_attachment(content_url, dest, user, token)
            count += 1
            print(f"  Downloaded attachment: {filename}", file=sys.stderr)
        except Exception as e:
            print(f"  Error downloading {filename}: {e}", file=sys.stderr)
    if count == 0:
        os.rmdir(att_dir)
    else:
        print(f"  {count} attachment(s) saved to {att_dir}", file=sys.stderr)


def _extract_urls_from_adf(adf_doc):
    """Extract all URLs from an ADF document (inlineCards and link marks)."""
    urls = []
    if not isinstance(adf_doc, dict):
        return urls
    if adf_doc.get("type") == "inlineCard":
        url = adf_doc.get("attrs", {}).get("url", "")
        if url:
            urls.append(url)
    for mark in adf_doc.get("marks", []):
        if mark.get("type") == "link":
            url = mark.get("attrs", {}).get("href", "")
            if url:
                urls.append(url)
    for child in adf_doc.get("content", []):
        urls.extend(_extract_urls_from_adf(child))
    return urls


def _write_issue_md(issue_key, output_dir, server, user, token, written):
    """Fetch a single issue and write it as {KEY}.md with frontmatter."""
    if issue_key in written:
        return []
    written.add(issue_key)

    try:
        issue = get_issue(server, user, token, issue_key,
                          fields=["summary", "description", "issuelinks",
                                  "issuetype", FIELD_GIT_PULL_REQUEST])
    except Exception as e:
        print(f"  Error fetching {issue_key}: {e}", file=sys.stderr)
        return []

    fields = issue.get("fields", {})
    summary = fields.get("summary", "")
    issue_type_obj = fields.get("issuetype")
    issue_type = issue_type_obj.get("name", "") if isinstance(
        issue_type_obj, dict) else ""
    desc_md = _desc_to_markdown(fields.get("description"))

    pr_urls = _extract_urls_from_adf(fields.get(FIELD_GIT_PULL_REQUEST))

    # Remote links (web links / hyperlinks attached to the issue)
    remote_links = []
    try:
        remotes = api_call_with_retry(
            server, f"/issue/{issue_key}/remotelink", user, token)
        for r in remotes:
            obj = r.get("object", {})
            url = obj.get("url", "")
            title = obj.get("title", "")
            if url:
                remote_links.append({"title": title, "url": url})
    except Exception:
        pass

    md_path = os.path.join(output_dir, f"{issue_key}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f"jira_key: {issue_key}\n")
        f.write(f"summary: \"{summary}\"\n")
        if issue_type:
            f.write(f"issue_type: {issue_type}\n")
        if pr_urls:
            f.write("git_pull_requests:\n")
            for url in pr_urls:
                f.write(f"  - {url}\n")
        if remote_links:
            f.write("links:\n")
            for link in remote_links:
                f.write(f"  - title: \"{link['title']}\"\n")
                f.write(f"    url: {link['url']}\n")
        f.write("---\n\n")
        f.write(desc_md + "\n")

    print(f"  Wrote {md_path}", file=sys.stderr)
    return fields.get("issuelinks", [])


def _fetch_all(issue_key, output_dir, server, user, token, link_filter="UX"):
    """Fetch issue, its children, and filtered linked issues.

    Children (via JQL parent query) are always fetched.
    Linked issues are only fetched if their key contains link_filter.

    Returns 0 on success, 1 on error.
    """
    os.makedirs(output_dir, exist_ok=True)
    written = set()

    issuelinks = _write_issue_md(issue_key, output_dir,
                                 server, user, token, written)
    if issue_key not in written:
        return 1

    children = search_issues(server, user, token,
                             f"parent = {issue_key} ORDER BY key ASC",
                             fields=["summary"], max_results=100)
    for child in children:
        child_key = child.get("key", "")
        _write_issue_md(child_key, output_dir, server, user, token, written)

    for link in issuelinks:
        linked = link.get("inwardIssue") or link.get("outwardIssue")
        if not linked:
            continue
        linked_key = linked.get("key", "")
        if link_filter and link_filter not in linked_key:
            continue
        _write_issue_md(linked_key, output_dir, server, user, token, written)

    print(f"OK: wrote {len(written)} issue(s) to {output_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue_key",
                        help="Jira issue key (e.g. RHAIRFE-1234)")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--fields", default=None,
                            help="Comma-separated list of fields to fetch "
                                 "(default: summary,description,priority,"
                                 "labels,status). "
                                 "Use 'comment' to also fetch comments.")
    mode_group.add_argument("--fetch-all", metavar="OUTPUT_DIR",
                            help="Fetch issue and all linked issues, write "
                                 "each as its own markdown file in the "
                                 "given directory.")

    parser.add_argument("--link-filter", default="UX",
                        help="Only download linked issues whose key "
                             "contains this substring (default: UX). "
                             "Pass empty string to download all links.")
    parser.add_argument("--markdown", action="store_true",
                        help="Convert ADF fields (description, comments) "
                             "to markdown strings in the output")
    parser.add_argument("--write-original", metavar="DIR",
                        help="Write the description as markdown to "
                             "DIR/<issue_key>.md. If JIRA creds are "
                             "available, refetches via REST API and uses "
                             "adf_to_markdown for deterministic output. "
                             "If not, copies DIR/<issue_key>.input.md "
                             "as a fallback.")
    args = parser.parse_args()

    server, user, token = require_env()

    # --fetch-all mode: script does everything
    if args.fetch_all:
        if not all([server, user, token]):
            print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
                  "required for --fetch-all mode.", file=sys.stderr)
            sys.exit(2)
        rc = _fetch_all(args.issue_key, args.fetch_all, server, user, token,
                        link_filter=args.link_filter)
        sys.exit(rc)

    # --write-original-only mode: no --fields means caller just wants
    # the original description snapshot written to disk.
    if args.write_original and not args.fields:
        os.makedirs(args.write_original, exist_ok=True)
        orig_path = os.path.join(args.write_original,
                                 f"{args.issue_key}.md")
        base, ext = os.path.splitext(orig_path)
        input_path = base + ".input" + ext
        if all([server, user, token]):
            issue = get_issue(server, user, token, args.issue_key,
                              fields=["description"])
            desc_md = _desc_to_markdown(
                issue.get("fields", {}).get("description"))
            with open(orig_path, "w", encoding="utf-8") as f:
                f.write(desc_md + "\n")
            if os.path.exists(input_path):
                os.remove(input_path)
        elif os.path.exists(input_path):
            shutil.copy2(input_path, orig_path)
            os.remove(input_path)
        else:
            print(f"Warning: no JIRA creds and no {input_path}, "
                  "skipping --write-original", file=sys.stderr)
        return

    # Default fields when not in write-original-only mode
    if not args.fields:
        args.fields = "summary,description,priority,labels,status"

    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
              "required.", file=sys.stderr)
        sys.exit(1)

    requested = [f.strip() for f in args.fields.split(",")]
    fetch_comments = "comment" in requested
    api_fields = [f for f in requested if f != "comment"]

    # Fetch the issue
    issue = get_issue(server, user, token, args.issue_key,
                      fields=api_fields if api_fields else None)

    # Build output
    fields = issue.get("fields", {})
    output = {
        "key": issue.get("key"),
        "fields": {},
    }

    for field_name in api_fields:
        value = fields.get(field_name)
        # Convert ADF description to markdown if requested
        if args.markdown and field_name == "description" and \
                isinstance(value, dict):
            value = adf_to_markdown(value).strip()
        output["fields"][field_name] = value

    # Fetch comments separately if requested
    if fetch_comments:
        comments = get_comments(server, user, token, args.issue_key)
        output["comments"] = []
        for c in comments:
            body = c.get("body", {})
            if args.markdown and isinstance(body, dict):
                body = adf_to_markdown(body).strip()
            output["comments"].append({
                "author": c.get("author", {}).get("displayName", "Unknown"),
                "created": c.get("created", ""),
                "body": body,
            })

    # Write original description snapshot for conflict detection
    if args.write_original:
        desc_md = _desc_to_markdown(fields.get("description"))
        os.makedirs(args.write_original, exist_ok=True)
        orig_path = os.path.join(args.write_original,
                                 f"{args.issue_key}.md")
        with open(orig_path, "w", encoding="utf-8") as f:
            f.write(desc_md + "\n")

    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
