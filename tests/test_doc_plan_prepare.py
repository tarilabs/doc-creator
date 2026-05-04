"""Tests for doc_plan_prepare.py — assembles planner input from doccontext."""
import json
import os
import subprocess
import sys

import yaml


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PREPARE_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "doc_plan_prepare.py")


def _write_manifest(path, fm, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False)
        + "---\n\n" + body + "\n"
    )


def _run_prepare(tmp_path):
    return subprocess.run(
        [sys.executable, PREPARE_SCRIPT,
         "--doccontext", str(tmp_path / "artifacts" / "doccontext.md"),
         "--output-dir", str(tmp_path / "artifacts" / "docplan")],
        capture_output=True, text=True,
        cwd=str(tmp_path),
    )


def _setup_minimal(tmp_path):
    jc_dir = tmp_path / "artifacts" / "jiracontext"
    jc_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(
        jc_dir / "PROJ-1.md",
        {"jira_key": "PROJ-1", "summary": "Add widget", "issue_type": "Feature Request"},
        body="Widget requirements here.",
    )
    _write_manifest(
        tmp_path / "artifacts" / "doccontext.md",
        {"starting_issue": "PROJ-1",
         "jira_issues": [{"key": "PROJ-1", "path": str(jc_dir / "PROJ-1.md")}],
         "pull_requests": [], "code_repositories": [], "additional_links": []},
        body="Feature overview text.",
    )


def test_golden_path(tmp_path):
    _setup_minimal(tmp_path)
    result = _run_prepare(tmp_path)
    assert result.returncode == 0
    text = (tmp_path / "artifacts" / "docplan" / "planner-input.md").read_text()
    assert "Widget requirements here." in text
    assert "Feature overview text." in text
    summary = json.loads(result.stdout.strip())
    assert summary["jira_issues"] == 1


def test_relevant_pr_sections_included(tmp_path):
    _setup_minimal(tmp_path)
    pr_dir = tmp_path / "artifacts" / "prcontext"
    pr_dir.mkdir(parents=True, exist_ok=True)
    (pr_dir / "filtered").mkdir()
    _write_manifest(
        pr_dir / "org__repo__10.md",
        {"verdict": "relevant", "gist": "Adds widget API"},
        body="## What changed\nNew endpoint added.\n\n## Documentation impact\nNeeds API reference.",
    )
    dc_path = tmp_path / "artifacts" / "doccontext.md"
    text = dc_path.read_text()
    _, fm_raw, body = text.split("---\n", 2)
    fm = yaml.safe_load(fm_raw)
    fm["pull_requests"] = [{
        "url": "https://github.com/org/repo/pull/10",
        "title": "Add widget endpoint",
        "verdict": "relevant",
        "gist": "Adds widget API",
        "filtered_patch": str(pr_dir / "filtered" / "org__repo__10.patch"),
    }]
    _write_manifest(dc_path, fm, body.strip())

    _run_prepare(tmp_path)
    text = (tmp_path / "artifacts" / "docplan" / "planner-input.md").read_text()
    assert "New endpoint added." in text
    assert "Needs API reference." in text


def test_missing_doccontext_exits_2(tmp_path):
    assert _run_prepare(tmp_path).returncode == 2
