"""Tests for pr_context_fetch.py, pr_context_filter.py, and prcontext-populate skill."""
import json
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
PRECLASSIFY_SCRIPT = os.path.join(SCRIPTS_DIR, "pr_context_preclassify.py")
VERDICT_CHECK_SCRIPT = os.path.join(SCRIPTS_DIR, "pr_context_verdict_check.py")
REPORT_SCRIPT = os.path.join(SCRIPTS_DIR, "pr_context_report.py")
PREPARE_SCRIPT = os.path.join(SCRIPTS_DIR, "pr_context_prepare.py")
SANITIZE_SCRIPT = os.path.join(SCRIPTS_DIR, "pr_context_sanitize_yaml.py")

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

        pr_manifest = out_dir + ".md"
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

    def test_skill_has_seven_steps(self):
        _, body = _parse_skill_md(SKILL_MD)
        steps = re.findall(r'^## Step \d+', body, re.MULTILINE)
        assert len(steps) == 7, \
            f"SKILL.md should have 7 steps, found {len(steps)}: {steps}"


# ── Helpers for preclassify / verdict check tests ──────────────────────────

def _write_manifest(directory, entries, source_manifest="artifacts/jiracontext.md"):
    """Write a prcontext.md manifest with YAML frontmatter."""
    prcontext_dir = os.path.join(directory, "artifacts", "prcontext")
    os.makedirs(prcontext_dir, exist_ok=True)
    manifest_path = os.path.join(directory, "artifacts", "prcontext.md")
    fm = {
        "started_at": "2026-01-01T00:00:00Z",
        "source_manifest": source_manifest,
        "output_directory": prcontext_dir,
        "pull_requests": entries,
    }
    with open(manifest_path, "w") as f:
        f.write("---\n")
        f.write(yaml.dump(fm, default_flow_style=False, sort_keys=False))
        f.write("---\n")
    return manifest_path


def _write_meta_yaml(raw_dir, stem, files=None, title="", body=""):
    """Write a {stem}.meta.yaml in raw_dir with the given files list."""
    os.makedirs(raw_dir, exist_ok=True)
    meta = {"title": title, "body": body, "files": files or []}
    path = os.path.join(raw_dir, f"{stem}.meta.yaml")
    with open(path, "w") as f:
        yaml.dump(meta, f, default_flow_style=False)
    return path


def _write_summary(output_dir, stem, verdict, title="", gist=""):
    """Write a {stem}.md summary file with YAML frontmatter."""
    path = os.path.join(output_dir, f"{stem}.md")
    fm = {"pr_url": f"https://github.com/org/repo/pull/1",
          "repo": "org/repo", "pr_number": 1,
          "title": title, "verdict": verdict}
    if gist:
        fm["gist"] = gist
    with open(path, "w") as f:
        f.write("---\n")
        f.write(yaml.dump(fm, default_flow_style=False, sort_keys=False))
        f.write("---\n\n## What changed\n\nTest.\n")
    return path


def _write_filtered_patch(filtered_dir, stem, content="some patch content"):
    """Write a filtered patch file."""
    os.makedirs(filtered_dir, exist_ok=True)
    path = os.path.join(filtered_dir, f"{stem}.patch")
    with open(path, "w") as f:
        f.write(content)
    return path


def _run_preclassify(manifest):
    return subprocess.run(
        [sys.executable, PRECLASSIFY_SCRIPT, "--manifest", manifest],
        capture_output=True, text=True,
    )


def _run_verdict_check(manifest, output_dir):
    return subprocess.run(
        [sys.executable, VERDICT_CHECK_SCRIPT,
         "--manifest", manifest, "--output-dir", output_dir],
        capture_output=True, text=True,
    )


def _run_report(manifest, output_dir=None):
    cmd = [sys.executable, REPORT_SCRIPT, "--manifest", manifest]
    if output_dir:
        cmd.extend(["--output-dir", output_dir])
    return subprocess.run(cmd, capture_output=True, text=True)


# ── Tier 1c: Pre-classify script tests ────────────────────────────────────

from pr_context_preclassify import expand_hint_text


