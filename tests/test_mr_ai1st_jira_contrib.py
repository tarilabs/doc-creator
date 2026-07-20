"""Tests for mr_ai1st_jira_contrib.py against jira-emulator."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import mr_ai1st_jira_contrib as mr_mod
from mr_ai1st_jira_contrib import extract_jira_key, process_mr
from jira_utils import get_comments, get_issue, adf_to_markdown
from fetch_issue import _extract_urls_from_adf, FIELD_GIT_PULL_REQUEST

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _patch_glab(monkeypatch, mr_data):
    """Stub out all glab_* functions so no real GitLab calls are made."""
    import mr_ai1st_jira_contrib as mod
    monkeypatch.setattr(mod, "is_already_processed", lambda *a, **kw: False)
    monkeypatch.setattr(mod, "glab_mr_update_label", lambda *a, **kw: True)
    monkeypatch.setattr(mod, "glab_mr_note_create", lambda *a, **kw: True)


class TestExtractJiraKey:

    def test_2_of_3_match(self):
        mr = {"title": "docs(RHOAIENG-123): foo",
              "source_branch": "rhoaieng-123-abc",
              "description": "unrelated text"}
        assert extract_jira_key(mr) == "RHOAIENG-123"

    def test_rejects_single_source(self):
        mr = {"title": "some title",
              "source_branch": "feature-branch",
              "description": "mentions RHOAIENG-999 once"}
        assert extract_jira_key(mr) is None

    def test_case_insensitive_branch(self):
        mr = {"title": "RHAISTRAT-50: strategy",
              "source_branch": "rhaistrat-50-impl",
              "description": ""}
        assert extract_jira_key(mr) == "RHAISTRAT-50"


class TestProcessMR:

    def test_full_flow_with_hierarchy(self, jira, monkeypatch):
        """End-to-end: updates JIRA, walks to RHAISTRAT, finds/creates
        Epic and Task."""
        jira.create("RHAISTRAT-100", "Top strategy", "Strategy desc.")
        jira.create("RHOAIENG-200", "Eng Epic with CCS",
                     "Epic desc.", issue_type="Epic",
                     parent_key="RHAISTRAT-100")
        jira.create("RHOAIENG-300", "Leaf task",
                     "Task desc.", parent_key="RHOAIENG-200")

        mr_data = {
            "iid": 99,
            "title": "[DO NOT MERGE] docs(RHOAIENG-300): new docs",
            "source_branch": "rhoaieng-300-docs",
            "description": "JIRA: RHOAIENG-300",
            "web_url": "https://gitlab.example.com/proj/-/merge_requests/99",
            "author": {"name": "AI_FIRST_TOKEN", "username": "bot"},
        }
        _patch_glab(monkeypatch, mr_data)

        os.environ["JIRA_SERVER"] = jira.url
        os.environ["JIRA_USER"] = "admin"
        os.environ["JIRA_TOKEN"] = "admin"

        result = process_mr(
            "gitlab.example.com", "proj", 99, mr_data,
            jira.url, "admin", "admin")
        assert result is True

        leaf = jira.get("RHOAIENG-300")
        assert "ai1st-doc-contributed" in leaf["fields"]["labels"]

        strat = jira.get("RHAISTRAT-100")
        assert "ai1st-doc-contributed" in strat["fields"]["labels"]

        comments = get_comments(jira.url, "admin", "admin", "RHOAIENG-300")
        comment_md = adf_to_markdown(comments[-1]["body"])
        assert "merge_requests/99" in comment_md
        assert "(DO NOT MERGE)" in comment_md
        assert "[DO NOT MERGE]" not in comment_md

    def test_linked_documented_by_issue(self, jira, monkeypatch):
        """When the STRAT has no child CCS Epic but has a 'documented by'
        link, the MR is appended to the linked issue directly."""
        jira.create("RHAISTRAT-200", "Strategy with linked docs",
                     "Strategy desc.")
        jira.create("RHOAIENG-400", "[CCS] Docs task for strategy",
                     "Tracking docs.", issue_type="Task")
        jira.create("RHOAIENG-500", "Leaf task under eng",
                     "Task desc.", parent_key="RHAISTRAT-200")

        jira.link("Document", "RHAISTRAT-200", "RHOAIENG-400")

        mr_data = {
            "iid": 101,
            "title": "[DO NOT MERGE] docs(RHOAIENG-500): linked docs",
            "source_branch": "rhoaieng-500-docs",
            "description": "JIRA: RHOAIENG-500",
            "web_url": "https://gitlab.example.com/proj/-/merge_requests/101",
            "author": {"name": "AI_FIRST_TOKEN", "username": "bot"},
        }
        _patch_glab(monkeypatch, mr_data)

        result = process_mr(
            "gitlab.example.com", "proj", 101, mr_data,
            jira.url, "admin", "admin")
        assert result is True

        linked = jira.get("RHOAIENG-400")
        assert "ai1st-doc-contributed" in linked["fields"]["labels"]

        pr_adf = linked["fields"].get(FIELD_GIT_PULL_REQUEST)
        pr_urls = _extract_urls_from_adf(pr_adf)
        assert "https://gitlab.example.com/proj/-/merge_requests/101" in pr_urls

        comments = get_comments(jira.url, "admin", "admin", "RHOAIENG-400")
        comment_md = adf_to_markdown(comments[-1]["body"])
        assert "merge_requests/101" in comment_md

        all_children = jira.search(
            f"parent = RHAISTRAT-200", fields="key,issuetype")
        epic_children = [c for c in all_children
                         if c["fields"]["issuetype"]["name"] == "Epic"]
        assert len(epic_children) == 0, "Should NOT create a child CCS Epic"

    def test_no_jira_key_returns_false(self, jira, monkeypatch):
        mr_data = {
            "iid": 50,
            "title": "random title",
            "source_branch": "feature-branch",
            "description": "no jira reference here",
            "web_url": "https://gitlab.example.com/proj/-/merge_requests/50",
            "author": {"name": "AI_FIRST_TOKEN", "username": "bot"},
        }
        _patch_glab(monkeypatch, mr_data)

        result = process_mr(
            "gitlab.example.com", "proj", 50, mr_data,
            jira.url, "admin", "admin")
        assert result is False


class TestGhostUser:

    def _mr_data(self, iid, author):
        base = {
            "iid": iid,
            "title": f"docs(RHOAIENG-{iid}): ghost test",
            "source_branch": f"rhoaieng-{iid}-ghost",
            "description": f"JIRA: RHOAIENG-{iid}",
            "web_url": f"https://gitlab.example.com/proj/-/merge_requests/{iid}",
        }
        if author is not None:
            base["author"] = author
        return base

    def test_process_mr_null_author(self, jira, monkeypatch):
        """process_mr handles ghost user (author=None) without crashing."""
        jira.create("RHOAIENG-600", "Leaf task", "Task desc.")
        mr_data = self._mr_data(600, author=None)
        mr_data["author"] = None
        _patch_glab(monkeypatch, mr_data)

        result = process_mr(
            "gitlab.example.com", "proj", 600, mr_data,
            jira.url, "admin", "admin")
        assert result is True

        comments = get_comments(jira.url, "admin", "admin", "RHOAIENG-600")
        comment_md = adf_to_markdown(comments[-1]["body"])
        assert "(ghost user)" in comment_md

    def test_process_mr_missing_author_key(self, jira, monkeypatch):
        """process_mr handles missing author key without crashing."""
        jira.create("RHOAIENG-700", "Leaf task", "Task desc.")
        mr_data = self._mr_data(700, author=None)
        _patch_glab(monkeypatch, mr_data)

        result = process_mr(
            "gitlab.example.com", "proj", 700, mr_data,
            jira.url, "admin", "admin")
        assert result is True

    def test_process_mr_author_with_null_fields(self, jira, monkeypatch):
        """process_mr handles author object with null name/username."""
        jira.create("RHOAIENG-800", "Leaf task", "Task desc.")
        mr_data = self._mr_data(800, author={"name": None, "username": None})
        _patch_glab(monkeypatch, mr_data)

        result = process_mr(
            "gitlab.example.com", "proj", 800, mr_data,
            jira.url, "admin", "admin")
        assert result is True

        comments = get_comments(jira.url, "admin", "admin", "RHOAIENG-800")
        comment_md = adf_to_markdown(comments[-1]["body"])
        assert "(ghost user)" in comment_md

    def test_glab_mr_list_fallback_on_deleted_user(self, monkeypatch):
        """glab_mr_list falls back to unfiltered query when author is deleted."""
        call_log = []

        def fake_run(cmd, **kwargs):
            call_log.append(cmd[:])
            if "--author" in cmd:
                return subprocess.CompletedProcess(
                    cmd, returncode=1,
                    stdout="",
                    stderr="Failed to find user by name: old_bot.\n")
            return subprocess.CompletedProcess(
                cmd, returncode=0,
                stdout=json.dumps([{"iid": 1, "title": "test MR"}]),
                stderr="")

        monkeypatch.setattr(mr_mod.subprocess, "run", fake_run)

        mrs = mr_mod.glab_mr_list("https://example.com/repo",
                                  author="old_bot")
        assert len(mrs) == 1
        assert mrs[0]["iid"] == 1
        assert len(call_log) == 2
        assert "--author" in call_log[0]
        assert "--author" not in call_log[1]
