"""Tests for jira_context_bootstrap.py and the jiracontext-populate skill."""
import os
import re
import shutil
import subprocess
import sys
import textwrap

import pytest
import yaml


SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "jira_context_bootstrap.py")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILL_DIR = os.path.join(PROJECT_ROOT, ".claude", "skills",
                         "jiracontext-populate")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _run_bootstrap(args, cwd):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, text=True, cwd=cwd,
    )


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("---\n")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _parse_skill_md(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("---\n")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _create_exploration_fixtures(base_dir):
    """Create a minimal jiraexploration directory and manifest."""
    explore_dir = base_dir / "artifacts" / "jiraexploration"
    explore_dir.mkdir(parents=True)

    # Starting issue — has meaningful body
    (explore_dir / "RHOAIENG-100.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHOAIENG-100
        summary: "Needs doc for Kueue integration"
        issue_type: Task
        ---

        Needs Doc for how EvalHub integration with Kueue.
        The related code is present in:
        [https://github.com/org/repo/pull/42](https://github.com/org/repo/pull/42)
    """))

    # Rich RHAISTRAT — detailed requirements
    (explore_dir / "RHAISTRAT-200.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHAISTRAT-200
        summary: "EvalHub Integration with Kueue"
        issue_type: Feature
        ---

        ## Requirements

        - Users shall be able to designate a target Kueue queue.
        - When Kueue is available, EvalHub shall submit through that queue.
        - EvalHub job status shall reflect Kueue scheduling states.

        ## Scope

        - Kueue-aware evaluation job submission
        - Graceful degradation when Kueue is unavailable
    """))

    # Empty tracking Epic
    (explore_dir / "RHOAIENG-101.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHOAIENG-101
        summary: "[DOCS RHAISTRAT-200] EvalHub Kueue docs tracking"
        issue_type: Epic
        ---

    """))

    # Noise-only Epic
    (explore_dir / "RHOAIENG-102.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHOAIENG-102
        summary: "[QE RHAISTRAT-200] EvalHub Kueue QE validation"
        issue_type: Epic
        ---

    """))

    # Manifest
    manifest_path = base_dir / "artifacts" / "jiraexploration.md"
    fm = {
        "starting_issue": "RHOAIENG-100",
        "started_at": "2026-04-30T10:00:00Z",
        "link_filter": "UX",
        "output_directory": "artifacts/jiraexploration",
        "rhaistrat": "RHAISTRAT-200",
        "hierarchy": ["RHOAIENG-100", "RHAISTRAT-200"],
        "pull_requests": [
            "https://github.com/org/repo/pull/42 (RHOAIENG-100: Kueue docs)",
        ],
    }
    body = ("Needs Doc for how EvalHub integration with Kueue.\n"
            "The related code is present in:\n"
            "[https://github.com/org/repo/pull/42]"
            "(https://github.com/org/repo/pull/42)")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(fm, default_flow_style=False, sort_keys=False))
        f.write("---\n\n")
        f.write(body + "\n")

    return explore_dir, manifest_path


# ── Tier 1: Script bootstrap tests ──────────────────────────────────────────

