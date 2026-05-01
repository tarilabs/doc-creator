"""Integration tests for fetch_issue.py against jira-emulator."""
import json
import os
import subprocess
import sys

import pytest
import yaml


SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_issue.py")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _env(jira):
    return {
        **os.environ,
        "JIRA_SERVER": jira.url,
        "JIRA_USER": "admin",
        "JIRA_TOKEN": "admin",
    }


def _run(jira, args, env_override=None, cwd=None):
    env = env_override if env_override is not None else _env(jira)
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, text=True, env=env, cwd=cwd,
    )


class TestFetchIssue:

    def test_fetches_issue_as_json(self, jira):
        jira.create("RHAIRFE-2000", "Pipeline autoscaling",
                     "Scale data pipelines based on queue depth.")

        result = _run(jira, ["RHAIRFE-2000"])
        assert result.returncode == 0, f"stderr: {result.stderr}"

        output = json.loads(result.stdout)
        assert output["key"] == "RHAIRFE-2000"
        assert output["fields"]["summary"] == "Pipeline autoscaling"

    def test_markdown_mode_converts_description(self, jira):
        jira.create("RHAIRFE-2001", "Feature with description",
                     "This is the **description** content.")

        result = _run(jira, ["RHAIRFE-2001", "--markdown"])
        assert result.returncode == 0, f"stderr: {result.stderr}"

        output = json.loads(result.stdout)
        desc = output["fields"].get("description", "")
        assert isinstance(desc, str)
        assert "description" in desc.lower()

    def test_fields_filter_limits_output(self, jira):
        jira.create("RHAIRFE-2002", "Filtered fields test",
                     "Full description here.",
                     labels=["alpha", "beta"])

        result = _run(jira, ["RHAIRFE-2002", "--fields", "summary,labels"])
        assert result.returncode == 0, f"stderr: {result.stderr}"

        output = json.loads(result.stdout)
        assert "summary" in output["fields"]
        assert "labels" in output["fields"]
        assert "description" not in output["fields"]
        assert "priority" not in output["fields"]
        assert "status" not in output["fields"]

    def test_missing_credentials_exits_with_error(self, jira, tmp_path):
        env = {k: v for k, v in os.environ.items()
               if k not in ("JIRA_SERVER", "JIRA_USER", "JIRA_TOKEN")}

        result = _run(jira, ["RHAIRFE-2000"], env_override=env, cwd=str(tmp_path))
        assert result.returncode != 0

    def test_fetch_all_writes_artifact_files(self, jira, art_dir):
        jira.create("RHAIRFE-2003", "Fetch-all target",
                     "Description for fetch-all test.")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        result = _run(jira, ["RHAIRFE-2003", "--fetch-all", output_dir],
                       cwd=PROJECT_ROOT)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        issue_file = os.path.join(output_dir, "RHAIRFE-2003.md")
        assert os.path.isfile(issue_file)

    def test_fetch_all_missing_credentials_exits_2(self, jira, art_dir):
        env = {k: v for k, v in os.environ.items()
               if k not in ("JIRA_SERVER", "JIRA_USER", "JIRA_TOKEN")}

        artifacts = str(art_dir / "artifacts")
        result = _run(jira, ["RHAIRFE-9999", "--fetch-all", artifacts],
                       env_override=env)
        assert result.returncode == 2