class TestExpandHintText:

    def test_no_hint_returns_none(self):
        assert expand_hint_text("no-hint", None) is None

    def test_absent_hint_returns_none(self):
        assert expand_hint_text(None, None) is None

    def test_candidate_peripheral(self):
        result = expand_hint_text("candidate-peripheral", "title prefix fix:")
        assert result.startswith("DETERMINISTIC HINT")
        assert "peripheral" in result
        assert "title prefix fix:" in result

    def test_candidate_noise(self):
        result = expand_hint_text("candidate-noise", "filtered patch empty")
        assert result.startswith("DETERMINISTIC HINT")
        assert "noise" in result
        assert "filtered patch empty" in result

    def test_unknown_hint_returns_none(self):
        assert expand_hint_text("something-else", "reason") is None


class TestPrContextPreclassify:

    def test_fix_prefix_hints_peripheral(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched",
                    "title": "fix: correct sort order"}]
        manifest = _write_manifest(art_dir, entries)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__1")

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "candidate-peripheral"
        assert "fix:" in fm["pull_requests"][0]["hint_reason"]
        assert "DETERMINISTIC HINT" in fm["pull_requests"][0]["hint_text"]
        assert "peripheral" in fm["pull_requests"][0]["hint_text"]

    def test_test_prefix_hints_peripheral(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/2",
                    "file": "org__repo__2", "status": "fetched",
                    "title": "test: add source label filtering tests"}]
        manifest = _write_manifest(art_dir, entries)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__2")

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "candidate-peripheral"

    def test_review_comments_hints_peripheral(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/3",
                    "file": "org__repo__3", "status": "fetched",
                    "title": "Address review comments from #100"}]
        manifest = _write_manifest(art_dir, entries)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__3")

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "candidate-peripheral"

    def test_feat_prefix_no_hint(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/4",
                    "file": "org__repo__4", "status": "fetched",
                    "title": "feat: add MCP deployment modal"}]
        manifest = _write_manifest(art_dir, entries)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__4")

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "no-hint"
        assert "hint_text" not in fm["pull_requests"][0]

    def test_no_prefix_no_hint(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/5",
                    "file": "org__repo__5", "status": "fetched",
                    "title": "Add deploy button as extension"}]
        manifest = _write_manifest(art_dir, entries)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__5")

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "no-hint"
        assert "hint_text" not in fm["pull_requests"][0]

    def test_all_test_files_hints_peripheral(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/6",
                    "file": "org__repo__6", "status": "fetched",
                    "title": "Add Cypress mocked tests"}]
        manifest = _write_manifest(art_dir, entries)
        raw_dir = os.path.join(art_dir, "artifacts", "prcontext", "raw")
        _write_meta_yaml(raw_dir, "org__repo__6", files=[
            {"path": "tests/test_catalog.py"},
            {"path": "tests/test_deploy.py"},
        ])
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__6")

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "candidate-peripheral"
        assert "test globs" in fm["pull_requests"][0]["hint_reason"]

    def test_empty_filtered_patch_hints_noise(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/7",
                    "file": "org__repo__7", "status": "fetched",
                    "title": "feat: something filtered away"}]
        manifest = _write_manifest(art_dir, entries)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__7", content="")

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "candidate-noise"
        assert "DETERMINISTIC HINT" in fm["pull_requests"][0]["hint_text"]
        assert "noise" in fm["pull_requests"][0]["hint_text"]

    def test_mixed_files_no_file_hint(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/8",
                    "file": "org__repo__8", "status": "fetched",
                    "title": "Add feature with tests"}]
        manifest = _write_manifest(art_dir, entries)
        raw_dir = os.path.join(art_dir, "artifacts", "prcontext", "raw")
        _write_meta_yaml(raw_dir, "org__repo__8", files=[
            {"path": "src/api.go"},
            {"path": "tests/test_api.py"},
        ])
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__8")

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "no-hint"

    def test_skipped_entry_gets_no_hint(self, art_dir):
        entries = [{"url": "https://gitlab.example.com/proj/-/merge_requests/1",
                    "file": None, "status": "skipped",
                    "reason": "GitLab MR not yet supported"}]
        manifest = _write_manifest(art_dir, entries)

        result = _run_preclassify(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["pull_requests"][0]["hint"] == "no-hint"

    def test_missing_manifest_exits_1(self, art_dir):
        result = _run_preclassify(str(art_dir / "nonexistent.md"))
        assert result.returncode == 1


# ── Tier 1d: Verdict check script tests ───────────────────────────────────

class TestPrContextVerdictCheck:

    def test_mixed_verdicts_clean(self, art_dir):
        entries = [
            {"url": "u1", "file": "a__b__1", "status": "fetched"},
            {"url": "u2", "file": "a__b__2", "status": "fetched"},
            {"url": "u3", "file": "a__b__3", "status": "fetched"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "a__b__1", "relevant")
        _write_summary(out_dir, "a__b__2", "peripheral")
        _write_summary(out_dir, "a__b__3", "noise")

        result = _run_verdict_check(manifest, out_dir)
        assert result.returncode == 0

    def test_all_same_verdicts_flags_skew(self, art_dir):
        entries = [
            {"url": f"u{i}", "file": f"a__b__{i}", "status": "fetched"}
            for i in range(6)
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        for i in range(6):
            _write_summary(out_dir, f"a__b__{i}", "relevant")

        result = _run_verdict_check(manifest, out_dir)
        assert result.returncode == 1
        assert "Distribution skew" in result.stderr

    def test_hint_override_flags(self, art_dir):
        entries = [
            {"url": "u1", "file": "a__b__1", "status": "fetched",
             "hint": "candidate-peripheral", "hint_reason": "title prefix fix:"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "a__b__1", "relevant")

        result = _run_verdict_check(manifest, out_dir)
        assert result.returncode == 1
        assert "Hint override" in result.stderr

    def test_missing_summary_flags(self, art_dir):
        entries = [
            {"url": "u1", "file": "a__b__1", "status": "fetched"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")

        result = _run_verdict_check(manifest, out_dir)
        assert result.returncode == 1
        assert "Missing summary" in result.stderr

    def test_small_batch_no_skew_flag(self, art_dir):
        entries = [
            {"url": f"u{i}", "file": f"a__b__{i}", "status": "fetched"}
            for i in range(3)
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        for i in range(3):
            _write_summary(out_dir, f"a__b__{i}", "relevant")

        result = _run_verdict_check(manifest, out_dir)
        assert result.returncode == 0

    def test_verdict_check_creates_report(self, art_dir):
        entries = [
            {"url": "u1", "file": "a__b__1", "status": "fetched"},
            {"url": "u2", "file": "a__b__2", "status": "fetched"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "a__b__1", "relevant")
        _write_summary(out_dir, "a__b__2", "peripheral")

        _run_verdict_check(manifest, out_dir)

        report_path = os.path.join(out_dir, "verdict_check.md")
        assert os.path.isfile(report_path)
        fm, _ = _parse_manifest(report_path)
        assert fm["status"] == "clean"
        assert fm["total_fetched"] == 2

    def test_missing_manifest_exits_2(self, art_dir):
        result = _run_verdict_check(
            str(art_dir / "nonexistent.md"),
            str(art_dir / "artifacts" / "prcontext"))
        assert result.returncode == 2


# ── Tier 1e: Report script tests ────────────────────────────────────────────

class TestPrContextReport:

    def test_mixed_verdicts_generates_table(self, art_dir):
        entries = [
            {"url": "https://github.com/org/repo/pull/1",
             "file": "org__repo__1", "status": "fetched",
             "title": "Add feature X", "hint": "no-hint"},
            {"url": "https://github.com/org/repo/pull/2",
             "file": "org__repo__2", "status": "fetched",
             "title": "fix: typo", "hint": "candidate-peripheral"},
            {"url": "https://github.com/org/repo/pull/3",
             "file": "org__repo__3", "status": "fetched",
             "title": "chore: update deps", "hint": "candidate-noise"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "org__repo__1", "relevant",
                       gist="New feature X for users")
        _write_summary(out_dir, "org__repo__2", "peripheral",
                       gist="Fix a typo in config label")
        _write_summary(out_dir, "org__repo__3", "noise",
                       gist="Dependency lockfile update")

        result = _run_report(manifest)
        assert result.returncode == 0

        _, body = _parse_manifest(manifest)
        assert "| PR | Repo | Verdict | Hint | Gist |" in body
        assert "relevant" in body
        assert "peripheral" in body
        assert "noise" in body
        assert "New feature X for users" in body
        assert "**Totals:** 1 relevant, 1 peripheral, 1 noise" in body

    def test_report_includes_flags(self, art_dir):
        entries = [
            {"url": "https://github.com/org/repo/pull/1",
             "file": "org__repo__1", "status": "fetched",
             "title": "Add X", "hint": "no-hint"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "org__repo__1", "relevant", gist="Add X")

        vc_path = os.path.join(out_dir, "verdict_check.md")
        vc_fm = {"status": "flagged", "flags": 1}
        with open(vc_path, "w") as f:
            f.write("---\n")
            f.write(yaml.dump(vc_fm, default_flow_style=False,
                              sort_keys=False))
            f.write("---\n\n## Flags\n\n")
            f.write("- Distribution skew: 1/1 PRs verdict relevant (>80%)\n")

        result = _run_report(manifest)
        assert result.returncode == 0

        _, body = _parse_manifest(manifest)
        assert "## Flags" in body
        assert "Distribution skew" in body

    def test_report_handles_skipped_entries(self, art_dir):
        entries = [
            {"url": "https://github.com/org/repo/pull/1",
             "file": "org__repo__1", "status": "fetched",
             "title": "Add X", "hint": "no-hint"},
            {"url": "https://gitlab.example.com/proj/-/merge_requests/1",
             "file": None, "status": "skipped",
             "reason": "GitLab MR not yet supported", "hint": "no-hint"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "org__repo__1", "relevant",
                       gist="Add X feature")

        result = _run_report(manifest)
        assert result.returncode == 0

        _, body = _parse_manifest(manifest)
        assert "1 skipped" in body
        assert "gitlab" not in body.lower()

    def test_report_handles_missing_summaries(self, art_dir):
        entries = [
            {"url": "https://github.com/org/repo/pull/1",
             "file": "org__repo__1", "status": "fetched",
             "title": "Add X", "hint": "no-hint"},
            {"url": "https://github.com/org/repo/pull/2",
             "file": "org__repo__2", "status": "fetched",
             "title": "Add Y", "hint": "no-hint"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "org__repo__1", "relevant",
                       gist="Add X feature")

        result = _run_report(manifest)
        assert result.returncode == 0

        _, body = _parse_manifest(manifest)
        assert body.count("[#") == 1

    def test_missing_manifest_exits_2(self, art_dir):
        result = _run_report(str(art_dir / "nonexistent.md"))
        assert result.returncode == 2

    def test_gist_fallback_to_title(self, art_dir):
        entries = [
            {"url": "https://github.com/org/repo/pull/1",
             "file": "org__repo__1", "status": "fetched",
             "title": "Add feature X", "hint": "no-hint"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "org__repo__1", "relevant")

        result = _run_report(manifest)
        assert result.returncode == 0

        _, body = _parse_manifest(manifest)
        assert "Add feature X" in body

    def test_manifest_frontmatter_preserved(self, art_dir):
        entries = [
            {"url": "https://github.com/org/repo/pull/1",
             "file": "org__repo__1", "status": "fetched",
             "title": "Add X", "hint": "no-hint"},
        ]
        manifest = _write_manifest(art_dir, entries)
        out_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_summary(out_dir, "org__repo__1", "relevant", gist="Add X")

        result = _run_report(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(manifest)
        assert fm["started_at"] == "2026-01-01T00:00:00Z"
        assert fm["source_manifest"] == "artifacts/jiracontext.md"
        assert len(fm["pull_requests"]) == 1


# ── Tier 1f: Prepare script tests ─────────────────────────────────────────

def _run_prepare(manifest, target=None, template=None):
    cmd = [sys.executable, PREPARE_SCRIPT, "--manifest", manifest]
    if target:
        cmd.extend(["--target", target])
    if template:
        cmd.extend(["--template", template])
    return subprocess.run(cmd, capture_output=True, text=True)


def _write_target(directory, content="---\nscope: test\n---\nDoc target.\n"):
    """Write a jiracontext.md documentation target file."""
    path = os.path.join(directory, "artifacts", "jiracontext.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return path


def _write_template(directory, content=None):
    """Write a minimal prompt template with placeholders."""
    if content is None:
        content = ("Target: {documentation_target_file}\n\n"
                   "{pr_entries}\n")
    path = os.path.join(directory, "template.md")
    with open(path, "w") as f:
        f.write(content)
    return path


class TestPrContextPrepare:

    def test_single_pr_produces_one_prompt(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched",
                    "title": "Add feature X"}]
        manifest = _write_manifest(art_dir, entries)
        target = _write_target(art_dir)
        template = _write_template(art_dir)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__1")

        result = _run_prepare(manifest, target=target, template=template)
        assert result.returncode == 0

        output = json.loads(result.stdout.strip())
        assert len(output["prompts"]) == 1
        assert output["noise_written"] == 0
        assert output["prompts"][0].endswith("org__repo__1.prompt.md")
        assert os.path.isfile(output["prompts"][0])

    def test_empty_patch_writes_noise_summary(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched",
                    "title": "All noise"}]
        manifest = _write_manifest(art_dir, entries)
        target = _write_target(art_dir)
        template = _write_template(art_dir)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__1", content="")

        result = _run_prepare(manifest, target=target, template=template)
        assert result.returncode == 0

        output = json.loads(result.stdout.strip())
        assert output["noise_written"] == 1
        assert len(output["prompts"]) == 0

        summary_path = os.path.join(
            art_dir, "artifacts", "prcontext", "org__repo__1.md")
        assert os.path.isfile(summary_path)
        fm, _ = _parse_manifest(summary_path)
        assert fm["verdict"] == "noise"

    def test_each_pr_gets_own_prompt(self, art_dir):
        entries = [
            {"url": f"https://github.com/org/repo/pull/{i}",
             "file": f"org__repo__{i}", "status": "fetched",
             "title": f"PR {i}"}
            for i in range(12)
        ]
        manifest = _write_manifest(art_dir, entries)
        target = _write_target(art_dir)
        template = _write_template(art_dir)
        filtered_dir = os.path.join(
            art_dir, "artifacts", "prcontext", "filtered")
        for i in range(12):
            _write_filtered_patch(filtered_dir, f"org__repo__{i}")

        result = _run_prepare(manifest, target=target, template=template)
        assert result.returncode == 0

        output = json.loads(result.stdout.strip())
        assert len(output["prompts"]) == 12
        for i in range(12):
            assert any(p.endswith(f"org__repo__{i}.prompt.md")
                       for p in output["prompts"])

    def test_prompt_file_contains_template_content(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/42",
                    "file": "org__repo__42", "status": "fetched",
                    "title": "Add MCP feature"}]
        manifest = _write_manifest(art_dir, entries)
        target = _write_target(art_dir)
        template = _write_template(art_dir)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__42")

        result = _run_prepare(manifest, target=target, template=template)
        assert result.returncode == 0

        output = json.loads(result.stdout.strip())
        with open(output["prompts"][0]) as f:
            content = f.read()
        assert "Target:" in content
        assert "{documentation_target_file}" not in content
        assert "{pr_entries}" not in content
        assert "org/repo" in content
        assert "42" in content
        assert "Add MCP feature" in content

    def test_hint_text_appears_in_prompt(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched",
                    "title": "fix: something",
                    "hint_text": "DETERMINISTIC HINT: peripheral"}]
        manifest = _write_manifest(art_dir, entries)
        target = _write_target(art_dir)
        template = _write_template(art_dir)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__1")

        result = _run_prepare(manifest, target=target, template=template)
        assert result.returncode == 0

        output = json.loads(result.stdout.strip())
        with open(output["prompts"][0]) as f:
            content = f.read()
        assert "DETERMINISTIC HINT: peripheral" in content

    def test_no_hint_text_uses_none(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched",
                    "title": "Add feature"}]
        manifest = _write_manifest(art_dir, entries)
        target = _write_target(art_dir)
        template = _write_template(art_dir)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__1")

        result = _run_prepare(manifest, target=target, template=template)
        assert result.returncode == 0

        output = json.loads(result.stdout.strip())
        with open(output["prompts"][0]) as f:
            content = f.read()
        assert "hint_block: (none)" in content

    def test_skipped_entries_ignored(self, art_dir):
        entries = [
            {"url": "https://github.com/org/repo/pull/1",
             "file": "org__repo__1", "status": "fetched",
             "title": "Add X"},
            {"url": "https://gitlab.example.com/proj/-/merge_requests/1",
             "file": None, "status": "skipped",
             "reason": "GitLab MR not yet supported"},
        ]
        manifest = _write_manifest(art_dir, entries)
        target = _write_target(art_dir)
        template = _write_template(art_dir)
        _write_filtered_patch(
            os.path.join(art_dir, "artifacts", "prcontext", "filtered"),
            "org__repo__1")

        result = _run_prepare(manifest, target=target, template=template)
        assert result.returncode == 0

        output = json.loads(result.stdout.strip())
        assert len(output["prompts"]) == 1

    def test_missing_manifest_exits_2(self, art_dir):
        result = _run_prepare(str(art_dir / "nonexistent.md"))
        assert result.returncode == 2


# ── Tier 1g: Sanitize YAML script tests ────────────────────────────────────


def _write_raw_summary(output_dir, stem, raw_frontmatter, body="## What changed\n\nTest.\n"):
    """Write a summary file with raw (potentially broken) YAML frontmatter."""
    path = os.path.join(output_dir, f"{stem}.md")
    with open(path, "w") as f:
        f.write("---\n")
        f.write(raw_frontmatter)
        f.write("---\n\n")
        f.write(body)
    return path


def _run_sanitize(manifest):
    return subprocess.run(
        [sys.executable, SANITIZE_SCRIPT, "--manifest", manifest],
        capture_output=True, text=True,
    )


class TestPrContextSanitizeYaml:

    def test_unquoted_title_with_colon_gets_quoted(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched"}]
        manifest = _write_manifest(art_dir, entries)
        output_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_raw_summary(output_dir, "org__repo__1",
                           "verdict: relevant\n"
                           "title: feat: Add MCP server deployments\n"
                           "gist: Adds deployment list page\n")

        result = _run_sanitize(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(
            os.path.join(output_dir, "org__repo__1.md"))
        assert fm["title"] == "feat: Add MCP server deployments"

    def test_unquoted_gist_with_colon_gets_quoted(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched"}]
        manifest = _write_manifest(art_dir, entries)
        output_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_raw_summary(output_dir, "org__repo__1",
                           "verdict: relevant\n"
                           "title: Add feature\n"
                           "gist: Cleanup following PR 7063: removes compat code\n")

        result = _run_sanitize(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(
            os.path.join(output_dir, "org__repo__1.md"))
        assert fm["gist"] == "Cleanup following PR 7063: removes compat code"

    def test_already_quoted_stays_unchanged(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched"}]
        manifest = _write_manifest(art_dir, entries)
        output_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_raw_summary(output_dir, "org__repo__1",
                           'verdict: relevant\n'
                           'title: "feat: Add MCP servers"\n'
                           'gist: "Does X: then Y"\n')

        result = _run_sanitize(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(
            os.path.join(output_dir, "org__repo__1.md"))
        assert fm["title"] == "feat: Add MCP servers"
        assert fm["gist"] == "Does X: then Y"

    def test_no_colon_stays_unchanged(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched"}]
        manifest = _write_manifest(art_dir, entries)
        output_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_raw_summary(output_dir, "org__repo__1",
                           "verdict: relevant\n"
                           "title: Add feature X\n"
                           "gist: Adds a new feature\n")

        result = _run_sanitize(manifest)
        assert result.returncode == 0

        fm, _ = _parse_manifest(
            os.path.join(output_dir, "org__repo__1.md"))
        assert fm["title"] == "Add feature X"

    def test_body_content_preserved(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched"}]
        manifest = _write_manifest(art_dir, entries)
        output_dir = os.path.join(art_dir, "artifacts", "prcontext")
        body = "## Verdict reasoning\n\nThis is important: keep it.\n"
        _write_raw_summary(output_dir, "org__repo__1",
                           "verdict: relevant\n"
                           "title: feat: Add X\n",
                           body=body)

        result = _run_sanitize(manifest)
        assert result.returncode == 0

        path = os.path.join(output_dir, "org__repo__1.md")
        with open(path) as f:
            text = f.read()
        assert body in text

    def test_idempotent(self, art_dir):
        entries = [{"url": "https://github.com/org/repo/pull/1",
                    "file": "org__repo__1", "status": "fetched"}]
        manifest = _write_manifest(art_dir, entries)
        output_dir = os.path.join(art_dir, "artifacts", "prcontext")
        _write_raw_summary(output_dir, "org__repo__1",
                           "verdict: relevant\n"
                           "title: feat: Add MCP servers\n")

        _run_sanitize(manifest)
        path = os.path.join(output_dir, "org__repo__1.md")
        with open(path) as f:
            after_first = f.read()

        _run_sanitize(manifest)
        with open(path) as f:
            after_second = f.read()

        assert after_first == after_second

    def test_referenced_script_exists(self):
        with open(SKILL_MD) as f:
            content = f.read()
        assert "pr_context_sanitize_yaml.py" in content
        assert os.path.isfile(SANITIZE_SCRIPT)
