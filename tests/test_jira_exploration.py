"""Integration tests for jira_exploration.py against jira-emulator."""
import os
import subprocess
import sys

import pytest
import yaml


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


def _parse_manifest(path):
    """Read the manifest and return (frontmatter_dict, body_str)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("---\n")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


class TestJiraExploration:

    def test_strat_issue_no_parent_walk(self, jira, art_dir):
        """Starting from a RHAISTRAT: no parent walk, noted as STRAT."""
        jira.create("RHAISTRAT-500", "Top-level strategy",
                     "Strategy description.")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        manifest = str(art_dir / "artifacts" / "jiraexploration.md")
        result = _run_explore(jira, "RHAISTRAT-500", output_dir)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        fm, body = _parse_manifest(manifest)
        assert fm["starting_issue"] == "RHAISTRAT-500"
        assert fm["rhaistrat"] == "RHAISTRAT-500"
        assert "hierarchy" not in fm
        assert "Strategy description" in body

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

        fm, body = _parse_manifest(manifest)
        assert fm["starting_issue"] == "RHOAIENG-800"
        assert fm["rhaistrat"] == "RHAISTRAT-600"
        assert fm["hierarchy"] == ["RHOAIENG-800", "RHOAIENG-700",
                                   "RHAISTRAT-600"]
        assert "Leaf description" in body

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

        fm, body = _parse_manifest(manifest)
        assert fm["starting_issue"] == "RHOAIENG-901"
        assert fm["rhaistrat"] is None
        assert fm["hierarchy"] == ["RHOAIENG-901", "RHOAIENG-900"]
        assert "Child description" in body

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

        fm, _ = _parse_manifest(manifest)
        assert "pull_requests" in fm
        pr_urls = [entry.split(" ")[0] for entry in fm["pull_requests"]]
        assert "https://github.com/org/repo/pull/10" in pr_urls
        assert "https://github.com/org/other-repo/pull/20" in pr_urls
        pr_text = "\n".join(fm["pull_requests"])
        assert "RHOAIENG-411" in pr_text
        assert "RHOAIENG-412" in pr_text
        assert "RHOAIENG-413" not in pr_text
