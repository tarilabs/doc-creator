"""Tests for pipeline.py — DAG runner for the doc-creator pipeline."""

import asyncio
import graphlib
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pipeline as pipeline_mod
from pipeline import (
    STEPS,
    Pipeline,
    PipelineStep,
    build_dag,
    parse_args,
    static_order,
)


# ---------------------------------------------------------------------------
# DAG topology
# ---------------------------------------------------------------------------


class TestDAGTopology:
    def test_static_order_starts_with_jira_explore(self):
        order = static_order(STEPS)
        assert order[0] == "jira-explore"

    def test_static_order_ends_with_doc_review(self):
        order = static_order(STEPS)
        assert order[-1] == "doc-review"

    def test_jira_context_after_jira_explore(self):
        order = static_order(STEPS)
        assert order.index("jira-context") > order.index("jira-explore")

    def test_doc_context_after_both_parallel_steps(self):
        order = static_order(STEPS)
        assert order.index("doc-context") > order.index("pr-context")
        assert order.index("doc-context") > order.index("clone-repos")

    def test_all_steps_present(self):
        order = static_order(STEPS)
        assert set(order) == set(STEPS.keys())

    def test_no_cycles(self):
        build_dag(STEPS)


# ---------------------------------------------------------------------------
# Parallel batch detection
# ---------------------------------------------------------------------------


class TestParallelBatch:
    def test_pr_context_and_clone_repos_in_same_batch(self):
        dag = build_dag(STEPS)

        batch1 = dag.get_ready()
        assert set(batch1) == {"jira-explore"}
        dag.done("jira-explore")

        batch2 = dag.get_ready()
        assert set(batch2) == {"jira-context"}
        dag.done("jira-context")

        batch3 = dag.get_ready()
        assert set(batch3) == {"pr-context", "clone-repos"}


# ---------------------------------------------------------------------------
# Command placeholder filling
# ---------------------------------------------------------------------------


class TestCommandPlaceholders:
    PLACEHOLDERS = {
        "jira_key": "RHAISTRAT-1084",
        "claude_flags": "--dangerously-skip-permissions --no-session-persistence",
        "write_args": "--target-repo /abs/path/to/repo",
    }

    def test_no_unfilled_placeholders(self):
        for step in STEPS.values():
            cmd = step.command.format_map(self.PLACEHOLDERS)
            assert "{" not in cmd, f"Unfilled placeholder in {step.name}: {cmd}"

    def test_jira_key_in_explore_command(self):
        cmd = STEPS["jira-explore"].command.format_map(self.PLACEHOLDERS)
        assert "RHAISTRAT-1084" in cmd

    def test_target_repo_in_write_command(self):
        cmd = STEPS["doc-write"].command.format_map(self.PLACEHOLDERS)
        assert "--target-repo /abs/path/to/repo" in cmd

    def test_claude_flags_in_skill_commands(self):
        for name in ("jira-context", "pr-context", "doc-plan", "doc-write", "doc-review"):
            cmd = STEPS[name].command.format_map(self.PLACEHOLDERS)
            assert "--dangerously-skip-permissions" in cmd
            assert "--no-session-persistence" in cmd


# ---------------------------------------------------------------------------
# Artifact existence checks
# ---------------------------------------------------------------------------


