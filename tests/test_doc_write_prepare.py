"""Tests for doc_write_prepare.py — prepares per-module prompts for writer agents."""
import json
import os
import subprocess
import sys

import yaml


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
PREPARE_SCRIPT = os.path.join(SCRIPTS_DIR, "doc_write_prepare.py")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _write_manifest(path, fm, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        + yaml.dump(fm, default_flow_style=False, sort_keys=False)
        + "---\n\n" + body + "\n"
    )


def _make_target_repo(tmp_path, framework="asciidoc"):
    """Create a minimal mock target documentation repository."""
    repo = tmp_path / "target-repo"
    repo.mkdir()

    if framework == "asciidoc":
        (repo / "_artifacts").mkdir()
        (repo / "_artifacts" / "document-attributes-global.adoc").write_text(
            ':_mod-docs-content-type: SNIPPET\n'
            ':productname-short: TestProduct\n'
            ':productname-long: Red Hat TestProduct\n'
            ':vernum: 1.0\n'
        )
        (repo / "modules").mkdir()
        (repo / "assemblies").mkdir()
        (repo / "CLAUDE.md").write_text("# Test repo\n\nUse sentence case.\n")

        (repo / "modules" / "about-widgets.adoc").write_text(
            ':_mod-docs-content-type: CONCEPT\n'
            '[id="about-widgets_{context}"]\n'
            '= About widgets\n\n'
            '[role="_abstract"]\n'
            'Widgets overview.\n'
        )
        (repo / "modules" / "configuring-widgets.adoc").write_text(
            ':_mod-docs-content-type: PROCEDURE\n'
            '[id="configuring-widgets_{context}"]\n'
            '= Configure widgets\n\n'
            '[role="_abstract"]\n'
            'Steps to configure.\n\n'
            '.Prerequisites\n\n'
            '* Admin access\n\n'
            '.Procedure\n\n'
            '. Click configure.\n\n'
            '.Verification\n\n'
            '* Widget appears.\n'
        )
        (repo / "modules" / "widget-configuration-reference.adoc").write_text(
            ':_mod-docs-content-type: REFERENCE\n'
            '[id="widget-configuration-reference_{context}"]\n'
            '= Widget configuration reference\n\n'
            '[role="_abstract"]\n'
            'Reference data.\n'
        )
    elif framework == "mkdocs":
        (repo / "mkdocs.yml").write_text("site_name: Test\n")
        (repo / "docs").mkdir()
        (repo / "docs" / "index.md").write_text("# Test\n\nWelcome.\n")

    return str(repo)


def _setup_minimal(tmp_path):
    """Create minimal docplan + doccontext + target repo."""
    jc_dir = tmp_path / "artifacts" / "jiracontext"
    jc_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(
        jc_dir / "PROJ-1.md",
        {"jira_key": "PROJ-1", "summary": "Add catalog", "issue_type": "Feature Request"},
        body="Catalog requirements.\n",
    )

    pr_dir = tmp_path / "artifacts" / "prcontext"
    pr_dir.mkdir(parents=True, exist_ok=True)
    (pr_dir / "filtered").mkdir()
    _write_manifest(
        pr_dir / "org__repo__10.md",
        {"verdict": "relevant", "gist": "Adds catalog API"},
        body="## What changed\nNew endpoint.\n\n## Documentation impact\nNeeds reference doc.",
    )

    _write_manifest(
        tmp_path / "artifacts" / "doccontext.md",
        {
            "starting_issue": "PROJ-1",
            "jira_issues": [{"key": "PROJ-1", "path": str(jc_dir / "PROJ-1.md")}],
            "pull_requests": [{
                "url": "https://github.com/org/repo/pull/10",
                "title": "Add catalog endpoint",
                "verdict": "relevant",
                "gist": "Adds catalog API",
                "filtered_patch": str(pr_dir / "filtered" / "org__repo__10.patch"),
            }],
            "code_repositories": [],
        },
        body="Feature overview.",
    )

    docplan_dir = tmp_path / "artifacts" / "docplan"
    docplan_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(
        docplan_dir / "docplan.md",
        {
            "starting_issue": "PROJ-1",
            "feature_name": "Test Feature",
            "module_count": 2,
        },
        body=(
            "# Documentation Plan: Test Feature\n\n"
            "## Planned Modules\n\n"
            "### Module: Catalog overview\n\n"
            "- **Type:** concept\n"
            "- **Persona:** admin\n"
            "- **Journey Phase:** discover\n"
            "- **Job Statement:** Understand the catalog\n"
            "- **Source Evidence:**\n"
            "  - JIRA: PROJ-1\n"
            "  - PRs: repo#10\n"
            "- **Content Outline:**\n"
            "  - What the catalog is\n"
            "  - How it works\n"
            "- **Prerequisites:** None\n"
            "- **Dev Preview Disclaimer:** required\n\n"
            "### Module: Configure the catalog\n\n"
            "- **Type:** procedure\n"
            "- **Persona:** admin\n"
            "- **Journey Phase:** deploy\n"
            "- **Job Statement:** Set up the catalog\n"
            "- **Source Evidence:**\n"
            "  - JIRA: PROJ-1\n"
            "  - PRs: repo#10\n"
            "- **Content Outline:**\n"
            "  - Step by step configuration\n"
            "- **Prerequisites:** Admin access\n"
            "- **Dev Preview Disclaimer:** required\n"
        ),
    )

    return _make_target_repo(tmp_path)