def _parse_md(path):
    """Read a markdown file and return (frontmatter_dict, body_str)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("---\n"), "file should start with YAML frontmatter"
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


class TestFetchAllFrontmatter:

    def test_frontmatter_contains_key_and_summary(self, jira, art_dir):
        jira.create("RHOAIENG-3000", "Kueue scheduling support",
                     "Implement Kueue workload scheduling.")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        result = _run(jira, ["RHOAIENG-3000", "--fetch-all", output_dir],
                       cwd=PROJECT_ROOT)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        fm, body = _parse_md(os.path.join(output_dir, "RHOAIENG-3000.md"))
        assert fm["jira_key"] == "RHOAIENG-3000"
        assert fm["summary"] == "Kueue scheduling support"
        assert "Kueue workload scheduling" in body

    def test_frontmatter_includes_git_pull_requests(self, jira, art_dir):
        pr_adf = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "inlineCard", "attrs": {
                    "url": "https://github.com/org/repo/pull/42"}},
                {"type": "text", "text": " "},
                {"type": "inlineCard", "attrs": {
                    "url": "https://github.com/org/repo/pull/99"}},
            ]}],
        }
        jira.create("RHOAIENG-3001", "feat(sdk): add queue support",
                     "Add queue config model.",
                     git_pull_request=pr_adf)

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        result = _run(jira, ["RHOAIENG-3001", "--fetch-all", output_dir],
                       cwd=PROJECT_ROOT)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        fm, _ = _parse_md(os.path.join(output_dir, "RHOAIENG-3001.md"))
        assert "git_pull_requests" in fm
        assert "https://github.com/org/repo/pull/42" in fm["git_pull_requests"]
        assert "https://github.com/org/repo/pull/99" in fm["git_pull_requests"]

    def test_frontmatter_includes_remote_links(self, jira, art_dir):
        jira.create("RHOAIENG-3002", "Remote links test",
                     "Issue with hyperlinks.")
        jira.add_remote_link(
            "RHOAIENG-3002",
            url="https://github.com/org/repo/pull/7",
            title="org/repo#7: Fix retry logic")
        jira.add_remote_link(
            "RHOAIENG-3002",
            url="https://docs.example.com/guide",
            title="Setup guide")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        result = _run(jira, ["RHOAIENG-3002", "--fetch-all", output_dir],
                       cwd=PROJECT_ROOT)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        fm, _ = _parse_md(os.path.join(output_dir, "RHOAIENG-3002.md"))
        assert "links" in fm
        urls = [link["url"] for link in fm["links"]]
        titles = [link["title"] for link in fm["links"]]
        assert "https://github.com/org/repo/pull/7" in urls
        assert "https://docs.example.com/guide" in urls
        assert "org/repo#7: Fix retry logic" in titles

    def test_frontmatter_with_pr_and_links_together(self, jira, art_dir):
        pr_adf = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "inlineCard", "attrs": {
                    "url": "https://github.com/org/repo/pull/55"}},
            ]}],
        }
        jira.create("RHOAIENG-3003", "Full metadata issue",
                     "Description with all metadata.",
                     git_pull_request=pr_adf)
        jira.add_remote_link(
            "RHOAIENG-3003",
            url="https://github.com/org/repo/pull/55",
            title="org/repo#55: Add feature")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        result = _run(jira, ["RHOAIENG-3003", "--fetch-all", output_dir],
                       cwd=PROJECT_ROOT)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        fm, body = _parse_md(os.path.join(output_dir, "RHOAIENG-3003.md"))
        assert fm["jira_key"] == "RHOAIENG-3003"
        assert fm["summary"] == "Full metadata issue"
        assert fm["git_pull_requests"] == [
            "https://github.com/org/repo/pull/55"]
        assert len(fm["links"]) == 1
        assert fm["links"][0]["url"] == "https://github.com/org/repo/pull/55"
        assert "all metadata" in body

    def test_no_pr_or_links_omits_sections(self, jira, art_dir):
        jira.create("RHOAIENG-3004", "Plain issue",
                     "No PRs or links here.")

        output_dir = str(art_dir / "artifacts" / "jiraexploration")
        result = _run(jira, ["RHOAIENG-3004", "--fetch-all", output_dir],
                       cwd=PROJECT_ROOT)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        fm, _ = _parse_md(os.path.join(output_dir, "RHOAIENG-3004.md"))
        assert fm["jira_key"] == "RHOAIENG-3004"
        assert "git_pull_requests" not in fm
        assert "links" not in fm
