"""Tests for doc_plan_verify.py — validates documentation plan structure."""
import json
import os
import subprocess
import sys
import textwrap

import yaml


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VERIFY_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "doc_plan_verify.py")

VALID_FM = {
    "starting_issue": "PROJ-100",
    "created_at": "2025-01-01T00:00:00Z",
    "feature_name": "Test Feature",
    "personas": ["Developer", "Admin"],
    "module_count": 1,
    "dev_preview": True,
}

VALID_BODY = textwrap.dedent("""\
    ## Executive Summary
    Some summary.

    ## Personas
    Two personas.

    ## User Journey
    Step by step.

    ## Planned Modules

    ### Module: Getting Started
    Content here.

    ## Deferred Topics
    None.

    ## Unverified Topics
    None.
""")


def _write_plan(tmp_path, fm, body):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False)
        + "---\n\n" + body + "\n"
    )
    return str(plan_path)


def _run_verify(plan_path):
    result = subprocess.run(
        [sys.executable, VERIFY_SCRIPT, "--plan", plan_path],
        capture_output=True, text=True,
    )
    last_line = result.stdout.strip().split("\n")[-1]
    report = json.loads(last_line)
    return result.returncode, report


def test_clean_plan_exits_zero(tmp_path):
    plan = _write_plan(tmp_path, VALID_FM, VALID_BODY)
    rc, report = _run_verify(plan)
    assert rc == 0
    assert report["errors"] == 0 and report["warnings"] == 0


def test_missing_field_exits_2(tmp_path):
    fm = {**VALID_FM}
    del fm["feature_name"]
    plan = _write_plan(tmp_path, fm, VALID_BODY)
    rc, report = _run_verify(plan)
    assert rc == 2
    assert any("feature_name" in e for e in report["error_details"])


def test_module_count_mismatch_exits_1(tmp_path):
    fm = {**VALID_FM, "module_count": 5}
    plan = _write_plan(tmp_path, fm, VALID_BODY)
    rc, report = _run_verify(plan)
    assert rc == 1
    assert any("mismatch" in w.lower() for w in report["warning_details"])


def test_missing_file_exits_2(tmp_path):
    result = subprocess.run(
        [sys.executable, VERIFY_SCRIPT, "--plan", str(tmp_path / "nope.md")],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
