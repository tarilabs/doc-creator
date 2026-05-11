"""Tests for jira_ai1st_doc_start_trigger.py against jira-emulator."""
import os
import subprocess
import sys

import pytest


SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "jira_ai1st_doc_start_trigger.py")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _env(jira):
    return {
        **os.environ,
        "JIRA_SERVER": jira.url,
        "JIRA_USER": "admin",
        "JIRA_TOKEN": "admin",
    }


def _fake_glab(tmp_path, exit_code=0):
    """Create a fake glab script that returns a canned response."""
    fake = tmp_path / "glab"
    if exit_code == 0:
        fake.write_text(
            '#!/bin/sh\n'
            'echo "Created pipeline (id: 123), status: created, ref: main, '
            'weburl: https://gitlab.com/org/repo/-/pipelines/123"\n'
        )
    else:
        fake.write_text(
            '#!/bin/sh\n'
            'echo "ERROR: something went wrong" >&2\n'
            f'exit {exit_code}\n'
        )
    fake.chmod(0o755)
    return str(tmp_path)


def _run(jira, tmp_path, extra_args=(), glab_exit=0):
    env = _env(jira)
    env["PATH"] = _fake_glab(tmp_path, glab_exit) + ":" + env.get("PATH", "")
    return subprocess.run(
        [sys.executable, SCRIPT, "--projects", "TESTPROJ", *extra_args],
        capture_output=True, text=True, env=env, cwd=PROJECT_ROOT,
    )


def test_trigger_swaps_label_and_skips_dual_labeled(jira, tmp_path):
    jira.create("TESTPROJ-1", "Ready", "desc",
                 labels=["ai1st-doc-start"])
    jira.create("TESTPROJ-2", "Both labels", "desc",
                 labels=["ai1st-doc-start", "ai1st-doc-invoked"])

    result = _run(jira, tmp_path, ["--trigger"])

    assert result.returncode == 0
    assert "SKIPPED" in result.stderr
    assert "TESTPROJ-2" in result.stderr

    issue1 = jira.get("TESTPROJ-1")
    assert "ai1st-doc-invoked" in issue1["fields"]["labels"]
    assert "ai1st-doc-start" not in issue1["fields"]["labels"]

    issue2 = jira.get("TESTPROJ-2")
    assert "ai1st-doc-start" in issue2["fields"]["labels"]


def test_trigger_failure_keeps_label(jira, tmp_path):
    jira.create("TESTPROJ-10", "Will fail", "desc",
                 labels=["ai1st-doc-start"])

    result = _run(jira, tmp_path, ["--trigger"], glab_exit=1)

    assert result.returncode == 1
    issue = jira.get("TESTPROJ-10")
    assert "ai1st-doc-start" in issue["fields"]["labels"]
    assert "ai1st-doc-invoked" not in issue["fields"]["labels"]
