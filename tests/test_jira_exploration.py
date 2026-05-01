"""Integration tests for jira_exploration.py against jira-emulator."""
import os
import subprocess
import sys

import pytest


SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "jira_exploration.py")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _env(jira):
    return {
        **os.environ,
        "JIRA_SERVER": jira.url,
        "JIRA_USER": "admin",
        "JIRA_TOKEN": "admin",
    }


def _run_explore(jira, issue_key, output_dir, cwd=PROJECT_ROOT):
    return subprocess.run(
        [sys.executable, SCRIPT, issue_key, "--output-dir", output_dir],
        capture_output=True, text=True, env=_env(jira), cwd=cwd,
    )


class TestJiraExploration:

    def test_strat_issue_no_parent_walk(self, jira, art_dir):
        """Starting from a RHAISTRAT: no parent walk, noted as STRAT."""
        jira.create("RHAISTRAT-500", "Top-level strategy",
                     "Strategy description.")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        manifest = str(art_dir / "artifacts" / "jiraexploration.md")
        result = _run_explore(jira, "RHAISTRAT-500", output_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        assert os.path.isfile(manifest)
        with open(manifest, encoding="utf-8") as f:
            text = f.read()

        assert "**Starting issue**: RHAISTRAT-500" in text
        assert "starting issue is a STRAT" in text
        assert "Hierarchy" not in text
        assert "Strategy description" in text

        assert os.path.isfile(os.path.join(output_dir, "RHAISTRAT-500.md"))

    def test_non_strat_walks_up_to_rhaistrat(self, jira, art_dir):
        """Starting from a leaf: walks up the parent chain to RHAISTRAT."""
        jira.create("RHAISTRAT-600", "Parent STRAT",
                     "STRAT-level description.")
        jira.create("RHOAIENG-700", "Mid-level epic",
                     "Engineering epic.", parent_key="RHAISTRAT-600")
        jira.create("RHOAIENG-800", "Leaf story",
                     "Leaf description.", parent_key="RHOAIENG-700")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        manifest = str(art_dir / "artifacts" / "jiraexploration.md")
        result = _run_explore(jira, "RHOAIENG-800", output_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        with open(manifest, encoding="utf-8") as f:
            text = f.read()

        assert "**Starting issue**: RHOAIENG-800" in text
        assert "**RHAISTRAT**: RHAISTRAT-600" in text
        assert "RHOAIENG-800 → RHOAIENG-700 → RHAISTRAT-600" in text
        assert "Leaf description" in text

        assert os.path.isfile(os.path.join(output_dir, "RHAISTRAT-600.md"))
        assert os.path.isfile(os.path.join(output_dir, "RHOAIENG-800.md"))

    def test_no_strat_ancestor_noted(self, jira, art_dir):
        """When no RHAISTRAT ancestor exists, the manifest says so."""
        jira.create("RHOAIENG-900", "Top eng issue",
                     "No STRAT parent.")
        jira.create("RHOAIENG-901", "Child eng issue",
                     "Child description.", parent_key="RHOAIENG-900")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        manifest = str(art_dir / "artifacts" / "jiraexploration.md")
        result = _run_explore(jira, "RHOAIENG-901", output_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        with open(manifest, encoding="utf-8") as f:
            text = f.read()

        assert "**Starting issue**: RHOAIENG-901" in text
        assert "**RHAISTRAT**: not found" in text
        assert "no RHAISTRAT ancestor" in text
        assert "RHOAIENG-901 → RHOAIENG-900" in text
        assert "Child description" in text

    def test_collects_pr_urls_from_epic_grandchildren(self, jira, art_dir):
        """PR URLs from Epic grandchildren are collected in the manifest."""
        pr_adf = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "inlineCard", "attrs": {
                    "url": "https://github.com/org/repo/pull/10"}},
            ]}],
        }
        desc_with_pr = (
            "See https://github.com/org/other-repo/pull/20 for details.")

        jira.create("RHAISTRAT-400", "Strategy with Epics",
                     "Top-level strategy.")
        jira.create("RHOAIENG-410", "Eng Epic",
                     "Engineering work.", issue_type="Epic",
                     parent_key="RHAISTRAT-400")
        jira.create("RHOAIENG-411", "Task with PR field",
                     "Task description.",
                     parent_key="RHOAIENG-410",
                     git_pull_request=pr_adf)
        jira.create("RHOAIENG-412", "Task with PR in description",
                     desc_with_pr,
                     parent_key="RHOAIENG-410")
        jira.create("RHOAIENG-413", "Task with no PRs",
                     "Nothing here.",
                     parent_key="RHOAIENG-410")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        manifest = str(art_dir / "artifacts" / "jiraexploration.md")
        result = _run_explore(jira, "RHAISTRAT-400", output_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        with open(manifest, encoding="utf-8") as f:
            text = f.read()

        assert "## Pull Requests" in text
        assert "https://github.com/org/repo/pull/10" in text
        assert "https://github.com/org/other-repo/pull/20" in text
        assert "RHOAIENG-411" in text
        assert "RHOAIENG-412" in text
        # Task with no PRs should not appear in the PR section
        assert "RHOAIENG-413" not in text.split("## Pull Requests")[1]
