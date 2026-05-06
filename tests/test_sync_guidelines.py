"""Tests for sync_guidelines.py — copies style guidelines from source repo."""
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYNC_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "sync_guidelines.py")


def _create_source_structure(source_dir):
    """Create a minimal redhat-docs-agent-tools skill structure."""
    skills_dir = source_dir / "plugins" / "docs-tools" / "skills"

    ibm_skill = skills_dir / "ibm-sg-test-skill"
    ibm_skill.mkdir(parents=True)
    (ibm_skill / "SKILL.md").write_text(
        "---\ncontext: fork\nname: ibm-sg-test-skill\n"
        "description: Test IBM skill\n---\n\n# IBM Test\n\n- [ ] Check one\n"
    )

    rh_skill = skills_dir / "rh-ssg-test-skill"
    rh_skill.mkdir(parents=True)
    (rh_skill / "SKILL.md").write_text(
        "---\ncontext: fork\nname: rh-ssg-test-skill\n"
        "description: Test RH skill\n---\n\n# RH Test\n\n- [ ] Check two\n"
    )

    other_skill = skills_dir / "other-skill"
    other_skill.mkdir(parents=True)
    (other_skill / "SKILL.md").write_text("---\nname: other\n---\n\n# Other\n")

    return skills_dir


def _run_sync(source, output):
    return subprocess.run(
        [sys.executable, SYNC_SCRIPT, "--source", str(source), "--output", str(output)],
        capture_output=True, text=True,
    )


def test_copies_matching_skills(tmp_path):
    source = tmp_path / "source"
    _create_source_structure(source)
    output = tmp_path / "output"

    result = _run_sync(source, output)
    assert result.returncode == 0

    assert (output / "ibm-sg-test-skill.md").exists()
    assert (output / "rh-ssg-test-skill.md").exists()
    assert not (output / "other-skill.md").exists()


def test_strips_context_and_name_fields(tmp_path):
    source = tmp_path / "source"
    _create_source_structure(source)
    output = tmp_path / "output"

    _run_sync(source, output)

    content = (output / "ibm-sg-test-skill.md").read_text()
    assert "context: fork" not in content
    assert "name: ibm-sg-test-skill" not in content
    assert "description: Test IBM skill" in content


def test_preserves_body_content(tmp_path):
    source = tmp_path / "source"
    _create_source_structure(source)
    output = tmp_path / "output"

    _run_sync(source, output)

    content = (output / "rh-ssg-test-skill.md").read_text()
    assert "# RH Test" in content
    assert "- Check two" in content


def test_missing_source_exits_2(tmp_path):
    result = _run_sync(tmp_path / "nonexistent", tmp_path / "output")
    assert result.returncode == 2


def test_empty_skills_dir_exits_2(tmp_path):
    source = tmp_path / "source" / "plugins" / "docs-tools" / "skills"
    source.mkdir(parents=True)
    result = _run_sync(tmp_path / "source", tmp_path / "output")
    assert result.returncode == 2


def test_strips_checkboxes(tmp_path):
    source = tmp_path / "source"
    _create_source_structure(source)
    output = tmp_path / "output"

    _run_sync(source, output)

    content = (output / "ibm-sg-test-skill.md").read_text()
    assert "- [ ] " not in content
    assert "- Check one" in content


def test_creates_output_dir(tmp_path):
    source = tmp_path / "source"
    _create_source_structure(source)
    output = tmp_path / "deep" / "nested" / "output"

    result = _run_sync(source, output)
    assert result.returncode == 0
    assert output.is_dir()