class TestJiraContextBootstrap:

    def test_copies_starting_issue_only(self, art_dir):
        _create_exploration_fixtures(art_dir)
        result = _run_bootstrap([], cwd=str(art_dir))
        assert result.returncode == 0, f"stderr: {result.stderr}"

        ctx_dir = art_dir / "artifacts" / "jiracontext"
        assert (ctx_dir / "RHOAIENG-100.md").exists(), \
            "starting issue not copied"
        for name in ("RHAISTRAT-200.md", "RHOAIENG-101.md",
                     "RHOAIENG-102.md"):
            assert not (ctx_dir / name).exists(), \
                f"{name} should not be copied by bootstrap"

    def test_manifest_frontmatter(self, art_dir):
        _create_exploration_fixtures(art_dir)
        _run_bootstrap([], cwd=str(art_dir))

        fm, _ = _parse_manifest(
            str(art_dir / "artifacts" / "jiracontext.md"))
        assert fm["starting_issue"] == "RHOAIENG-100"
        assert fm["output_directory"] == "artifacts/jiracontext"
        assert fm["rhaistrat"] == "RHAISTRAT-200"
        assert fm["hierarchy"] == ["RHOAIENG-100", "RHAISTRAT-200"]
        assert "link_filter" not in fm
        assert "pull_requests" not in fm

    def test_manifest_body_preserved(self, art_dir):
        _create_exploration_fixtures(art_dir)
        _run_bootstrap([], cwd=str(art_dir))

        _, explore_body = _parse_manifest(
            str(art_dir / "artifacts" / "jiraexploration.md"))
        _, ctx_body = _parse_manifest(
            str(art_dir / "artifacts" / "jiracontext.md"))
        assert ctx_body == explore_body

    def test_manifest_started_at_is_fresh(self, art_dir):
        _create_exploration_fixtures(art_dir)
        _run_bootstrap([], cwd=str(art_dir))

        explore_fm, _ = _parse_manifest(
            str(art_dir / "artifacts" / "jiraexploration.md"))
        ctx_fm, _ = _parse_manifest(
            str(art_dir / "artifacts" / "jiracontext.md"))
        assert ctx_fm["started_at"] != explore_fm["started_at"]

    def test_custom_output_dir(self, art_dir):
        _create_exploration_fixtures(art_dir)
        custom = str(art_dir / "custom_out" / "ctx")
        (art_dir / "custom_out").mkdir()
        result = _run_bootstrap(["--output-dir", custom], cwd=str(art_dir))
        assert result.returncode == 0, f"stderr: {result.stderr}"

        manifest = art_dir / "custom_out" / "jiracontext.md"
        assert manifest.exists()
        fm, _ = _parse_manifest(str(manifest))
        assert fm["output_directory"] == custom

    def test_missing_input_dir_exits_nonzero(self, art_dir):
        result = _run_bootstrap(
            ["--input-dir", "nonexistent/dir"], cwd=str(art_dir))
        assert result.returncode != 0


# ── Tier 2: Skill YAML validation ───────────────────────────────────────────

class TestSkillDefinition:

    def test_skill_frontmatter_valid(self):
        fm, _ = _parse_skill_md(SKILL_MD)
        assert "name" in fm, "missing required 'name' field"
        assert "description" in fm, "missing required 'description' field"
        assert isinstance(fm["name"], str)
        assert isinstance(fm["description"], str)
        assert len(fm["description"]) > 0

    def test_skill_name_matches_directory(self):
        fm, _ = _parse_skill_md(SKILL_MD)
        dir_name = os.path.basename(SKILL_DIR)
        assert fm["name"] == dir_name

    def test_skill_name_format(self):
        fm, _ = _parse_skill_md(SKILL_MD)
        name = fm["name"]
        assert name == name.lower(), "name must be lowercase"
        assert re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', name), \
            "name must be lowercase alphanumeric with hyphens, " \
            "not starting/ending with hyphen"
        assert "--" not in name, "name must not contain consecutive hyphens"
        assert len(name) <= 64, "name must be at most 64 characters"

    def test_skill_under_500_lines(self):
        with open(SKILL_MD, encoding="utf-8") as f:
            line_count = sum(1 for _ in f)
        assert line_count < 500, \
            f"SKILL.md is {line_count} lines, should be under 500"

    def test_referenced_script_exists(self):
        _, body = _parse_skill_md(SKILL_MD)
        match = re.search(r'scripts/\S+\.py', body)
        assert match, "SKILL.md should reference a script"
        script_path = os.path.join(PROJECT_ROOT, match.group())
        assert os.path.isfile(script_path), \
            f"Referenced script {match.group()} does not exist"

    def test_prompt_template_exists(self):
        assert os.path.isfile(os.path.join(SKILL_DIR, "prompt-template.md")), \
            "SKILL.md references prompt-template.md but file is missing"


# ── Tier 3: Population decision test ────────────────────────────────────────

PROMPT_TEMPLATE = os.path.join(SKILL_DIR, "prompt-template.md")


