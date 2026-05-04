#!/usr/bin/env python3
"""Assemble planner input from doccontext manifest and upstream artifacts.

Reads artifacts/doccontext.md and resolves all referenced files to produce
a single self-contained planner input document at artifacts/docplan/planner-input.md.

For each JIRA issue: includes the full body (already curated by jiracontext-populate).
For each relevant PR: extracts "What changed" and "Documentation impact" sections.
For each peripheral PR: includes only the gist from the manifest.

Files read (not modified)
    artifacts/doccontext.md — consolidated context manifest
    artifacts/jiracontext/*.md — JIRA issue files (body content)
    artifacts/prcontext/*.md — PR summary files (What changed, Doc impact)
File written
    artifacts/docplan/planner-input.md — assembled planner input

Usage:
    python3 scripts/doc_plan_prepare.py
    python3 scripts/doc_plan_prepare.py --doccontext artifacts/doccontext.md
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

log = logging.getLogger("doc_plan_prepare")

DEFAULT_DOCCONTEXT = "artifacts/doccontext.md"
DEFAULT_OUTPUT_DIR = "artifacts/docplan"


def _parse_manifest(manifest_path):
    """Read a manifest markdown file and return (frontmatter_dict, body_str)."""
    with open(manifest_path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{manifest_path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _extract_section(body, heading):
    """Extract a markdown section by ## heading. Returns content until the next ## heading."""
    lines = body.split("\n")
    capturing = False
    buffer = []
    for line in lines:
        if re.match(r"^##\s+", line):
            if capturing:
                break
            if re.match(rf"^##\s+{re.escape(heading)}\s*$", line, re.IGNORECASE):
                capturing = True
                continue
        elif capturing:
            buffer.append(line)
    return "\n".join(buffer).strip() if buffer else None


def _repo_from_url(url):
    """Extract org/repo from a GitHub PR URL."""
    parts = url.rstrip("/").split("/")
    if len(parts) >= 5 and parts[-2] == "pull":
        return f"{parts[-4]}/{parts[-3]}"
    return ""


def _pr_number_from_url(url):
    """Extract PR number from a GitHub PR URL."""
    parts = url.rstrip("/").split("/")
    if parts:
        return parts[-1]
    return ""


def _stem_from_patch_path(patch_path):
    """Derive the file stem from a filtered patch path."""
    return Path(patch_path).stem if patch_path else None


def _read_jira_issues(jira_issues):
    """Read JIRA issue files and return list of dicts with key, summary, body."""
    results = []
    for issue in jira_issues:
        path = issue.get("path")
        key = issue.get("key")
        if not path or not Path(path).exists():
            log.warning("JIRA file not found: %s", path)
            continue
        try:
            fm, body = _parse_manifest(path)
        except (ValueError, Exception) as e:
            log.warning("Cannot parse %s: %s", path, e)
            continue
        results.append({
            "key": key,
            "summary": fm.get("summary", ""),
            "issue_type": fm.get("issue_type", ""),
            "body": body,
        })
    return results


def _read_relevant_prs(pull_requests):
    """Read summary files for relevant PRs, extract doc-relevant sections."""
    relevant = []
    for pr in pull_requests:
        if pr.get("verdict") != "relevant":
            continue

        url = pr.get("url", "")
        stem = _stem_from_patch_path(pr.get("filtered_patch"))
        if not stem:
            log.warning("No patch path for PR %s, skipping detail extraction", url)
            relevant.append({
                "url": url,
                "repo": _repo_from_url(url),
                "pr_number": _pr_number_from_url(url),
                "title": pr.get("title", ""),
                "gist": pr.get("gist", ""),
                "what_changed": None,
                "doc_impact": None,
            })
            continue

        summary_path = Path("artifacts/prcontext") / f"{stem}.md"
        what_changed = None
        doc_impact = None

        if summary_path.exists():
            try:
                _, sbody = _parse_manifest(summary_path)
                what_changed = _extract_section(sbody, "What changed")
                doc_impact = _extract_section(sbody, "Documentation impact")
            except (ValueError, Exception):
                log.warning("Cannot parse summary for %s", stem)

        relevant.append({
            "url": url,
            "repo": _repo_from_url(url),
            "pr_number": _pr_number_from_url(url),
            "title": pr.get("title", ""),
            "gist": pr.get("gist", ""),
            "what_changed": what_changed,
            "doc_impact": doc_impact,
        })
    return relevant


def _collect_peripheral_prs(pull_requests):
    """Collect peripheral PR metadata (gist only, no file reads)."""
    peripheral = []
    for pr in pull_requests:
        if pr.get("verdict") != "peripheral":
            continue
        url = pr.get("url", "")
        peripheral.append({
            "url": url,
            "repo": _repo_from_url(url),
            "pr_number": _pr_number_from_url(url),
            "title": pr.get("title", ""),
            "gist": pr.get("gist", ""),
        })
    return peripheral


def _group_by_repo(prs):
    """Group a list of PR dicts by repo."""
    groups = {}
    for pr in prs:
        repo = pr.get("repo") or "unknown"
        groups.setdefault(repo, []).append(pr)
    return groups