def _run_prepare(tmp_path, target_repo, extra_args=None):
    cmd = [
        sys.executable, PREPARE_SCRIPT,
        "--docplan", str(tmp_path / "artifacts" / "docplan" / "docplan.md"),
        "--doccontext", str(tmp_path / "artifacts" / "doccontext.md"),
        "--target-repo", target_repo,
        "--output-dir", str(tmp_path / "artifacts" / "docwrite"),
    ]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_path))


def test_golden_path(tmp_path):
    target_repo = _setup_minimal(tmp_path)
    result = _run_prepare(tmp_path, target_repo)
    assert result.returncode == 0, result.stderr

    summary = json.loads(result.stdout.strip())
    assert summary["module_count"] == 2
    assert summary["framework"] == "asciidoc"

    config_path = tmp_path / "artifacts" / "docwrite" / "writer-config.json"
    assert config_path.exists()
    config = json.loads(config_path.read_text())

    assert config["repo_profile"]["framework"] == "asciidoc"
    assert config["repo_profile"]["modules_dir"] == "modules"
    assert config["repo_profile"]["assemblies_dir"] == "assemblies"
    assert len(config["modules"]) == 2

    for mod in config["modules"]:
        prompt_path = mod["prompt_file"]
        assert os.path.exists(prompt_path)
        content = open(prompt_path).read()
        assert "Content Outline" in content
        assert "JIRA Evidence" in content
        assert "PR Evidence" in content


def test_module_parsing(tmp_path):
    target_repo = _setup_minimal(tmp_path)
    result = _run_prepare(tmp_path, target_repo)
    assert result.returncode == 0

    config = json.loads(
        (tmp_path / "artifacts" / "docwrite" / "writer-config.json").read_text()
    )
    modules = config["modules"]

    concept = next(m for m in modules if m["type"] == "concept")
    assert concept["slug"] == "catalog-overview"
    assert concept["title"] == "Catalog overview"

    proc = next(m for m in modules if m["type"] == "procedure")
    assert proc["slug"] == "configure-the-catalog"


def test_slug_kebab_case():
    from doc_write_prepare import _title_to_slug

    assert _title_to_slug("Introduction to the MCP Catalog") == "introduction-to-the-mcp-catalog"
    assert _title_to_slug("Browsing and Evaluating MCP Servers") == "browsing-and-evaluating-mcp-servers"
    assert _title_to_slug("MCP Server Configuration Reference") == "mcp-server-configuration-reference"


def test_slug_deduplication():
    from doc_write_prepare import _ensure_unique_slug

    existing = {"my-module", "my-module-2"}
    assert _ensure_unique_slug("my-module", existing) == "my-module-3"
    assert _ensure_unique_slug("new-module", existing) == "new-module"


