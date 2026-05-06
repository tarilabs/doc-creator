"""Tests for doc_review_prepare.py — prepares review prompts and snapshots."""
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PREPARE_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "doc_review_prepare.py")


def _write_writer_config(config_path, modules, target_repo=None, repo_profile=None):
    """Write a writer-config.json for testing."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if repo_profile is None:
        repo_profile = {
            "framework": "asciidoc",
            "file_extension": ".adoc",
            "modules_dir": "modules",
            "assemblies_dir": "assemblies",
            "product_attributes_file": None,
        }
    config = {
        "target_repo": target_repo or str(config_path.parent),
        "mode": "write",
        "repo_profile": repo_profile,
        "modules": modules,
    }
    config_path.write_text(json.dumps(config, indent=2))


def _write_adoc(path, content):
    """Write an AsciiDoc file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _write_guidelines(guidelines_dir):
    """Write minimal guideline files."""
    guidelines_dir.mkdir(parents=True, exist_ok=True)
    (guidelines_dir / "ibm-sg-test.md").write_text(
        "---\ndescription: Test IBM guideline\n---\n\n# IBM Test\n\n- [ ] Rule one\n"
    )
    (guidelines_dir / "rh-ssg-test.md").write_text(
        "---\ndescription: Test RH guideline\n---\n\n# RH Test\n\n- [ ] Rule two\n"
    )


def _write_doccontext(doccontext_path):
    """Write minimal doccontext.md."""
    doccontext_path.parent.mkdir(parents=True, exist_ok=True)
    doccontext_path.write_text(
        "---\nstarting_issue: TEST-1\njira_issues: []\npull_requests: []\n---\n\nTest context.\n"
    )


def _run_prepare(config_path, doccontext_path, output_dir, guidelines_dir):
    return subprocess.run(
        [
            sys.executable, PREPARE_SCRIPT,
            "--config", str(config_path),
            "--doccontext", str(doccontext_path),
            "--output-dir", str(output_dir),
            "--guidelines-dir", str(guidelines_dir),
        ],
        capture_output=True, text=True,
    )


def test_successful_preparation(tmp_path):
    adoc = tmp_path / "modules" / "test-module.adoc"
    _write_adoc(adoc, ":_mod-docs-content-type: CONCEPT\n= Test\n\nContent.\n")

    config_path = tmp_path / "writer-config.json"
    _write_writer_config(config_path, [
        {"slug": "test-module", "title": "Test Module", "type": "concept",
         "target_path": str(adoc), "evidence_confidence": "strong",
         "prompt_file": ""},
    ])

    guidelines_dir = tmp_path / "guidelines"
    _write_guidelines(guidelines_dir)

    doccontext_path = tmp_path / "doccontext.md"
    _write_doccontext(doccontext_path)

    output_dir = tmp_path / "docreview"

    result = _run_prepare(config_path, doccontext_path, output_dir, guidelines_dir)

    assert result.returncode == 0 or result.returncode == 1

    output = json.loads(result.stdout.strip().split("\n")[-1])
    assert output["module_count"] == 1
    assert output["has_guidelines"] is True


def test_snapshots_created(tmp_path):
    adoc = tmp_path / "modules" / "snap-test.adoc"
    _write_adoc(adoc, "Test content for snapshot.\n")

    config_path = tmp_path / "writer-config.json"
    _write_writer_config(config_path, [
        {"slug": "snap-test", "title": "Snap Test", "type": "concept",
         "target_path": str(adoc), "evidence_confidence": "moderate",
         "prompt_file": ""},
    ])

    guidelines_dir = tmp_path / "guidelines"
    _write_guidelines(guidelines_dir)
    doccontext_path = tmp_path / "doccontext.md"
    _write_doccontext(doccontext_path)
    output_dir = tmp_path / "docreview"

    _run_prepare(config_path, doccontext_path, output_dir, guidelines_dir)

    snapshot = output_dir / "snapshots" / "snap-test.adoc"
    assert snapshot.exists()
    assert snapshot.read_text() == "Test content for snapshot.\n"


def test_style_rubric_consolidated(tmp_path):
    adoc = tmp_path / "modules" / "rubric-test.adoc"
    _write_adoc(adoc, "Content.\n")

    config_path = tmp_path / "writer-config.json"
    _write_writer_config(config_path, [
        {"slug": "rubric-test", "title": "Rubric Test", "type": "concept",
         "target_path": str(adoc), "evidence_confidence": "strong",
         "prompt_file": ""},
    ])

    guidelines_dir = tmp_path / "guidelines"
    _write_guidelines(guidelines_dir)
    doccontext_path = tmp_path / "doccontext.md"
    _write_doccontext(doccontext_path)
    output_dir = tmp_path / "docreview"

    _run_prepare(config_path, doccontext_path, output_dir, guidelines_dir)

    rubric = output_dir / "style-rubric.md"
    assert rubric.exists()
    content = rubric.read_text()
    assert "Precedence" in content
    assert "Rule one" in content
    assert "Rule two" in content


