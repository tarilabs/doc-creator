"""Tests for pr_context_fetch.py, pr_context_filter.py, and prcontext-populate skill."""
import os
import re
import subprocess
import sys
import textwrap

import pytest
import yaml


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
FETCH_SCRIPT = os.path.join(SCRIPTS_DIR, "pr_context_fetch.py")
FILTER_SCRIPT = os.path.join(SCRIPTS_DIR, "pr_context_filter.py")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
SKILL_DIR = os.path.join(PROJECT_ROOT, ".claude", "skills",
                         "prcontext-populate")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")


# ── Helpers ──────────────────────────────────────────────────────────────────

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


# ── Tier 1a: Fetch script unit tests ─────────────────────────────────────────

class TestPrUrlParsing:
    """Test URL parsing functions without network calls."""

    def test_parses_github_pr_url(self):
        from pr_context_fetch import parse_pr_url
        result = parse_pr_url("https://github.com/kubeflow/model-registry/pull/2367")
        assert result == ("kubeflow", "model-registry", 2367)

    def test_parses_github_pr_url_trailing_whitespace(self):
        from pr_context_fetch import parse_pr_url
        result = parse_pr_url("https://github.com/org/repo/pull/42  ")
        assert result == ("org", "repo", 42)

    def test_returns_none_for_gitlab_mr(self):
        from pr_context_fetch import parse_pr_url
        result = parse_pr_url(
            "https://gitlab.cee.redhat.com/group/project/-/merge_requests/99")
        assert result is None

    def test_returns_none_for_non_pr_url(self):
        from pr_context_fetch import parse_pr_url
        result = parse_pr_url("https://github.com/org/repo")
        assert result is None

    def test_file_stem_format(self):
        from pr_context_fetch import pr_file_stem
        assert pr_file_stem("kubeflow", "model-registry", 2367) == \
            "kubeflow__model-registry__2367"


