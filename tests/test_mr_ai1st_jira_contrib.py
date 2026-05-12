"""Tests for mr_ai1st_jira_contrib.py against jira-emulator."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from mr_ai1st_jira_contrib import extract_jira_key, process_mr
from jira_utils import get_comments, adf_to_markdown

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
