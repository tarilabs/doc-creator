"""Tests for clone_code_repos.py — shallow-clones repos listed in jiracontext.md.

Not tested beyond this placeholder. The script is almost entirely subprocess
(git clone --depth 1) and filesystem I/O. Testing it meaningfully requires
either mocking git or standing up temporary bare repos, both of which add
significant plumbing for little signal — the logic is a thin loop around
`git clone` with skip-if-exists and error logging.
"""


def test_placeholder():
    pass
