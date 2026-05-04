"""Tests for doc_context_bootstrap.py — consolidates upstream context into doccontext.md."""
import os
import subprocess
import sys

import yaml


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BOOTSTRAP_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "doc_context_bootstrap.py")


def _write_manifest(path, fm, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False)
        + "---\n\n" + body + "\n"
    )


def _parse_manifest(path):
    text = path.read_text()
    assert text.startswith("---\n")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _run_bootstrap(art_dir):
    return subprocess.run(
        [sys.executable, BOOTSTRAP_SCRIPT,
         "--jiracontext-manifest", str(art_dir / "artifacts" / "jiracontext.md"),
         "--prcontext-manifest", str(art_dir / "artifacts" / "prcontext.md"),
         "--codecontext-dir", str(art_dir / "artifacts" / "codecontext"),
         "--output", str(art_dir / "artifacts" / "doccontext.md")],
        capture_output=True, text=True,
    )


def _setup_minimal(art_dir):
    jc_dir = art_dir / "artifacts" / "jiracontext"
    jc_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(
        art_dir / "artifacts" / "jiracontext.md",
        {"starting_issue": "PROJ-1", "output_directory": str(jc_dir),
         "code_repositories": []},
        body="Feature description here.",
    )
    _write_manifest(
        jc_dir / "PROJ-1.md",
        {"jira_key": "PROJ-1", "summary": "Test issue"},
        body="Issue body.",
    )
    (art_dir / "artifacts" / "codecontext").mkdir(parents=True, exist_ok=True)


def test_golden_path(tmp_path):
    _setup_minimal(tmp_path)
    result = _run_bootstrap(tmp_path)
    assert result.returncode == 0
    fm, body = _parse_manifest(tmp_path / "artifacts" / "doccontext.md")
    assert fm["starting_issue"] == "PROJ-1"
    assert fm["jira_issues"][0]["key"] == "PROJ-1"
    assert "Feature description here." in body


def test_repo_included_only_if_cloned(tmp_path):
    _setup_minimal(tmp_path)
    jc_manifest = tmp_path / "artifacts" / "jiracontext.md"
    fm, body = _parse_manifest(jc_manifest)
    fm["code_repositories"] = [
        "https://github.com/org/cloned",
        "https://github.com/org/missing",
    ]
    _write_manifest(jc_manifest, fm, body)
    (tmp_path / "artifacts" / "codecontext" / "org--cloned").mkdir(parents=True)

    _run_bootstrap(tmp_path)
    out_fm, _ = _parse_manifest(tmp_path / "artifacts" / "doccontext.md")
    assert len(out_fm["code_repositories"]) == 1
    assert out_fm["code_repositories"][0]["url"] == "https://github.com/org/cloned"


def test_missing_jiracontext_exits_2(tmp_path):
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    assert _run_bootstrap(tmp_path).returncode == 2