class TestArtifactExists:
    def _make_pipeline(self, root):
        return Pipeline(
            jira_key="X",
            write_args="--draft",
            claude_flags="",
            root=root,
        )

    def test_file_missing(self, tmp_path):
        p = self._make_pipeline(tmp_path)
        step = PipelineStep("t", "", produces="artifacts/test.md")
        assert not p._artifact_exists(step)

    def test_file_empty(self, tmp_path):
        f = tmp_path / "artifacts" / "test.md"
        f.parent.mkdir(parents=True)
        f.write_text("")
        p = self._make_pipeline(tmp_path)
        step = PipelineStep("t", "", produces="artifacts/test.md")
        assert not p._artifact_exists(step)

    def test_file_nonempty(self, tmp_path):
        f = tmp_path / "artifacts" / "test.md"
        f.parent.mkdir(parents=True)
        f.write_text("data")
        p = self._make_pipeline(tmp_path)
        step = PipelineStep("t", "", produces="artifacts/test.md")
        assert p._artifact_exists(step)

    def test_dir_missing(self, tmp_path):
        p = self._make_pipeline(tmp_path)
        step = PipelineStep("t", "", produces="artifacts/ctx", is_dir=True)
        assert not p._artifact_exists(step)

    def test_dir_empty(self, tmp_path):
        d = tmp_path / "artifacts" / "ctx"
        d.mkdir(parents=True)
        p = self._make_pipeline(tmp_path)
        step = PipelineStep("t", "", produces="artifacts/ctx", is_dir=True)
        assert not p._artifact_exists(step)

    def test_dir_nonempty(self, tmp_path):
        d = tmp_path / "artifacts" / "ctx"
        d.mkdir(parents=True)
        (d / "repo").mkdir()
        p = self._make_pipeline(tmp_path)
        step = PipelineStep("t", "", produces="artifacts/ctx", is_dir=True)
        assert p._artifact_exists(step)


# ---------------------------------------------------------------------------
# Tool formatting
# ---------------------------------------------------------------------------


class TestFormatTool:
    def test_bash_shows_command(self):
        result = Pipeline._format_tool(
            "Bash", json.dumps({"command": "ls -la", "description": "list files"})
        )
        assert "$ ls -la" in result
        assert "list files" in result

    def test_bash_truncates_long_command(self):
        long_cmd = "x" * 200
        result = Pipeline._format_tool("Bash", json.dumps({"command": long_cmd}))
        assert len(result) < 200

    def test_read_shows_path(self):
        result = Pipeline._format_tool("Read", json.dumps({"file_path": "foo.py"}))
        assert result == "foo.py"

    def test_agent_shows_description(self):
        result = Pipeline._format_tool(
            "Agent", json.dumps({"description": "explore code"})
        )
        assert result == "explore code"

    def test_skill_shows_name(self):
        result = Pipeline._format_tool("Skill", json.dumps({"skill": "docwrite"}))
        assert result == "/docwrite"

    def test_invalid_json_returns_empty(self):
        assert Pipeline._format_tool("Bash", "not json") == ""


# ---------------------------------------------------------------------------
# --start-from skip set
# ---------------------------------------------------------------------------


class TestStartFrom:
    def test_skip_set_for_doc_context(self):
        p = Pipeline(
            jira_key="X",
            write_args="--draft",
            claude_flags="",
            start_from="doc-context",
        )
        skip = p._compute_skip_set()
        assert "jira-explore" in skip
        assert "jira-context" in skip
        assert "pr-context" in skip
        assert "clone-repos" in skip
        assert "doc-context" not in skip
        assert "doc-plan" not in skip

    def test_skip_set_for_first_step_is_empty(self):
        p = Pipeline(
            jira_key="X",
            write_args="--draft",
            claude_flags="",
            start_from="jira-explore",
        )
        assert p._compute_skip_set() == set()


# ---------------------------------------------------------------------------
# Pipeline execution — mocked subprocess
# ---------------------------------------------------------------------------


_STEP_SIGNATURES = {
    "jira-explore": "jira_exploration.py",
    "jira-context": "/jiracontext-populate",
    "pr-context": "/prcontext-populate",
    "clone-repos": "clone_code_repos.py",
    "doc-context": "doc_context_bootstrap.py",
    "doc-plan": "/docplan-create",
    "doc-write": "/docwrite",
    "doc-review": "/docreview",
}


def _make_mock_subprocess(exit_codes=None):
    """Return an async factory that mimics create_subprocess_shell."""
    codes = exit_codes or {}

    async def factory(cmd, **kwargs):
        name = None
        for step_name, sig in _STEP_SIGNATURES.items():
            if sig in cmd:
                name = step_name
                break
        code = codes.get(name, 0)
        proc = AsyncMock()
        proc.wait = AsyncMock(return_value=code)
        return proc

    return factory


