"""Integration tests for add_labels, remove_labels, swap_labels against jira-emulator."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from jira_utils import add_labels, remove_labels, swap_labels


def _labels(jira, key):
    return jira.get(key)["fields"]["labels"]


class TestAddLabels:
    def test_adds_to_unlabelled_issue(self, jira):
        jira.create("TEST-1", "Summary", "Desc")
        add_labels(jira.url, "admin", "admin", "TEST-1", ["alpha", "beta"])
        assert sorted(_labels(jira, "TEST-1")) == ["alpha", "beta"]

    def test_preserves_existing_labels(self, jira):
        jira.create("TEST-1", "Summary", "Desc", labels=["existing"])
        add_labels(jira.url, "admin", "admin", "TEST-1", ["new"])
        assert sorted(_labels(jira, "TEST-1")) == ["existing", "new"]

    def test_duplicate_add_is_idempotent(self, jira):
        jira.create("TEST-1", "Summary", "Desc", labels=["alpha"])
        add_labels(jira.url, "admin", "admin", "TEST-1", ["alpha"])
        assert _labels(jira, "TEST-1") == ["alpha"]


class TestRemoveLabels:
    def test_removes_existing_label(self, jira):
        jira.create("TEST-1", "Summary", "Desc", labels=["keep", "drop"])
        remove_labels(jira.url, "admin", "admin", "TEST-1", ["drop"])
        assert _labels(jira, "TEST-1") == ["keep"]

    def test_removing_absent_label_is_noop(self, jira):
        jira.create("TEST-1", "Summary", "Desc", labels=["keep"])
        remove_labels(jira.url, "admin", "admin", "TEST-1", ["nonexistent"])
        assert _labels(jira, "TEST-1") == ["keep"]

    def test_removes_all_labels(self, jira):
        jira.create("TEST-1", "Summary", "Desc", labels=["a", "b"])
        remove_labels(jira.url, "admin", "admin", "TEST-1", ["a", "b"])
        assert _labels(jira, "TEST-1") == []


class TestSwapLabels:
    def test_add_and_remove_in_single_call(self, jira):
        jira.create("TEST-1", "Summary", "Desc", labels=["old"])
        swap_labels(jira.url, "admin", "admin", "TEST-1",
                    add=["new"], remove=["old"])
        labels = _labels(jira, "TEST-1")
        assert "new" in labels
        assert "old" not in labels

    def test_preserves_unrelated_labels(self, jira):
        jira.create("TEST-1", "Summary", "Desc",
                    labels=["keep", "old"])
        swap_labels(jira.url, "admin", "admin", "TEST-1",
                    add=["new"], remove=["old"])
        assert sorted(_labels(jira, "TEST-1")) == ["keep", "new"]

    def test_add_only(self, jira):
        jira.create("TEST-1", "Summary", "Desc", labels=["existing"])
        swap_labels(jira.url, "admin", "admin", "TEST-1",
                    add=["added"], remove=[])
        assert sorted(_labels(jira, "TEST-1")) == ["added", "existing"]

    def test_remove_only(self, jira):
        jira.create("TEST-1", "Summary", "Desc", labels=["drop", "keep"])
        swap_labels(jira.url, "admin", "admin", "TEST-1",
                    add=[], remove=["drop"])
        assert _labels(jira, "TEST-1") == ["keep"]

    def test_multiple_adds_and_removes(self, jira):
        jira.create("TEST-1", "Summary", "Desc",
                    labels=["old-a", "old-b", "keep"])
        swap_labels(jira.url, "admin", "admin", "TEST-1",
                    add=["new-a", "new-b"], remove=["old-a", "old-b"])
        assert sorted(_labels(jira, "TEST-1")) == ["keep", "new-a", "new-b"]
