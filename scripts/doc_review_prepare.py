#!/usr/bin/env python3
"""Prepare per-module review prompts for documentation reviewer agents.

Reads the writer-config.json and doccontext manifests, snapshots all
written modules for auditability, consolidates style guidelines into
a single rubric, builds per-module evidence maps into codecontext,
and writes prompt files for style and technical reviewer agents.

Files read (not modified)
    artifacts/docwrite/writer-config.json — writer config with module list
    artifacts/doccontext.md — consolidated context manifest
    .claude/skills/guidelines/*.md — style guideline files
    {target_path} files — written documentation files (snapshotted, not modified)

Files written
    artifacts/docreview/reviewer-config.json — config for skill orchestrator
    artifacts/docreview/snapshots/{slug}.adoc — pre-review snapshots
    artifacts/docreview/style-rubric.md — consolidated style guidelines
    artifacts/docreview/{slug}.style-prompt.md — per-module style review prompts
    artifacts/docreview/{slug}.technical-prompt.md — per-module technical review prompts

Usage:
    python3 scripts/doc_review_prepare.py
    python3 scripts/doc_review_prepare.py --config artifacts/docwrite/writer-config.json
"""

import argparse
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

log = logging.getLogger("doc_review_prepare")

DEFAULT_CONFIG = "artifacts/docwrite/writer-config.json"
DEFAULT_DOCCONTEXT = "artifacts/doccontext.md"
DEFAULT_OUTPUT_DIR = "artifacts/docreview"
GUIDELINES_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "guidelines"
FORMAT_REFERENCE = "artifacts/docwrite/format-reference.md"