def test_prompt_files_generated(tmp_path):
    adoc = tmp_path / "modules" / "prompt-test.adoc"
    _write_adoc(adoc, "Content.\n")

    config_path = tmp_path / "writer-config.json"
    _write_writer_config(config_path, [
        {"slug": "prompt-test", "title": "Prompt Test", "type": "procedure",
         "target_path": str(adoc), "evidence_confidence": "weak",
         "prompt_file": ""},
    ])

    guidelines_dir = tmp_path / "guidelines"
    _write_guidelines(guidelines_dir)
    doccontext_path = tmp_path / "doccontext.md"
    _write_doccontext(doccontext_path)
    output_dir = tmp_path / "docreview"

    _run_prepare(config_path, doccontext_path, output_dir, guidelines_dir)

    assert (output_dir / "prompt-test.style-prompt.md").exists()
    assert (output_dir / "prompt-test.technical-prompt.md").exists()


def test_reviewer_config_structure(tmp_path):
    adoc = tmp_path / "modules" / "config-test.adoc"
    _write_adoc(adoc, "Content.\n")

    config_path = tmp_path / "writer-config.json"
    _write_writer_config(config_path, [
        {"slug": "config-test", "title": "Config Test", "type": "concept",
         "target_path": str(adoc), "evidence_confidence": "strong",
         "prompt_file": ""},
    ])

    guidelines_dir = tmp_path / "guidelines"
    _write_guidelines(guidelines_dir)
    doccontext_path = tmp_path / "doccontext.md"
    _write_doccontext(doccontext_path)
    output_dir = tmp_path / "docreview"

    _run_prepare(config_path, doccontext_path, output_dir, guidelines_dir)

    rc_path = output_dir / "reviewer-config.json"
    assert rc_path.exists()

    rc = json.loads(rc_path.read_text())
    assert "source_config" in rc
    assert "snapshot_dir" in rc
    assert "review_started_at" in rc
    assert "modules" in rc
    assert len(rc["modules"]) == 1

    mod = rc["modules"][0]
    assert mod["slug"] == "config-test"
    assert "style_prompt" in mod
    assert "technical_prompt" in mod
    assert "codecontext_dirs" in mod


def test_missing_written_file_exits_2(tmp_path):
    config_path = tmp_path / "writer-config.json"
    _write_writer_config(config_path, [
        {"slug": "missing", "title": "Missing", "type": "concept",
         "target_path": str(tmp_path / "nonexistent.adoc"),
         "evidence_confidence": "strong", "prompt_file": ""},
    ])

    guidelines_dir = tmp_path / "guidelines"
    _write_guidelines(guidelines_dir)
    doccontext_path = tmp_path / "doccontext.md"
    _write_doccontext(doccontext_path)
    output_dir = tmp_path / "docreview"

    result = _run_prepare(config_path, doccontext_path, output_dir, guidelines_dir)
    assert result.returncode == 2


def test_missing_config_exits_2(tmp_path):
    result = subprocess.run(
        [sys.executable, PREPARE_SCRIPT, "--config", str(tmp_path / "nonexistent.json"),
         "--guidelines-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2


def test_no_guidelines_warns(tmp_path):
    adoc = tmp_path / "modules" / "no-guide.adoc"
    _write_adoc(adoc, "Content.\n")

    config_path = tmp_path / "writer-config.json"
    _write_writer_config(config_path, [
        {"slug": "no-guide", "title": "No Guide", "type": "concept",
         "target_path": str(adoc), "evidence_confidence": "strong",
         "prompt_file": ""},
    ])

    empty_guidelines = tmp_path / "empty-guidelines"
    empty_guidelines.mkdir()
    doccontext_path = tmp_path / "doccontext.md"
    _write_doccontext(doccontext_path)
    output_dir = tmp_path / "docreview"

    result = _run_prepare(config_path, doccontext_path, output_dir, empty_guidelines)
    assert result.returncode == 1

    output = json.loads(result.stdout.strip().split("\n")[-1])
    assert output["has_guidelines"] is False
    assert len(output["warnings"]) > 0