class TestFetchManifestWriting:
    """Test manifest writing without network calls."""

    def test_missing_manifest_exits_2(self, art_dir):
        result = subprocess.run(
            [sys.executable, FETCH_SCRIPT,
             "--manifest", "nonexistent.md",
             "--output-dir", str(art_dir / "prcontext")],
            capture_output=True, text=True, cwd=str(art_dir),
        )
        assert result.returncode == 2

    def test_empty_pr_list_exits_0(self, art_dir):
        manifest = art_dir / "artifacts" / "jiracontext.md"
        (art_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        with open(manifest, "w") as f:
            f.write("---\nstarting_issue: TEST-1\n---\n\nBody.\n")

        result = subprocess.run(
            [sys.executable, FETCH_SCRIPT,
             "--manifest", str(manifest),
             "--output-dir", str(art_dir / "prcontext")],
            capture_output=True, text=True, cwd=str(art_dir),
        )
        assert result.returncode == 0

    def test_gitlab_mr_marked_skipped(self, art_dir):
        manifest = art_dir / "artifacts" / "jiracontext.md"
        (art_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        fm = {
            "starting_issue": "TEST-1",
            "pull_requests": [
                "https://gitlab.cee.redhat.com/group/proj/-/merge_requests/99",
            ],
        }
        with open(manifest, "w") as f:
            f.write("---\n")
            f.write(yaml.dump(fm, default_flow_style=False, sort_keys=False))
            f.write("---\n\nBody.\n")

        out_dir = str(art_dir / "prcontext")
        result = subprocess.run(
            [sys.executable, FETCH_SCRIPT,
             "--manifest", str(manifest),
             "--output-dir", out_dir],
            capture_output=True, text=True, cwd=str(art_dir),
        )
        assert result.returncode == 0

        pr_manifest = os.path.join(out_dir, "prcontext.md")
        assert os.path.exists(pr_manifest)
        pr_fm, _ = _parse_manifest(pr_manifest)
        assert pr_fm["pull_requests"][0]["status"] == "skipped"


# ── Tier 1b: Filter script tests ─────────────────────────────────────────────

SAMPLE_PATCH_LOCKFILE = textwrap.dedent("""\
    diff --git a/package-lock.json b/package-lock.json
    index abc1234..def5678 100644
    --- a/package-lock.json
    +++ b/package-lock.json
    @@ -1,3 +1,4 @@
     {
       "name": "my-app",
    +  "version": "2.0.0",
       "lockfileVersion": 3
    diff --git a/src/app.ts b/src/app.ts
    index 1111111..2222222 100644
    --- a/src/app.ts
    +++ b/src/app.ts
    @@ -10,6 +10,8 @@
     import { Router } from 'express';

    +import { McpCatalog } from './catalog';
    +
     const app = express();
""")

SAMPLE_PATCH_WHITESPACE = textwrap.dedent("""\
    diff --git a/src/utils.py b/src/utils.py
    index aaa1111..bbb2222 100644
    --- a/src/utils.py
    +++ b/src/utils.py
    @@ -5,3 +5,3 @@
    -    return  value
    +    return value
""")

SAMPLE_PATCH_TEST_ONLY = textwrap.dedent("""\
    diff --git a/tests/test_catalog.py b/tests/test_catalog.py
    index ccc3333..ddd4444 100644
    --- a/tests/test_catalog.py
    +++ b/tests/test_catalog.py
    @@ -1,3 +1,5 @@
     import pytest

    +from catalog import McpCatalog
    +
     def test_list_servers():
""")

SAMPLE_PATCH_MIXED = textwrap.dedent("""\
    diff --git a/src/api.go b/src/api.go
    index eee5555..fff6666 100644
    --- a/src/api.go
    +++ b/src/api.go
    @@ -20,6 +20,9 @@
     func ListServers() []Server {
    +    // New endpoint for MCP catalog
    +    catalog := NewCatalog()
    +    return catalog.List()
     }
    diff --git a/src/api_test.go b/src/api_test.go
    index 7777777..8888888 100644
    --- a/src/api_test.go
    +++ b/src/api_test.go
    @@ -1,3 +1,5 @@
     package api

    +func TestListServers(t *testing.T) {
    +}
""")

SAMPLE_PATCH_IMAGE = textwrap.dedent("""\
    diff --git a/assets/logo.png b/assets/logo.png
    new file mode 100644
    index 0000000..abcdef1
    Binary files /dev/null and b/assets/logo.png differ
    diff --git a/src/main.py b/src/main.py
    index 1234567..7654321 100644
    --- a/src/main.py
    +++ b/src/main.py
    @@ -1,2 +1,3 @@
     import os
    +import catalog
""")


def _write_patch(directory, name, content):
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def _run_filter(input_dir, output_dir):
    return subprocess.run(
        [sys.executable, FILTER_SCRIPT,
         "--input-dir", input_dir,
         "--output-dir", output_dir],
        capture_output=True, text=True,
    )


class TestPrContextFilter:

    def test_drops_lockfile_hunks(self, tmp_path):
        raw = str(tmp_path / "raw")
        filtered = str(tmp_path / "filtered")
        os.makedirs(raw)
        _write_patch(raw, "org__repo__1.patch", SAMPLE_PATCH_LOCKFILE)

        result = _run_filter(raw, filtered)
        assert result.returncode == 0

        with open(os.path.join(filtered, "org__repo__1.patch")) as f:
            text = f.read()
        assert "package-lock.json" not in text
        assert "src/app.ts" in text

    def test_drops_whitespace_only(self, tmp_path):
        raw = str(tmp_path / "raw")
        filtered = str(tmp_path / "filtered")
        os.makedirs(raw)
        _write_patch(raw, "org__repo__2.patch", SAMPLE_PATCH_WHITESPACE)

        result = _run_filter(raw, filtered)
        assert result.returncode == 0

        with open(os.path.join(filtered, "org__repo__2.patch")) as f:
            text = f.read()
        assert text.strip() == ""

    def test_preserves_source_hunks(self, tmp_path):
        raw = str(tmp_path / "raw")
        filtered = str(tmp_path / "filtered")
        os.makedirs(raw)
        _write_patch(raw, "org__repo__3.patch", SAMPLE_PATCH_LOCKFILE)

        result = _run_filter(raw, filtered)
        assert result.returncode == 0

        with open(os.path.join(filtered, "org__repo__3.patch")) as f:
            text = f.read()
        assert "McpCatalog" in text

    def test_empty_after_filter(self, tmp_path):
        """When all hunks are noise, output is empty marker."""
        raw = str(tmp_path / "raw")
        filtered = str(tmp_path / "filtered")
        os.makedirs(raw)
        _write_patch(raw, "org__repo__4.patch", SAMPLE_PATCH_WHITESPACE)

        _run_filter(raw, filtered)

        output_path = os.path.join(filtered, "org__repo__4.patch")
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) == 0

    def test_test_only_pr_preserved(self, tmp_path):
        """PR with only test files keeps the test hunks."""
        raw = str(tmp_path / "raw")
        filtered = str(tmp_path / "filtered")
        os.makedirs(raw)
        _write_patch(raw, "org__repo__5.patch", SAMPLE_PATCH_TEST_ONLY)

        result = _run_filter(raw, filtered)
        assert result.returncode == 0

        with open(os.path.join(filtered, "org__repo__5.patch")) as f:
            text = f.read()
        assert "test_catalog.py" in text

    def test_mixed_drops_test_keeps_source(self, tmp_path):
        """PR with both source and test files drops tests, keeps source."""
        raw = str(tmp_path / "raw")
        filtered = str(tmp_path / "filtered")
        os.makedirs(raw)
        _write_patch(raw, "org__repo__6.patch", SAMPLE_PATCH_MIXED)

        result = _run_filter(raw, filtered)
        assert result.returncode == 0

        with open(os.path.join(filtered, "org__repo__6.patch")) as f:
            text = f.read()
        assert "src/api.go" in text
        assert "api_test.go" not in text

    def test_drops_image_hunks(self, tmp_path):
        raw = str(tmp_path / "raw")
        filtered = str(tmp_path / "filtered")
        os.makedirs(raw)
        _write_patch(raw, "org__repo__7.patch", SAMPLE_PATCH_IMAGE)

        result = _run_filter(raw, filtered)
        assert result.returncode == 0

        with open(os.path.join(filtered, "org__repo__7.patch")) as f:
            text = f.read()
        assert "logo.png" not in text
        assert "src/main.py" in text

    def test_diff_header_preserved(self, tmp_path):
        """Output patch has valid diff --git headers."""
        raw = str(tmp_path / "raw")
        filtered = str(tmp_path / "filtered")
        os.makedirs(raw)
        _write_patch(raw, "org__repo__8.patch", SAMPLE_PATCH_LOCKFILE)

        _run_filter(raw, filtered)

        with open(os.path.join(filtered, "org__repo__8.patch")) as f:
            text = f.read()
        assert text.startswith("diff --git")

    def test_input_dir_missing_exits_1(self, tmp_path):
        result = _run_filter(str(tmp_path / "nonexistent"), str(tmp_path / "out"))
        assert result.returncode == 1


# ── Tier 2: Skill YAML validation ────────────────────────────────────────────

class TestPrContextSkillDefinition:

    def test_skill_frontmatter_valid(self):
        fm, _ = _parse_skill_md(SKILL_MD)
        assert "name" in fm
        assert "description" in fm
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
        assert name == name.lower()
        assert re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', name)
        assert "--" not in name
        assert len(name) <= 64

    def test_skill_under_500_lines(self):
        with open(SKILL_MD, encoding="utf-8") as f:
            line_count = sum(1 for _ in f)
        assert line_count < 500, \
            f"SKILL.md is {line_count} lines, should be under 500"

    def test_referenced_scripts_exist(self):
        _, body = _parse_skill_md(SKILL_MD)
        scripts_found = re.findall(r'scripts/\S+\.py', body)
        assert len(scripts_found) >= 2, \
            "SKILL.md should reference both fetch and filter scripts"
        for script_ref in scripts_found:
            script_path = os.path.join(PROJECT_ROOT, script_ref)
            assert os.path.isfile(script_path), \
                f"Referenced script {script_ref} does not exist"

    def test_prompt_template_exists(self):
        assert os.path.isfile(os.path.join(SKILL_DIR, "prompt-template.md"))