def _read_prompt_template():
    """Read the subagent prompt template from prompt-template.md."""
    with open(PROMPT_TEMPLATE, encoding="utf-8") as f:
        return f.read().rstrip("\n")


def _build_populate_prompt(jiracontext_body, input_directory,
                           output_directory, starting_issue):
    """Fill the prompt template with concrete values."""
    template = _read_prompt_template()
    return (template
            .replace("{the full markdown body from jiracontext.md}",
                     jiracontext_body)
            .replace("{input_directory}", input_directory)
            .replace("{output_directory}", output_directory)
            .replace("{starting_issue}", starting_issue))


_LLM_TEST_DIR = os.path.join(PROJECT_ROOT, ".tmp_llm_test")


@pytest.fixture(scope="class")
def llm_workdir():
    """Project-local temp directory so Claude's sandbox allows file ops."""
    import pathlib
    base = pathlib.Path(_LLM_TEST_DIR)
    if base.exists():
        shutil.rmtree(base)
    base.mkdir()
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _create_populate_fixtures(base_dir):
    """Create jiraexploration source + bootstrapped jiracontext for tests."""
    explore_dir = base_dir / "artifacts" / "jiraexploration"
    explore_dir.mkdir(parents=True)
    ctx_dir = base_dir / "artifacts" / "jiracontext"
    ctx_dir.mkdir(parents=True)

    # Starting issue — already in jiracontext (copied by bootstrap)
    starting_md = textwrap.dedent("""\
        ---
        jira_key: RHOAIENG-100
        summary: "Needs doc for Kueue integration"
        issue_type: Task
        ---

        Needs Doc for how EvalHub integration with Kueue.
    """)
    (explore_dir / "RHOAIENG-100.md").write_text(starting_md)
    (ctx_dir / "RHOAIENG-100.md").write_text(starting_md)

    # Rich content — should be COPIED
    (explore_dir / "RHAISTRAT-200.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHAISTRAT-200
        summary: "EvalHub Integration with Kueue"
        issue_type: Feature
        ---

        ## Requirements

        - Users shall be able to designate a target Kueue queue as part of
          an evaluation job request through the EvalHub API.
        - When Kueue is available in the cluster and a queue is specified,
          EvalHub shall submit the evaluation job through that queue.
        - EvalHub job status shall accurately reflect Kueue scheduling states.
        - When Kueue is not available and a queue name is specified, EvalHub
          shall surface a non-blocking notification and proceed with direct
          scheduling.

        ## Scope

        In scope:
        - Kueue-aware evaluation job submission
        - Graceful degradation when Kueue is unavailable
        - Lifecycle state mapping between Kueue and EvalHub job states
        - API-level support for optionally specifying a target queue

        Out of scope:
        - Cluster-level queue configuration
        - Priority class or cohort configuration
        - Automatic queue selection
    """))

    # Empty body — should be SKIPPED
    (explore_dir / "RHOAIENG-101.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHOAIENG-101
        summary: "[DOCS RHAISTRAT-200] EvalHub Kueue docs tracking"
        issue_type: Epic
        ---

    """))

    # Noise only — should be SKIPPED
    (explore_dir / "RHOAIENG-102.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHOAIENG-102
        summary: "[QE RHAISTRAT-200] EvalHub Kueue QE validation"
        issue_type: Epic
        ---

    """))

    # Manifest
    body = "Needs Doc for how EvalHub integration with Kueue."
    manifest = base_dir / "artifacts" / "jiracontext.md"
    fm = {
        "starting_issue": "RHOAIENG-100",
        "started_at": "2026-05-01T12:00:00Z",
        "output_directory": str(ctx_dir),
        "rhaistrat": "RHAISTRAT-200",
        "hierarchy": ["RHOAIENG-100", "RHAISTRAT-200"],
    }
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(fm, default_flow_style=False, sort_keys=False))
        f.write("---\n\n")
        f.write(body + "\n")

    return explore_dir, ctx_dir, body


_has_claude = shutil.which("claude") is not None


@pytest.mark.llm
@pytest.mark.skipif(not _has_claude, reason="claude CLI not available")
class TestPopulateDecision:
    """Single claude -p invocation, then each test checks one semantic.

    The class-scoped fixture ``run_populate_once`` executes the populate
    prompt once against known test fixtures and streams output live.
    Every test method below asserts one aspect of that single run's result
    — no additional LLM calls are made.
    """

    @pytest.fixture(autouse=True, scope="class")
    def run_populate_once(self, llm_workdir, request):
        """Run claude -p with the populate prompt once for the whole class.

        Fixture files (in jiraexploration/):
          - RHOAIENG-100.md  starting issue, pre-copied to jiracontext/
          - RHAISTRAT-200.md rich requirements & scope — expect COPIED
          - RHOAIENG-101.md  empty body — expect SKIPPED
          - RHOAIENG-102.md  empty body — expect SKIPPED

        Results are stored as class attributes so every test method can
        inspect the same ctx_dir and return code.
        """
        explore_dir, ctx_dir, body = _create_populate_fixtures(llm_workdir)
        prompt = _build_populate_prompt(
            body, str(explore_dir), str(ctx_dir), "RHOAIENG-100")

        cmd = [
            "claude", "-p", prompt,
            "--permission-mode", "acceptEdits",
        ]
        print(f"\n>>> Running: claude -p <prompt> (workdir={llm_workdir})")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        output_lines = []
        for line in proc.stdout:
            print(line, end="", flush=True)
            output_lines.append(line)
        proc.wait()

        request.cls.explore_dir = explore_dir
        request.cls.ctx_dir = ctx_dir
        request.cls.claude_returncode = proc.returncode
        request.cls.claude_output = "".join(output_lines)

    # -- assertions on the single claude run --

    def test_claude_exits_cleanly(self):
        """The claude -p process completed without errors."""
        assert self.claude_returncode == 0, \
            f"claude exited {self.claude_returncode}\n{self.claude_output}"

    def test_starting_issue_untouched(self):
        """The starting issue (pre-copied by bootstrap) was not removed."""
        assert (self.ctx_dir / "RHOAIENG-100.md").exists(), \
            "starting issue must remain in jiracontext"

    def test_copies_rich_content(self):
        """A file with requirements and scope was copied in."""
        assert (self.ctx_dir / "RHAISTRAT-200.md").exists(), \
            "RHAISTRAT-200 has rich requirements/scope — should be copied"

    def test_skips_empty_body(self):
        """A file with an empty body was not copied."""
        assert not (self.ctx_dir / "RHOAIENG-101.md").exists(), \
            "RHOAIENG-101 has empty body — should not be copied"

    def test_skips_noise(self):
        """A file with no technical content was not copied."""
        assert not (self.ctx_dir / "RHOAIENG-102.md").exists(), \
            "RHOAIENG-102 has no technical content — should not be copied"


# ── Tier 1b: Link extraction tests ──────────────────────────────────────────

LINKS_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                            "jira_context_links.py")


def _run_links(args, cwd):
    return subprocess.run(
        [sys.executable, LINKS_SCRIPT] + args,
        capture_output=True, text=True, cwd=cwd,
    )


def _create_links_fixtures(base_dir):
    """Create jiracontext + jiraexploration manifests and context files
    with known URLs for link classification tests."""
    ctx_dir = base_dir / "artifacts" / "jiracontext"
    ctx_dir.mkdir(parents=True)

    # Context file with a mix of link types
    (ctx_dir / "RHOAIENG-100.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHOAIENG-100
        summary: "Feature with links"
        issue_type: Task
        ---

        See the PR: https://github.com/org/repo/pull/42
        Code is at [repo](https://github.com/org/repo)
        Also check https://github.com/org/other-repo/tree/main/docs
        Design doc: [Miro](https://miro.com/app/board/abc123)
        Spec: https://docs.google.com/document/d/xyz/edit
    """))

    # Context file with a GitLab MR
    (ctx_dir / "RHAISTRAT-200.md").write_text(textwrap.dedent("""\
        ---
        jira_key: RHAISTRAT-200
        summary: "Feature spec"
        issue_type: Feature
        ---

        MR: https://gitlab.cee.redhat.com/group/project/-/merge_requests/99
    """))

    # Exploration manifest with pre-collected PRs (includes annotation)
    explore_manifest = base_dir / "artifacts" / "jiraexploration.md"
    explore_fm = {
        "starting_issue": "RHOAIENG-100",
        "started_at": "2026-04-30T10:00:00Z",
        "output_directory": "artifacts/jiraexploration",
        "pull_requests": [
            "https://github.com/org/repo/pull/42 (RHOAIENG-100: Feature)",
            "https://github.com/org/repo/pull/99 (RHOAIENG-200: Other PR)",
        ],
    }
    with open(explore_manifest, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(explore_fm, default_flow_style=False,
                          sort_keys=False))
        f.write("---\n\n")

    # Context manifest (will be updated by the script)
    ctx_manifest = base_dir / "artifacts" / "jiracontext.md"
    ctx_fm = {
        "starting_issue": "RHOAIENG-100",
        "started_at": "2026-05-01T12:00:00Z",
        "output_directory": str(ctx_dir),
    }
    with open(ctx_manifest, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(yaml.dump(ctx_fm, default_flow_style=False, sort_keys=False))
        f.write("---\n\nSome body.\n")

    return ctx_dir


class TestJiraContextLinks:
    """Deterministic tests for jira_context_links.py link extraction."""

    @pytest.fixture(autouse=True)
    def setup_and_run_links(self, art_dir):
        _create_links_fixtures(art_dir)
        result = _run_links([], cwd=str(art_dir))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        self.fm, self.body = _parse_manifest(
            str(art_dir / "artifacts" / "jiracontext.md"))

    def test_extracts_prs_from_exploration_manifest(self):
        """PRs from jiraexploration.md pull_requests appear (bare URLs)."""
        assert "https://github.com/org/repo/pull/99" in self.fm["pull_requests"]

    def test_extracts_prs_from_context_bodies(self):
        """A PR URL found in a context file body is extracted."""
        assert "https://github.com/org/repo/pull/42" in self.fm["pull_requests"]

    def test_extracts_gitlab_mr(self):
        """A GitLab MR URL is classified as a pull request."""
        mrs = [u for u in self.fm["pull_requests"] if "merge_requests" in u]
        assert len(mrs) == 1

    def test_deduplicates_prs(self):
        """Same PR from exploration + body appears only once."""
        count = self.fm["pull_requests"].count(
            "https://github.com/org/repo/pull/42")
        assert count == 1

    def test_extracts_code_repositories(self):
        """A github.com/org/repo URL is classified as a code repository."""
        assert "code_repositories" in self.fm
        assert any("github.com/org/" in u
                    for u in self.fm["code_repositories"])

    def test_extracts_additional_links(self):
        """Non-PR, non-repo URLs go to additional_links."""
        assert "additional_links" in self.fm
        urls = self.fm["additional_links"]
        assert any("miro.com" in u for u in urls)
        assert any("docs.google.com" in u for u in urls)

    def test_pr_not_in_repos(self):
        """PR URLs do not also appear in code_repositories."""
        repos = set(self.fm.get("code_repositories", []))
        for pr in self.fm.get("pull_requests", []):
            assert pr not in repos

    def test_repo_not_in_additional(self):
        """Repo URLs do not also appear in additional_links."""
        additional = set(self.fm.get("additional_links", []))
        for repo in self.fm.get("code_repositories", []):
            assert repo not in additional

    def test_repos_derived_from_prs(self):
        """Repos are inferred from PR URLs (org/repo extracted from PR path)."""
        repos = self.fm.get("code_repositories", [])
        assert "https://github.com/org/repo" in repos

    def test_gitlab_repo_derived_from_mr(self):
        """GitLab repo is inferred from MR URL."""
        repos = self.fm.get("code_repositories", [])
        assert any("gitlab.cee.redhat.com" in u for u in repos)

    def test_body_preserved(self):
        """The manifest body is not lost during frontmatter update."""
        assert self.body == "Some body."
