"""Tests for doc_review_verify.py — validates findings and generates report."""
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VERIFY_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "doc_review_verify.py")


def _write_reviewer_config(config_path, modules, snapshot_dir="snapshots"):
    """Write a reviewer-config.json for testing."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "source_config": "writer-config.json",
        "target_repo": str(config_path.parent),
        "snapshot_dir": str(config_path.parent / snapshot_dir),
        "review_started_at": "2026-05-05T10:00:00Z",
        "style_rubric": "style-rubric.md",
        "format_reference": None,
        "repo_profile": {"framework": "asciidoc"},
        "modules": modules,
    }
    config_path.write_text(json.dumps(config, indent=2))


def _write_findings(path, module, review_type, findings, verdict="pass"):
    """Write a findings JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "module": module,
        "review_type": review_type,
        "findings": findings,
        "changes_applied": sum(1 for f in findings if f.get("action") == "fixed"),
        "changes_reported": sum(1 for f in findings if f.get("action") == "reported"),
        "verdict": verdict,
        "summary": "Test summary",
    }
    path.write_text(json.dumps(data, indent=2))


def _write_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _run_verify(config_path):
    return subprocess.run(
        [sys.executable, VERIFY_SCRIPT, "--config", str(config_path)],
        capture_output=True, text=True,
    )


def test_all_pass_exit_0(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Content after review.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Content after review.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    _write_findings(
        tmp_path / "test.style-findings.json",
        "test", "style", [], "pass",
    )
    _write_findings(
        tmp_path / "test.technical-findings.json",
        "test", "technical", [], "pass",
    )

    result = _run_verify(config_path)
    assert result.returncode == 0

    output = json.loads(result.stdout)
    assert output["verdicts"]["test"] == "pass"


def test_minor_findings_exit_1(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Content.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Content.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    _write_findings(
        tmp_path / "test.style-findings.json",
        "test", "style",
        [{"severity": "minor", "category": "style_violation",
          "description": "Minor style issue", "action": "reported"}],
        "pass_with_warnings",
    )
    _write_findings(
        tmp_path / "test.technical-findings.json",
        "test", "technical", [], "pass",
    )

    result = _run_verify(config_path)
    assert result.returncode == 1

    output = json.loads(result.stdout)
    assert output["verdicts"]["test"] == "pass_with_warnings"


def test_critical_findings_exit_2(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Content.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Content.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    _write_findings(
        tmp_path / "test.style-findings.json",
        "test", "style", [], "pass",
    )
    _write_findings(
        tmp_path / "test.technical-findings.json",
        "test", "technical",
        [{"severity": "critical", "category": "hallucination",
          "description": "Hallucinated API field", "action": "reported"}],
        "fail",
    )

    result = _run_verify(config_path)
    assert result.returncode == 2

    output = json.loads(result.stdout)
    assert output["verdicts"]["test"] == "fail"


def test_fixed_findings_dont_affect_verdict(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Fixed content.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Original content.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    _write_findings(
        tmp_path / "test.style-findings.json",
        "test", "style",
        [{"severity": "major", "category": "style_violation",
          "description": "Fixed a major issue", "action": "fixed"}],
        "pass",
    )
    _write_findings(
        tmp_path / "test.technical-findings.json",
        "test", "technical", [], "pass",
    )

    result = _run_verify(config_path)
    assert result.returncode == 0

    output = json.loads(result.stdout)
    assert output["verdicts"]["test"] == "pass"


def test_diff_metrics_computed(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Line one.\nLine two modified.\nLine three new.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Line one.\nLine two original.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    _write_findings(tmp_path / "test.style-findings.json", "test", "style", [])
    _write_findings(tmp_path / "test.technical-findings.json", "test", "technical", [])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)

    dm = output["diff_metrics"]["test"]
    assert dm["diffable"] is True
    assert dm["total_changes"] > 0
    assert dm["unchanged"] is False


def test_unchanged_module_diff(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    content = "Same content.\n"
    _write_file(adoc, content)
    _write_file(tmp_path / "snapshots" / "test.adoc", content)

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    _write_findings(tmp_path / "test.style-findings.json", "test", "style", [])
    _write_findings(tmp_path / "test.technical-findings.json", "test", "technical", [])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)

    dm = output["diff_metrics"]["test"]
    assert dm["unchanged"] is True


def test_report_generated(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Content.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Content.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    _write_findings(tmp_path / "test.style-findings.json", "test", "style", [])
    _write_findings(tmp_path / "test.technical-findings.json", "test", "technical", [])

    _run_verify(config_path)

    report = tmp_path / "review-report.md"
    assert report.exists()
    report_content = report.read_text()
    assert "Documentation Review Report" in report_content
    assert "test" in report_content


def test_aggregation_counts(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Content.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Content.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    _write_findings(
        tmp_path / "test.style-findings.json",
        "test", "style",
        [
            {"severity": "minor", "category": "style_violation",
             "description": "Issue 1", "action": "fixed"},
            {"severity": "minor", "category": "formatting_issue",
             "description": "Issue 2", "action": "fixed"},
            {"severity": "info", "category": "terminology_issue",
             "description": "Info 1", "action": "reported"},
        ],
    )
    _write_findings(
        tmp_path / "test.technical-findings.json",
        "test", "technical",
        [{"severity": "minor", "category": "ungrounded_claim",
          "description": "No evidence", "action": "skipped"}],
    )

    result = _run_verify(config_path)
    output = json.loads(result.stdout)

    agg = output["aggregation"]
    assert agg["total_findings"] == 4
    assert agg["by_action"]["fixed"] == 2
    assert agg["by_action"]["reported"] == 1
    assert agg["by_action"]["skipped"] == 1


def test_missing_findings_reported(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Content.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Content.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)

    assert len(output["validation_errors"]) > 0


def test_missing_config_exits_2(tmp_path):
    result = _run_verify(tmp_path / "nonexistent.json")
    assert result.returncode == 2


def test_invalid_json_findings(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_file(adoc, "Content.\n")
    _write_file(tmp_path / "snapshots" / "test.adoc", "Content.\n")

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, [
        {"slug": "test", "title": "Test", "type": "concept",
         "target_path": str(adoc)},
    ])

    bad_findings = tmp_path / "test.style-findings.json"
    bad_findings.write_text("not valid json {{{")
    _write_findings(tmp_path / "test.technical-findings.json", "test", "technical", [])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)

    assert len(output["validation_errors"]) > 0


def test_multiple_modules(tmp_path):
    modules = []
    for slug in ["mod-a", "mod-b", "mod-c"]:
        adoc = tmp_path / "modules" / f"{slug}.adoc"
        _write_file(adoc, f"Content for {slug}.\n")
        _write_file(tmp_path / "snapshots" / f"{slug}.adoc", f"Content for {slug}.\n")
        modules.append({
            "slug": slug, "title": slug.title(), "type": "concept",
            "target_path": str(adoc),
        })

        _write_findings(
            tmp_path / f"{slug}.style-findings.json", slug, "style", [],
        )
        _write_findings(
            tmp_path / f"{slug}.technical-findings.json", slug, "technical", [],
        )

    config_path = tmp_path / "reviewer-config.json"
    _write_reviewer_config(config_path, modules)

    result = _run_verify(config_path)
    assert result.returncode == 0

    output = json.loads(result.stdout)
    assert output["total_modules"] == 3
    assert output["modules_reviewed"] == 3