def _parse_manifest(manifest_path):
    """Read a manifest markdown file and return (frontmatter_dict, body_str)."""
    with open(manifest_path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{manifest_path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


# ---------------------------------------------------------------------------
# Phase 1: Parse writer config
# ---------------------------------------------------------------------------

def load_writer_config(config_path):
    """Load and validate writer-config.json."""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    modules = config.get("modules", [])
    if not modules:
        raise ValueError("No modules in writer config")

    return config


# ---------------------------------------------------------------------------
# Phase 2: Verify written files exist
# ---------------------------------------------------------------------------

def verify_written_files(modules):
    """Check that all written module files exist. Returns list of missing."""
    missing = []
    for mod in modules:
        target = Path(mod["target_path"])
        if not target.exists():
            missing.append(mod["slug"])
            log.error("Written file not found: %s (%s)", mod["slug"], mod["target_path"])
    return missing


# ---------------------------------------------------------------------------
# Phase 3: Snapshot modules
# ---------------------------------------------------------------------------

def snapshot_modules(modules, snapshot_dir):
    """Copy each written module to snapshot directory for auditability."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for mod in modules:
        target = Path(mod["target_path"])
        if target.exists():
            dest = snapshot_dir / target.name
            shutil.copy2(target, dest)
            log.info("Snapshot: %s → %s", target.name, dest)


# ---------------------------------------------------------------------------
# Phase 4: Consolidate style guidelines
# ---------------------------------------------------------------------------

def consolidate_guidelines(guidelines_dir, output_path):
    """Read all guideline files and concatenate into a single rubric."""
    if not guidelines_dir.is_dir():
        log.warning("Guidelines directory not found: %s", guidelines_dir)
        return False

    guideline_files = sorted(guidelines_dir.glob("*.md"))
    if not guideline_files:
        log.warning("No guideline files found in %s", guidelines_dir)
        return False

    sections = []
    sections.append("# Documentation Style Review Rubric\n")
    sections.append("**Precedence:** For Red Hat documentation, RH-SSG rules take ")
    sections.append("precedence over IBM-SG rules where they conflict. Repository-specific ")
    sections.append("conventions from the format reference override both.\n\n")
    sections.append("---\n\n")

    for gf in guideline_files:
        content = gf.read_text(encoding="utf-8")
        if content.startswith("---\n"):
            parts = content.split("---\n", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        sections.append(f"<!-- source: {gf.name} -->\n")
        sections.append(content)
        sections.append("\n\n---\n\n")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(sections), encoding="utf-8")
    log.info("Consolidated %d guidelines → %s", len(guideline_files), output_path)
    return True


# ---------------------------------------------------------------------------
# Phase 5: Build codecontext evidence map
# ---------------------------------------------------------------------------

def _repo_short_name_from_url(url):
    """Extract short repo name from a GitHub PR URL."""
    parts = url.rstrip("/").split("/")
    if len(parts) >= 5 and parts[-2] == "pull":
        return parts[-3]
    return ""


def _org_repo_from_url(url):
    """Extract org--repo directory name from a GitHub PR URL."""
    parts = url.rstrip("/").split("/")
    if len(parts) >= 5 and parts[-2] == "pull":
        org = parts[-4]
        repo = parts[-3]
        return f"{org}--{repo}"
    return ""


def build_codecontext_map(doccontext_path, modules, writer_config):
    """Map each module to relevant codecontext directories via PR evidence."""
    codecontext_base = Path("artifacts/codecontext")
    if not codecontext_base.is_dir():
        log.warning("No codecontext directory found")
        return {}

    available_repos = {d.name for d in codecontext_base.iterdir() if d.is_dir()}

    pr_repo_map = {}
    try:
        fm, _ = _parse_manifest(doccontext_path)
        for pr in fm.get("pull_requests", []):
            url = pr.get("url", "")
            org_repo = _org_repo_from_url(url)
            short_name = _repo_short_name_from_url(url)
            number = url.rstrip("/").split("/")[-1] if "/" in url else ""
            if short_name and number:
                ref_key = f"{short_name}#{number}"
                pr_repo_map[ref_key] = org_repo
    except Exception as e:
        log.warning("Could not parse doccontext for PR mapping: %s", e)

    module_evidence = {}
    for mod in modules:
        prompt_file = mod.get("prompt_file", "")
        if not prompt_file or not Path(prompt_file).exists():
            module_evidence[mod["slug"]] = {"codecontext_dirs": [], "prompt_file": prompt_file}
            continue

        prompt_content = Path(prompt_file).read_text(encoding="utf-8")

        referenced_repos = set()
        for ref_key, org_repo in pr_repo_map.items():
            if ref_key in prompt_content and org_repo in available_repos:
                referenced_repos.add(org_repo)

        codecontext_dirs = [str(codecontext_base / repo) for repo in sorted(referenced_repos)]
        module_evidence[mod["slug"]] = {
            "codecontext_dirs": codecontext_dirs,
            "prompt_file": prompt_file,
        }

    return module_evidence


# ---------------------------------------------------------------------------
# Phase 6–7: Write per-module prompt files
# ---------------------------------------------------------------------------

def write_style_prompt(mod, rubric_path, format_ref_path, output_dir):
    """Write a style review prompt file for one module."""
    slug = mod["slug"]
    prompt_path = output_dir / f"{slug}.style-prompt.md"

    lines = [
        f"# Style Review: {mod.get('title', slug)}",
        "",
        f"**Module type:** {mod.get('type', 'unknown')}",
        f"**Module file:** `{mod['target_path']}`",
        f"**Style rubric:** `{rubric_path}`",
        f"**Format reference:** `{format_ref_path}`",
        f"**Findings output:** `{output_dir / (slug + '.style-findings.json')}`",
        "",
    ]

    prompt_path.write_text("\n".join(lines), encoding="utf-8")
    return str(prompt_path)


def write_technical_prompt(mod, evidence, doccontext_path, output_dir):
    """Write a technical review prompt file for one module."""
    slug = mod["slug"]
    prompt_path = output_dir / f"{slug}.technical-prompt.md"

    lines = [
        f"# Technical Review: {mod.get('title', slug)}",
        "",
        f"**Module type:** {mod.get('type', 'unknown')}",
        f"**Module file:** `{mod['target_path']}`",
        f"**Evidence confidence:** {mod.get('evidence_confidence', 'unknown')}",
        f"**Writer evidence file:** `{evidence.get('prompt_file', 'N/A')}`",
        f"**Doccontext:** `{doccontext_path}`",
        f"**Findings output:** `{output_dir / (slug + '.technical-findings.json')}`",
        "",
        "## Codecontext directories",
        "",
    ]

    codecontext_dirs = evidence.get("codecontext_dirs", [])
    if codecontext_dirs:
        for d in codecontext_dirs:
            lines.append(f"- `{d}`")
    else:
        lines.append("(No codecontext mapped for this module)")

    lines.append("")
    prompt_path.write_text("\n".join(lines), encoding="utf-8")
    return str(prompt_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Prepare per-module prompts for documentation reviewer agents."
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG,
        help=f"Path to writer-config.json (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--doccontext", default=DEFAULT_DOCCONTEXT,
        help=f"Path to doccontext manifest (default: {DEFAULT_DOCCONTEXT})",
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--guidelines-dir", type=Path, default=GUIDELINES_DIR,
        help=f"Guidelines directory (default: {GUIDELINES_DIR})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(args.config)
    if not config_path.exists():
        log.error("Writer config not found: %s", args.config)
        sys.exit(2)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: Parse writer config
    try:
        config = load_writer_config(config_path)
    except Exception as e:
        log.error("Failed to parse writer config: %s", e)
        sys.exit(2)

    modules = config["modules"]
    target_repo = config.get("target_repo", "")
    repo_profile = config.get("repo_profile", {})
    log.info("Loaded %d modules from writer config", len(modules))

    # Phase 2: Verify written files
    missing = verify_written_files(modules)
    if missing:
        log.error("Missing %d written files: %s", len(missing), ", ".join(missing))
        sys.exit(2)

    # Phase 3: Snapshot
    snapshot_dir = output_dir / "snapshots"
    snapshot_modules(modules, snapshot_dir)

    # Phase 4: Consolidate guidelines
    rubric_path = output_dir / "style-rubric.md"
    format_ref_path = Path(FORMAT_REFERENCE)
    has_guidelines = consolidate_guidelines(args.guidelines_dir, rubric_path)

    warnings = []
    if not has_guidelines:
        warnings.append("No style guidelines found — style review will be limited")
    if not format_ref_path.exists():
        warnings.append("Format reference not found — style review will lack repo-specific conventions")

    # Phase 5: Build codecontext evidence map
    doccontext_path = Path(args.doccontext)
    evidence_map = {}
    if doccontext_path.exists():
        evidence_map = build_codecontext_map(args.doccontext, modules, config)
    else:
        log.warning("Doccontext not found: %s", args.doccontext)
        warnings.append("Doccontext not found — technical review will lack evidence mapping")

    # Phase 6-7: Write per-module prompt files
    review_modules = []
    for mod in modules:
        slug = mod["slug"]
        evidence = evidence_map.get(slug, {"codecontext_dirs": [], "prompt_file": ""})

        style_prompt = write_style_prompt(mod, str(rubric_path), str(format_ref_path), output_dir)
        technical_prompt = write_technical_prompt(mod, evidence, str(doccontext_path), output_dir)

        review_modules.append({
            "slug": slug,
            "title": mod.get("title", slug),
            "type": mod.get("type", "unknown"),
            "target_path": mod["target_path"],
            "evidence_confidence": mod.get("evidence_confidence", "unknown"),
            "style_prompt": style_prompt,
            "technical_prompt": technical_prompt,
            "codecontext_dirs": evidence.get("codecontext_dirs", []),
            "prompt_file": evidence.get("prompt_file", ""),
        })

    # Phase 8: Write reviewer-config.json
    reviewer_config = {
        "source_config": str(config_path),
        "target_repo": target_repo,
        "snapshot_dir": str(snapshot_dir),
        "review_started_at": datetime.now(timezone.utc).isoformat(),
        "style_rubric": str(rubric_path),
        "format_reference": str(format_ref_path) if format_ref_path.exists() else None,
        "repo_profile": repo_profile,
        "modules": review_modules,
    }

    reviewer_config_path = output_dir / "reviewer-config.json"
    with open(reviewer_config_path, "w", encoding="utf-8") as f:
        json.dump(reviewer_config, f, indent=2)

    log.info("Wrote reviewer config to %s", reviewer_config_path)
    log.info("Prepared %d modules for review", len(review_modules))

    for w in warnings:
        log.warning(w)

    summary = {
        "config_path": str(reviewer_config_path),
        "module_count": len(review_modules),
        "snapshot_dir": str(snapshot_dir),
        "style_rubric": str(rubric_path),
        "has_guidelines": has_guidelines,
        "has_format_reference": format_ref_path.exists(),
        "warnings": warnings,
        "modules": [
            {
                "slug": m["slug"],
                "type": m["type"],
                "confidence": m["evidence_confidence"],
                "codecontext_count": len(m["codecontext_dirs"]),
            }
            for m in review_modules
        ],
    }
    print(json.dumps(summary))

    if warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