class TestPipelineExecution:
    def _make_pipeline(self, tmp_path, **kwargs):
        (tmp_path / "artifacts" / "pipeline").mkdir(parents=True, exist_ok=True)
        return Pipeline(
            jira_key="TEST-1",
            write_args="--draft",
            claude_flags="--dangerously-skip-permissions",
            root=tmp_path,
            **kwargs,
        )

    def test_all_succeed(self, tmp_path):
        p = self._make_pipeline(tmp_path)
        with patch("asyncio.create_subprocess_shell", side_effect=_make_mock_subprocess()):
            rc = asyncio.run(p.run())
        assert rc == 0
        names = [r.name for r in p.results]
        assert len(names) == len(STEPS)
        assert all(r.status == "success" for r in p.results)

    def test_fatal_exit_stops_pipeline(self, tmp_path):
        p = self._make_pipeline(tmp_path)
        mock = _make_mock_subprocess({"jira-context": 2})
        with patch("asyncio.create_subprocess_shell", side_effect=mock):
            rc = asyncio.run(p.run())
        assert rc == 1
        executed = {r.name for r in p.results}
        assert "jira-explore" in executed
        assert "jira-context" in executed
        assert "pr-context" not in executed

    def test_warning_exit_continues(self, tmp_path):
        p = self._make_pipeline(tmp_path)
        mock = _make_mock_subprocess({"clone-repos": 1})
        with patch("asyncio.create_subprocess_shell", side_effect=mock):
            rc = asyncio.run(p.run())
        assert rc == 0
        clone_result = next(r for r in p.results if r.name == "clone-repos")
        assert clone_result.status == "warning"

    def test_nonzero_exit_fatal_for_claude_steps(self, tmp_path):
        p = self._make_pipeline(tmp_path)
        mock = _make_mock_subprocess({"jira-context": 1})
        with patch("asyncio.create_subprocess_shell", side_effect=mock):
            rc = asyncio.run(p.run())
        assert rc == 1
        jc = next(r for r in p.results if r.name == "jira-context")
        assert jc.status == "failed"

    def test_resume_skips_existing(self, tmp_path):
        art = tmp_path / "artifacts"
        (art / "jiraexploration.md").parent.mkdir(parents=True, exist_ok=True)
        (art / "jiraexploration.md").write_text("data")
        (art / "jiracontext.md").write_text("data")

        p = self._make_pipeline(tmp_path, resume=True)
        with patch("asyncio.create_subprocess_shell", side_effect=_make_mock_subprocess()):
            rc = asyncio.run(p.run())
        assert rc == 0
        skipped = {r.name for r in p.results if r.status == "skipped"}
        assert "jira-explore" in skipped
        assert "jira-context" in skipped
        assert "pr-context" not in skipped

    def test_start_from_skips_preceding(self, tmp_path):
        p = self._make_pipeline(tmp_path, start_from="doc-context")
        with patch("asyncio.create_subprocess_shell", side_effect=_make_mock_subprocess()):
            rc = asyncio.run(p.run())
        assert rc == 0
        skipped = {r.name for r in p.results if r.status == "skipped"}
        assert "jira-explore" in skipped
        assert "jira-context" in skipped
        assert "pr-context" in skipped
        assert "clone-repos" in skipped
        assert "doc-context" not in skipped


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


class TestCLI:
    def test_basic_target_repo(self):
        args = parse_args(["--jira-key", "TEST-1", "--target-repo", "/tmp/repo"])
        assert args.jira_key == "TEST-1"
        assert args.target_repo == "/tmp/repo"
        assert not args.draft

    def test_draft_mode(self):
        args = parse_args(["--jira-key", "TEST-1", "--draft"])
        assert args.draft is True
        assert args.target_repo is None

    def test_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            parse_args(["--jira-key", "X", "--target-repo", "/p", "--draft"])

    def test_resume_and_start_from(self):
        args = parse_args(
            ["--jira-key", "X", "--draft", "--resume", "--start-from", "doc-plan"]
        )
        assert args.resume is True
        assert args.start_from == "doc-plan"