def _build_output(feature_body, jira_issues, relevant_prs, peripheral_prs,
                  code_repos, additional_links, starting_issue):
    """Build the planner-input.md content."""
    lines = []

    # YAML frontmatter
    fm = {
        "starting_issue": starting_issue,
        "assembled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "evidence_summary": {
            "jira_issues": len(jira_issues),
            "relevant_prs": len(relevant_prs),
            "peripheral_prs": len(peripheral_prs),
            "code_repositories": len(code_repos),
        },
    }
    lines.append("---")
    lines.append(yaml.dump(fm, default_flow_style=False, sort_keys=False).rstrip())
    lines.append("---")
    lines.append("")

    # Feature overview
    lines.append("## Feature Overview")
    lines.append("")
    lines.append(feature_body)
    lines.append("")

    # JIRA requirements
    lines.append("## JIRA Requirements")
    lines.append("")
    for issue in jira_issues:
        lines.append(f"### {issue['key']}: {issue['summary']} ({issue['issue_type']})")
        lines.append("")
        lines.append(issue["body"])
        lines.append("")

    # Relevant PRs grouped by repo
    lines.append("## PR Evidence (Relevant)")
    lines.append("")
    lines.append("These PRs implement features that documentation should cover.")
    lines.append("")

    for repo, repo_prs in _group_by_repo(relevant_prs).items():
        lines.append(f"### {repo}")
        lines.append("")
        for pr in repo_prs:
            lines.append(f"#### PR #{pr['pr_number']}: {pr['title']}")
            lines.append(f"**Gist:** {pr['gist']}")
            lines.append(f"**URL:** {pr['url']}")
            lines.append("")
            if pr.get("what_changed"):
                lines.append("**What changed:**")
                lines.append(pr["what_changed"])
                lines.append("")
            if pr.get("doc_impact"):
                lines.append("**Documentation impact:**")
                lines.append(pr["doc_impact"])
                lines.append("")

    # Peripheral PRs (brief table)
    lines.append("## PR Evidence (Peripheral)")
    lines.append("")
    lines.append("These PRs are bug fixes, test additions, or refactors.")
    lines.append("They should NOT generate new documentation modules,")
    lines.append("but may contain details to incorporate into existing topics.")
    lines.append("")
    lines.append("| PR | Repo | Title | Gist |")
    lines.append("|---|---|---|---|")
    for pr in peripheral_prs:
        lines.append(
            f"| #{pr['pr_number']} | {pr['repo']} | {pr['title']} | {pr['gist']} |"
        )
    lines.append("")

    # Code repositories
    if code_repos:
        lines.append("## Code Repositories")
        lines.append("")
        lines.append("These repositories have been cloned locally. They can be")
        lines.append("referenced for CRD schemas, API types, and configuration formats.")
        lines.append("")
        for repo in code_repos:
            url = repo.get("url", "")
            path = repo.get("path", "")
            lines.append(f"- **{url}** cloned at `{path}`")
        lines.append("")

    # Additional links
    if additional_links:
        lines.append("## Additional Context Links")
        lines.append("")
        lines.append("These links contain design context (prototypes, Miro boards,")
        lines.append("Google Docs) that may inform planning but cannot be accessed")
        lines.append("programmatically. Note their existence for human reviewers.")
        lines.append("")
        for link in additional_links:
            lines.append(f"- {link}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Assemble planner input from doccontext manifest."
    )
    parser.add_argument(
        "--doccontext", default=DEFAULT_DOCCONTEXT,
        help=f"Path to doccontext manifest (default: {DEFAULT_DOCCONTEXT})",
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not Path(args.doccontext).exists():
        log.error("Doccontext manifest not found: %s", args.doccontext)
        sys.exit(2)

    fm, body = _parse_manifest(args.doccontext)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read JIRA issues
    jira_issues = _read_jira_issues(fm.get("jira_issues", []))
    log.info("Read %d JIRA issues", len(jira_issues))

    # Read PR summaries
    prs = fm.get("pull_requests", [])
    relevant_prs = _read_relevant_prs(prs)
    peripheral_prs = _collect_peripheral_prs(prs)
    log.info(
        "Found %d relevant PRs, %d peripheral PRs",
        len(relevant_prs), len(peripheral_prs),
    )

    code_repos = fm.get("code_repositories", [])
    additional_links = fm.get("additional_links", [])
    starting_issue = fm.get("starting_issue", "")

    # Build and write output
    content = _build_output(
        body, jira_issues, relevant_prs, peripheral_prs,
        code_repos, additional_links, starting_issue,
    )
    output_path = output_dir / "planner-input.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    log.info("Wrote planner input to %s", output_path)

    # JSON summary for the skill
    summary = {
        "planner_input": str(output_path),
        "jira_issues": len(jira_issues),
        "relevant_prs": len(relevant_prs),
        "peripheral_prs": len(peripheral_prs),
        "code_repos": len(code_repos),
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