def test_pr_reference_resolution(tmp_path):
    target_repo = _setup_minimal(tmp_path)
    result = _run_prepare(tmp_path, target_repo)
    assert result.returncode == 0

    concept_prompt = (
        tmp_path / "artifacts" / "docwrite" / "catalog-overview.prompt.md"
    )
    content = concept_prompt.read_text()
    assert "Adds catalog API" in content
    assert "New endpoint." in content


def test_jira_evidence_routing(tmp_path):
    target_repo = _setup_minimal(tmp_path)
    result = _run_prepare(tmp_path, target_repo)
    assert result.returncode == 0

    concept_prompt = (
        tmp_path / "artifacts" / "docwrite" / "catalog-overview.prompt.md"
    )
    content = concept_prompt.read_text()
    assert "PROJ-1" in content
    assert "Catalog requirements." in content


def test_evidence_confidence_strong(tmp_path):
    """Module with 3+ relevant PRs gets 'strong' confidence."""
    from doc_write_prepare import _compute_evidence_confidence

    pr_index = {
        "repo#1": {"verdict": "relevant"},
        "repo#2": {"verdict": "relevant"},
        "repo#3": {"verdict": "relevant"},
    }
    assert _compute_evidence_confidence(
        ["PROJ-1"], ["repo#1", "repo#2", "repo#3"], pr_index
    ) == "strong"


def test_evidence_confidence_moderate(tmp_path):
    from doc_write_prepare import _compute_evidence_confidence

    pr_index = {"repo#1": {"verdict": "relevant"}}
    assert _compute_evidence_confidence(
        ["PROJ-1"], ["repo#1"], pr_index
    ) == "moderate"


def test_evidence_confidence_weak():
    from doc_write_prepare import _compute_evidence_confidence

    assert _compute_evidence_confidence(["PROJ-1"], [], {}) == "weak"
    assert _compute_evidence_confidence([], [], {}) == "none"


def test_framework_detection_asciidoc(tmp_path):
    from doc_write_prepare import _detect_framework

    target = _make_target_repo(tmp_path, "asciidoc")
    assert _detect_framework(target) == "asciidoc"


def test_framework_detection_mkdocs(tmp_path):
    from doc_write_prepare import _detect_framework

    target = _make_target_repo(tmp_path, "mkdocs")
    assert _detect_framework(target) == "mkdocs"


def test_draft_mode(tmp_path):
    target_repo = _setup_minimal(tmp_path)
    result = _run_prepare(tmp_path, target_repo, extra_args=["--draft"])
    assert result.returncode == 0

    config = json.loads(
        (tmp_path / "artifacts" / "docwrite" / "writer-config.json").read_text()
    )
    assert config["mode"] == "draft"
    for mod in config["modules"]:
        assert "output" in mod["target_path"]


def test_cross_module_xref_map(tmp_path):
    target_repo = _setup_minimal(tmp_path)
    result = _run_prepare(tmp_path, target_repo)
    assert result.returncode == 0

    concept_prompt = (
        tmp_path / "artifacts" / "docwrite" / "catalog-overview.prompt.md"
    )
    content = concept_prompt.read_text()
    assert "Cross-Module References" in content
    assert "configure-the-catalog" in content


def test_missing_docplan_exits_2(tmp_path):
    target_repo = _make_target_repo(tmp_path)
    result = _run_prepare(tmp_path, target_repo)
    assert result.returncode == 2


def test_sample_file_detection(tmp_path):
    from doc_write_prepare import _sample_files

    target = _make_target_repo(tmp_path, "asciidoc")
    samples = _sample_files(target, "modules", "asciidoc", ["concept", "procedure", "reference"])
    assert "concept" in samples
    assert "procedure" in samples
    assert "reference" in samples


def test_existing_file_conflict_avoidance(tmp_path):
    target_repo = _setup_minimal(tmp_path)
    (tmp_path / "target-repo" / "modules" / "catalog-overview.adoc").write_text("existing")

    result = _run_prepare(tmp_path, target_repo)
    assert result.returncode == 0

    config = json.loads(
        (tmp_path / "artifacts" / "docwrite" / "writer-config.json").read_text()
    )
    slugs = [m["slug"] for m in config["modules"]]
    assert "catalog-overview-2" in slugs
