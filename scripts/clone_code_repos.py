#!/usr/bin/env python3
"""Shallow-clone code repositories listed in jiracontext.md.

Reads the code_repositories list from the jiracontext.md YAML frontmatter
and clones each repository at depth 1 into artifacts/codecontext/<repo-name>.
Skips repositories that already exist on disk. Failures are logged but do not
stop the script from attempting the remaining repositories.

Usage:
    python3 scripts/clone_code_repos.py
    python3 scripts/clone_code_repos.py --manifest artifacts/jiracontext.md --output-dir artifacts/codecontext

Exit codes:
    0  All repositories cloned (or already present) successfully
    1  One or more repositories failed to clone
    2  Manifest missing or has no code_repositories
"""

import argparse
import logging
import os
import subprocess
import sys
from urllib.parse import urlparse

import yaml

log = logging.getLogger("clone_code_repos")

DEFAULT_MANIFEST = "artifacts/jiracontext.md"
DEFAULT_OUTPUT_DIR = "artifacts/codecontext"

DEFAULT_EXCLUDE_DOMAINS = ["redhat.com"]
DEFAULT_EXCLUDE_REPOS = ["opendatahub-io/opendatahub-tests"]


def _parse_manifest(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    _, fm_raw, _ = text.split("---\n", 2)
    return yaml.safe_load(fm_raw)


def _repo_dirname(url):
    """Derive a directory name from a repository URL.

    Uses org--repo for GitHub/GitLab URLs to avoid collisions when
    multiple orgs have repos with the same name.
    """
    parsed = urlparse(url.rstrip("/"))
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}--{parts[-1]}"
    return parts[-1] if parts else "unknown"


def _is_excluded(url, exclude_domains, exclude_repos):
    """Check whether a repo URL matches any exclusion rule."""
    parsed = urlparse(url.rstrip("/"))
    if any(parsed.hostname and parsed.hostname.endswith(d)
           for d in exclude_domains):
        return True
    path = parsed.path.strip("/")
    if any(path == r or path.startswith(r + "/") for r in exclude_repos):
        return True
    return False


def _clone(url, dest):
    """Shallow-clone a repository. Returns True on success."""
    cmd = ["git", "clone", "--depth", "1", url, dest]
    log.info("Cloning %s -> %s", url, dest)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Failed to clone %s:\n%s", url, result.stderr.strip())
        return False
    log.info("OK: %s", dest)
    return True


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--manifest", default=DEFAULT_MANIFEST,
        help=f"Path to jiracontext.md (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for cloned repos (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--no-default-excludes", action="store_true",
        help="Disable default domain and repo exclusions",
    )
    parser.add_argument(
        "--exclude-domain", action="append", default=[],
        help="Additional domain suffixes to exclude (repeatable)",
    )
    parser.add_argument(
        "--exclude-repo", action="append", default=[],
        help="Additional org/repo paths to exclude (repeatable)",
    )
    args = parser.parse_args()

    if args.no_default_excludes:
        exclude_domains = list(args.exclude_domain)
        exclude_repos = list(args.exclude_repo)
    else:
        exclude_domains = DEFAULT_EXCLUDE_DOMAINS + args.exclude_domain
        exclude_repos = DEFAULT_EXCLUDE_REPOS + args.exclude_repo

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not os.path.exists(args.manifest):
        log.error("Manifest not found: %s", args.manifest)
        sys.exit(2)

    fm = _parse_manifest(args.manifest)
    repos = fm.get("code_repositories", [])
    if not repos:
        log.error("No code_repositories found in %s", args.manifest)
        sys.exit(2)

    os.makedirs(args.output_dir, exist_ok=True)

    failures = 0
    cloned = 0
    for url in repos:
        if _is_excluded(url, exclude_domains, exclude_repos):
            log.info("Excluded by filter: %s", url)
            continue
        dirname = _repo_dirname(url)
        dest = os.path.join(args.output_dir, dirname)
        if os.path.isdir(dest):
            log.info("Already exists, skipping: %s", dest)
            cloned += 1
            continue
        if _clone(url, dest):
            cloned += 1
        else:
            failures += 1

    if failures:
        log.error("%d repo(s) failed to clone", failures)
        sys.exit(1)

    log.info("Done. %d repo(s) in %s", cloned, args.output_dir)


if __name__ == "__main__":
    main()
