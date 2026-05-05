"""Tests for doc_write_verify.py — validates written documentation files."""
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VERIFY_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "doc_write_verify.py")


def _write_config(config_path, modules, repo_profile=None):
    """Write a writer-config.json for testing."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if repo_profile is None:
        repo_profile = {
            "framework": "asciidoc",
            "file_extension": ".adoc",
            "modules_dir": "modules",
            "assemblies_dir": "assemblies",
            "product_attributes_file": None,
            "claude_md_path": None,
        }
    config = {
        "target_repo": str(config_path.parent),
        "mode": "draft",
        "repo_profile": repo_profile,
        "modules": modules,
    }
    config_path.write_text(json.dumps(config, indent=2))


def _write_adoc(path, content):
    """Write an AsciiDoc file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _run_verify(config_path):
    return subprocess.run(
        [sys.executable, VERIFY_SCRIPT, "--config", str(config_path)],
        capture_output=True, text=True,
    )


def test_valid_concept_passes(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        ':_mod-docs-content-type: CONCEPT\n'
        '[id="test_{context}"]\n'
        '= Test module\n\n'
        '[role="_abstract"]\n'
        'Test abstract.\n'
    ))
    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["passed"] == 1
    assert output["failed"] == 0


def test_missing_file_fails(tmp_path):
    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "missing", "type": "concept", "target_path": str(tmp_path / "nonexistent.adoc")},
    ])

    result = _run_verify(config_path)
    assert result.returncode == 2
    output = json.loads(result.stdout)
    assert output["failed"] == 1


def test_placeholder_detected(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        ':_mod-docs-content-type: CONCEPT\n'
        '[id="test_{context}"]\n'
        '= Test module\n\n'
        '[role="_abstract"]\n'
        'Contains [TODO] placeholder.\n'
    ))
    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    assert result.returncode == 2
    output = json.loads(result.stdout)
    assert any("placeholder" in e["message"].lower() for e in output["modules"][0]["errors"])


def test_missing_content_type_attr(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        '[id="test_{context}"]\n'
        '= Test module\n\n'
        '[role="_abstract"]\n'
        'No content type attribute.\n'
    ))
    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)
    assert output["total_errors"] > 0


def test_wrong_content_type_warns(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        ':_mod-docs-content-type: PROCEDURE\n'
        '[id="test_{context}"]\n'
        '= Test module\n\n'
        '[role="_abstract"]\n'
        'Marked as procedure but expected concept.\n'
    ))
    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)
    assert output["total_warnings"] > 0


def test_deep_heading_warns(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        ':_mod-docs-content-type: CONCEPT\n'
        '[id="test_{context}"]\n'
        '= Test module\n\n'
        '[role="_abstract"]\n'
        'Content.\n\n'
        '=== Too deep\n\n'
        'This heading is too deep.\n'
    ))
    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)
    assert output["total_warnings"] > 0
    assert any("heading" in w["message"].lower() for w in output["modules"][0]["warnings"])


def test_procedure_missing_sections_warns(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        ':_mod-docs-content-type: PROCEDURE\n'
        '[id="test_{context}"]\n'
        '= Configure something\n\n'
        '[role="_abstract"]\n'
        'Steps without structure.\n'
    ))
    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "procedure", "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)
    assert output["total_warnings"] > 0
    assert any("Prerequisites" in w["message"] for w in output["modules"][0]["warnings"])


def test_mkdocs_skips_asciidoc_checks(tmp_path):
    md_file = tmp_path / "docs" / "test.md"
    _write_adoc(md_file, "# Test\n\nContent without AsciiDoc attributes.\n")

    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(md_file)},
    ], repo_profile={
        "framework": "mkdocs",
        "file_extension": ".md",
        "modules_dir": "docs",
        "assemblies_dir": None,
        "product_attributes_file": None,
        "claude_md_path": None,
    })

    result = _run_verify(config_path)
    output = json.loads(result.stdout)
    skipped = output["modules"][0]["checks_skipped"]
    assert any(s["check"] == "content_type_attr" for s in skipped)


def test_hardcoded_product_name_warns(tmp_path):
    attrs_file = tmp_path / "attrs.adoc"
    attrs_file.write_text(':productname-short: OpenShift AI\n')

    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        ':_mod-docs-content-type: CONCEPT\n'
        '[id="test_{context}"]\n'
        '= Test module\n\n'
        '[role="_abstract"]\n'
        'OpenShift AI provides great features.\n'
    ))

    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(adoc)},
    ], repo_profile={
        "framework": "asciidoc",
        "file_extension": ".adoc",
        "modules_dir": "modules",
        "assemblies_dir": "assemblies",
        "product_attributes_file": str(attrs_file),
        "claude_md_path": None,
    })

    result = _run_verify(config_path)
    output = json.loads(result.stdout)
    assert output["total_warnings"] > 0
    assert any("hardcoded" in w["message"].lower() for w in output["modules"][0]["warnings"])


def test_no_product_attrs_skips_check(tmp_path):
    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        ':_mod-docs-content-type: CONCEPT\n'
        '[id="test_{context}"]\n'
        '= Test module\n\n'
        '[role="_abstract"]\n'
        'Content.\n'
    ))

    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    output = json.loads(result.stdout)
    skipped = output["modules"][0]["checks_skipped"]
    assert any(s["check"] == "hardcoded_product_name" for s in skipped)


def test_missing_config_exits_2(tmp_path):
    result = _run_verify(tmp_path / "nonexistent.json")
    assert result.returncode == 2


def test_module_type_attribute_variant(tmp_path):
    """Both :_mod-docs-content-type: and :_module-type: should be accepted."""
    adoc = tmp_path / "modules" / "test.adoc"
    _write_adoc(adoc, (
        ':_module-type: CONCEPT\n'
        '[id="test_{context}"]\n'
        '= Test module\n\n'
        '[role="_abstract"]\n'
        'Content.\n'
    ))

    config_path = tmp_path / "writer-config.json"
    _write_config(config_path, [
        {"slug": "test", "type": "concept", "target_path": str(adoc)},
    ])

    result = _run_verify(config_path)
    assert result.returncode == 0
