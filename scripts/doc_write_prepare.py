#!/usr/bin/env python3
"""Prepare per-module prompt files for the documentation writer agents.

Reads the docplan and doccontext manifests, performs lightweight scanning
of the target documentation repository, extracts per-module evidence,
and writes prompt files that writer agents will consume.

The format reference (style rules, templates, conventions) is NOT produced
here — that's the repo profiler agent's job. This script does only
mechanical/deterministic work.

Files read (not modified)
    artifacts/docplan/docplan.md — documentation plan
    artifacts/doccontext.md — consolidated context manifest
    artifacts/jiracontext/*.md — JIRA issue files
    artifacts/prcontext/*.md — PR summary files
    {target_repo}/ — target documentation repository (scanned, not modified)
Files written
    artifacts/docwrite/writer-config.json — config for skill orchestrator
    artifacts/docwrite/{slug}.prompt.md — per-module prompt files

Usage:
    python3 scripts/doc_write_prepare.py
    python3 scripts/doc_write_prepare.py --target-repo /path/to/docs --draft
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import yaml

log = logging.getLogger("doc_write_prepare")

DEFAULT_DOCPLAN = "artifacts/docplan/docplan.md"
DEFAULT_DOCCONTEXT = "artifacts/doccontext.md"
DEFAULT_TARGET_REPO = "/Users/mmortari/git/openshift-ai-documentation"
DEFAULT_OUTPUT_DIR = "artifacts/docwrite"


# ---------------------------------------------------------------------------
# Manifest parsing (reused pattern from other scripts)
# ---------------------------------------------------------------------------

def _parse_manifest(manifest_path):
    """Read a manifest markdown file and return (frontmatter_dict, body_str)."""
    with open(manifest_path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{manifest_path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _extract_section(body, heading):
    """Extract a markdown section by ## heading."""
    lines = body.split("\n")
    capturing = False
    buf = []
    for line in lines:
        if re.match(r"^##\s+", line):
            if capturing:
                break
            if re.match(rf"^##\s+{re.escape(heading)}\s*$", line, re.IGNORECASE):
                capturing = True
                continue
        elif capturing:
            buf.append(line)
    return "\n".join(buf).strip() if buf else None


# ---------------------------------------------------------------------------
# Phase 1: Parse docplan
# ---------------------------------------------------------------------------

def parse_docplan(docplan_path):
    """Extract module specifications from docplan.md body."""
    _, body = _parse_manifest(docplan_path)
    modules = []
    current = None

    for line in body.split("\n"):
        m = re.match(r"^###\s+Module:\s+(.+)$", line)
        if m:
            if current:
                modules.append(current)
            current = {
                "title": m.group(1).strip(),
                "type": None,
                "personas": [],
                "journey_phase": None,
                "job_statement": None,
                "source_evidence": {"jira": [], "prs": []},
                "content_outline": [],
                "prerequisites": None,
                "dev_preview_disclaimer": None,
            }
            continue

        if current is None:
            continue

        field = re.match(r"^- \*\*(.+?):\*\*\s*(.*)$", line)
        if field:
            key, val = field.group(1).strip(), field.group(2).strip()
            if key == "Type":
                current["type"] = val.lower()
            elif key == "Persona":
                current["personas"] = [p.strip() for p in val.split(",")]
            elif key == "Journey Phase":
                current["journey_phase"] = val.lower()
            elif key == "Job Statement":
                current["job_statement"] = val
            elif key == "Prerequisites":
                current["prerequisites"] = val if val.lower() != "none" else None
            elif key == "Dev Preview Disclaimer":
                current["dev_preview_disclaimer"] = val.lower()
            elif key == "Content Outline":
                pass  # content outline items are sub-bullets
            elif key == "Source Evidence":
                pass  # source evidence items are sub-bullets
            continue

        # Sub-bullets for Source Evidence
        evidence_match = re.match(r"^\s+- (JIRA|PRs):\s*(.+)$", line)
        if evidence_match and current:
            etype, refs = evidence_match.group(1), evidence_match.group(2)
            ref_list = [r.strip() for r in refs.split(",")]
            if etype == "JIRA":
                current["source_evidence"]["jira"] = ref_list
            elif etype == "PRs":
                current["source_evidence"]["prs"] = ref_list
            continue

        # Sub-bullets for Content Outline
        outline_match = re.match(r"^\s+- (.+)$", line)
        if outline_match and current:
            item = outline_match.group(1).strip()
            if not re.match(r"^(JIRA|PRs):", item):
                current["content_outline"].append(item)

    if current:
        modules.append(current)

    return modules


# ---------------------------------------------------------------------------
# Phase 2: Parse doccontext and build evidence indices
# ---------------------------------------------------------------------------

def _repo_short_name_from_url(url):
    """Extract short repo name from a GitHub PR URL.

    Example: https://github.com/opendatahub-io/odh-dashboard/pull/6771
    → 'odh-dashboard'
    """
    parts = url.rstrip("/").split("/")
    if len(parts) >= 5 and parts[-2] == "pull":
        return parts[-3]
    return ""


def _pr_number_from_url(url):
    """Extract PR number from a GitHub PR URL."""
    parts = url.rstrip("/").split("/")
    if parts:
        return parts[-1]
    return ""


def _stem_from_patch_path(patch_path):
    """Derive the file stem from a filtered_patch path."""
    if not patch_path:
        return None
    return Path(patch_path).stem


def build_evidence_indices(doccontext_path):
    """Build lookup indices for JIRA issues and PRs from doccontext.md."""
    fm, _ = _parse_manifest(doccontext_path)

    jira_index = {}
    for issue in fm.get("jira_issues", []):
        jira_index[issue["key"]] = issue.get("path", "")

    pr_index = {}
    for pr in fm.get("pull_requests", []):
        url = pr.get("url", "")
        short_name = _repo_short_name_from_url(url)
        number = _pr_number_from_url(url)
        if short_name and number:
            ref_key = f"{short_name}#{number}"
            stem = _stem_from_patch_path(pr.get("filtered_patch"))
            pr_index[ref_key] = {
                "url": url,
                "title": pr.get("title", ""),
                "verdict": pr.get("verdict", ""),
                "gist": pr.get("gist", ""),
                "stem": stem,
            }

    return jira_index, pr_index


# ---------------------------------------------------------------------------
# Phase 3: Lightweight repo scan
# ---------------------------------------------------------------------------

def _detect_framework(target_repo):
    """Detect documentation framework by file extensions and config files."""
    repo = Path(target_repo)

    adoc_count = len(list(repo.rglob("*.adoc")))
    md_count = len(list(repo.glob("**/*.md")))  # non-recursive first level check
    rst_count = len(list(repo.rglob("*.rst")))

    if (repo / "antora.yml").exists() or (repo / "_artifacts").is_dir():
        return "asciidoc"
    if adoc_count > 10:
        return "asciidoc"
    if (repo / "mkdocs.yml").exists():
        return "mkdocs"
    if (repo / "conf.py").exists() and rst_count > 5:
        return "sphinx"
    if (repo / "docusaurus.config.js").exists() or (repo / "docusaurus.config.ts").exists():
        return "docusaurus"
    if md_count > rst_count and md_count > adoc_count:
        return "mkdocs"
    if rst_count > md_count:
        return "sphinx"
    if adoc_count > 0:
        return "asciidoc"
    return "unknown"


FRAMEWORK_EXTENSIONS = {
    "asciidoc": ".adoc",
    "mkdocs": ".md",
    "sphinx": ".rst",
    "docusaurus": ".md",
    "unknown": ".md",
}

CANDIDATE_MODULE_DIRS = ["modules", "docs", "content", "source", "pages", "topics"]
CANDIDATE_ASSEMBLY_DIRS = ["assemblies", "_data", "navigation"]


def _detect_dir(target_repo, candidates):
    """Find the first existing candidate directory."""
    repo = Path(target_repo)
    for d in candidates:
        if (repo / d).is_dir():
            return d
    return None


def _find_product_attributes_file(target_repo, framework):
    """Find product attributes/config file if it exists."""
    repo = Path(target_repo)
    if framework == "asciidoc":
        candidates = [
            repo / "_artifacts" / "document-attributes-global.adoc",
            repo / "_artifacts" / "document-attributes.adoc",
        ]
        for c in candidates:
            if c.exists():
                return str(c)
    if framework == "mkdocs":
        mkdocs_yml = repo / "mkdocs.yml"
        if mkdocs_yml.exists():
            return str(mkdocs_yml)
    return None


def _find_claude_md(target_repo):
    """Find CLAUDE.md in the target repo."""
    claude_md = Path(target_repo) / "CLAUDE.md"
    return str(claude_md) if claude_md.exists() else None


ASCIIDOC_TYPE_PATTERNS = {
    "concept": re.compile(r":_mod-docs-content-type:\s*CONCEPT", re.IGNORECASE),
    "procedure": re.compile(r":_mod-docs-content-type:\s*PROCEDURE", re.IGNORECASE),
    "reference": re.compile(r":_mod-docs-content-type:\s*REFERENCE", re.IGNORECASE),
}


def _sample_files(target_repo, modules_dir, framework, needed_types):
    """Find one sample file per content type for the profiler agent."""
    if not modules_dir:
        return {}

    mod_path = Path(target_repo) / modules_dir
    if not mod_path.is_dir():
        return {}

    ext = FRAMEWORK_EXTENSIONS.get(framework, ".md")
    files = sorted(mod_path.glob(f"*{ext}"))
    samples = {}

    if framework == "asciidoc":
        for f in files:
            if len(samples) >= len(needed_types):
                break
            try:
                content = f.read_text(encoding="utf-8")[:500]
            except Exception:
                continue
            for ctype, pattern in ASCIIDOC_TYPE_PATTERNS.items():
                if ctype in needed_types and ctype not in samples:
                    if pattern.search(content):
                        samples[ctype] = str(f)
    else:
        for ctype in needed_types:
            if files:
                samples[ctype] = str(files[0])

    return samples


def _list_existing_files(target_repo, modules_dir, ext):
    """List existing file stems in the modules directory."""
    if not modules_dir:
        return set()
    mod_path = Path(target_repo) / modules_dir
    if not mod_path.is_dir():
        return set()
    return {f.stem for f in mod_path.glob(f"*{ext}")}


def scan_target_repo(target_repo, needed_types):
    """Perform lightweight scan of the target documentation repository."""
    framework = _detect_framework(target_repo)
    ext = FRAMEWORK_EXTENSIONS.get(framework, ".md")
    modules_dir = _detect_dir(target_repo, CANDIDATE_MODULE_DIRS)
    assemblies_dir = _detect_dir(target_repo, CANDIDATE_ASSEMBLY_DIRS)

    return {
        "framework": framework,
        "file_extension": ext,
        "modules_dir": modules_dir,
        "assemblies_dir": assemblies_dir,
        "existing_files": sorted(_list_existing_files(target_repo, modules_dir, ext)),
        "product_attributes_file": _find_product_attributes_file(target_repo, framework),
        "claude_md_path": _find_claude_md(target_repo),
        "sample_file_paths": _sample_files(target_repo, modules_dir, framework, needed_types),
    }


# ---------------------------------------------------------------------------
# Phase 4: Extract per-module evidence and write prompt files
# ---------------------------------------------------------------------------

def _title_to_slug(title):
    """Convert module title to kebab-case slug."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug


def _ensure_unique_slug(slug, existing):
    """Ensure slug doesn't conflict with existing file stems."""
    if slug not in existing:
        return slug
    for i in range(2, 100):
        candidate = f"{slug}-{i}"
        if candidate not in existing:
            return candidate
    return slug


def _read_jira_evidence(jira_keys, jira_index):
    """Read JIRA issue bodies for the given keys."""
    evidence = []
    for key in jira_keys:
        path = jira_index.get(key)
        if not path or not Path(path).exists():
            evidence.append(f"### {key}\n\n(File not found: {path})\n")
            continue
        try:
            fm, body = _parse_manifest(path)
            summary = fm.get("summary", key)
            evidence.append(f"### {key}: {summary}\n\n{body}\n")
        except Exception:
            evidence.append(f"### {key}\n\n(Could not parse file)\n")
    return "\n".join(evidence)


def _read_pr_evidence(pr_refs, pr_index):
    """Read PR summary evidence for the given references."""
    evidence = []
    for ref in pr_refs:
        info = pr_index.get(ref)
        if not info:
            evidence.append(f"### {ref}\n\n(Not found in doccontext)\n")
            continue

        stem = info.get("stem")
        summary_path = Path("artifacts/prcontext") / f"{stem}.md" if stem else None

        parts = [f"### {ref}: {info.get('title', '')}"]
        parts.append(f"**Verdict:** {info.get('verdict', 'unknown')}")
        parts.append(f"**Gist:** {info.get('gist', '')}")
        parts.append(f"**URL:** {info.get('url', '')}")
        parts.append("")

        if summary_path and summary_path.exists():
            try:
                _, sbody = _parse_manifest(summary_path)
                what_changed = _extract_section(sbody, "What changed")
                doc_impact = _extract_section(sbody, "Documentation impact")
                if what_changed:
                    parts.append("**What changed:**")
                    parts.append(what_changed)
                    parts.append("")
                if doc_impact:
                    parts.append("**Documentation impact:**")
                    parts.append(doc_impact)
                    parts.append("")
            except Exception:
                parts.append("(Could not parse summary file)")
                parts.append("")

        evidence.append("\n".join(parts))
    return "\n".join(evidence)


def _compute_evidence_confidence(jira_keys, pr_refs, pr_index):
    """Rate evidence quality: strong, moderate, weak."""
    relevant_count = sum(
        1 for ref in pr_refs
        if pr_index.get(ref, {}).get("verdict") == "relevant"
    )
    if relevant_count >= 3:
        return "strong"
    if relevant_count >= 1:
        return "moderate"
    if jira_keys:
        return "weak"
    return "none"


def _build_xref_map(modules, ext):
    """Build cross-module reference map: slug → anchor-ID."""
    xref_map = {}
    for mod in modules:
        slug = _title_to_slug(mod["title"])
        anchor = re.sub(r"-", "-", slug)
        anchor_id = f"{anchor}_{{context}}"
        xref_map[slug] = {
            "title": mod["title"],
            "anchor_id": anchor_id,
            "file": f"{slug}{ext}",
        }
    return xref_map


def write_module_prompt(module, jira_evidence, pr_evidence, confidence,
                        xref_map, target_path, output_dir):
    """Write a per-module prompt file with spec + evidence."""
    slug = _title_to_slug(module["title"])
    prompt_path = Path(output_dir) / f"{slug}.prompt.md"

    lines = []
    lines.append(f"# Module: {module['title']}")
    lines.append("")
    lines.append(f"**Type:** {module['type']}")
    lines.append(f"**Personas:** {', '.join(module['personas'])}")
    if module.get("journey_phase"):
        lines.append(f"**Journey Phase:** {module['journey_phase']}")
    lines.append(f"**Evidence Confidence:** {confidence}")
    lines.append(f"**Target Path:** `{target_path}`")
    lines.append("")

    if module.get("job_statement"):
        lines.append("## Job Statement")
        lines.append("")
        lines.append(module["job_statement"])
        lines.append("")

    lines.append("## Content Outline")
    lines.append("")
    for item in module.get("content_outline", []):
        lines.append(f"- {item}")
    lines.append("")

    if module.get("prerequisites"):
        lines.append("## Prerequisites")
        lines.append("")
        lines.append(module["prerequisites"])
        lines.append("")

    if module.get("dev_preview_disclaimer") == "required":
        lines.append("## Dev Preview Disclaimer")
        lines.append("")
        lines.append("This module MUST include the Technology Preview / Dev Preview")
        lines.append("disclaimer admonition. Use the exact boilerplate from the")
        lines.append("format reference.")
        lines.append("")

    lines.append("## Cross-Module References")
    lines.append("")
    lines.append("Use these xref targets when linking to sibling modules:")
    lines.append("")
    for xslug, xinfo in xref_map.items():
        if xslug != slug:
            lines.append(f"- `{xinfo['file']}` — {xinfo['title']}")
    lines.append("")

    lines.append("## JIRA Evidence")
    lines.append("")
    if jira_evidence.strip():
        lines.append(jira_evidence)
    else:
        lines.append("(No JIRA evidence referenced for this module)")
    lines.append("")

    lines.append("## PR Evidence")
    lines.append("")
    if pr_evidence.strip():
        lines.append(pr_evidence)
    else:
        lines.append("(No PR evidence referenced for this module)")
    lines.append("")

    prompt_path.write_text("\n".join(lines), encoding="utf-8")
    return str(prompt_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Prepare per-module prompts for documentation writer agents."
    )
    parser.add_argument(
        "--docplan", default=DEFAULT_DOCPLAN,
        help=f"Path to docplan manifest (default: {DEFAULT_DOCPLAN})",
    )
    parser.add_argument(
        "--doccontext", default=DEFAULT_DOCCONTEXT,
        help=f"Path to doccontext manifest (default: {DEFAULT_DOCCONTEXT})",
    )
    parser.add_argument(
        "--target-repo", default=DEFAULT_TARGET_REPO,
        help=f"Path to target documentation repo (default: {DEFAULT_TARGET_REPO})",
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for prompt files (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--draft", action="store_true",
        help="Draft mode: write output to artifacts/docwrite/output/ instead of target repo",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    for path, label in [(args.docplan, "docplan"), (args.doccontext, "doccontext")]:
        if not Path(path).exists():
            log.error("%s not found: %s", label, path)
            sys.exit(2)

    if not Path(args.target_repo).is_dir():
        log.error("Target repo not found: %s", args.target_repo)
        sys.exit(2)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: Parse docplan
    modules = parse_docplan(args.docplan)
    log.info("Parsed %d modules from docplan", len(modules))

    if not modules:
        log.error("No modules found in docplan")
        sys.exit(2)

    # Phase 2: Build evidence indices
    jira_index, pr_index = build_evidence_indices(args.doccontext)
    log.info("Built indices: %d JIRA issues, %d PRs", len(jira_index), len(pr_index))

    # Phase 3: Lightweight repo scan
    needed_types = list({m["type"] for m in modules if m["type"]})
    repo_profile = scan_target_repo(args.target_repo, needed_types)
    log.info(
        "Detected framework: %s, modules_dir: %s, assemblies_dir: %s",
        repo_profile["framework"],
        repo_profile["modules_dir"],
        repo_profile["assemblies_dir"],
    )

    ext = repo_profile["file_extension"]
    existing_stems = set(repo_profile["existing_files"])

    # Phase 4: Build per-module prompts
    xref_map = _build_xref_map(modules, ext)

    module_configs = []
    for mod in modules:
        slug = _title_to_slug(mod["title"])
        slug = _ensure_unique_slug(slug, existing_stems)
        existing_stems.add(slug)

        if args.draft:
            draft_dir = output_dir / "output"
            draft_dir.mkdir(parents=True, exist_ok=True)
            target_path = str(draft_dir / f"{slug}{ext}")
        else:
            modules_dir = repo_profile["modules_dir"] or "modules"
            target_path = str(Path(args.target_repo) / modules_dir / f"{slug}{ext}")

        jira_evidence = _read_jira_evidence(
            mod["source_evidence"]["jira"], jira_index
        )
        pr_evidence = _read_pr_evidence(
            mod["source_evidence"]["prs"], pr_index
        )
        confidence = _compute_evidence_confidence(
            mod["source_evidence"]["jira"],
            mod["source_evidence"]["prs"],
            pr_index,
        )

        prompt_path = write_module_prompt(
            mod, jira_evidence, pr_evidence, confidence,
            xref_map, target_path, str(output_dir),
        )

        module_configs.append({
            "slug": slug,
            "title": mod["title"],
            "type": mod["type"],
            "prompt_file": prompt_path,
            "target_path": target_path,
            "evidence_confidence": confidence,
        })

    # Phase 5: Write config
    config = {
        "target_repo": os.path.abspath(args.target_repo),
        "mode": "draft" if args.draft else "write",
        "repo_profile": repo_profile,
        "modules": module_configs,
    }

    config_path = output_dir / "writer-config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    log.info("Wrote writer config to %s", config_path)
    log.info("Wrote %d module prompt files", len(module_configs))

    summary = {
        "config_path": str(config_path),
        "module_count": len(module_configs),
        "framework": repo_profile["framework"],
        "modules_dir": repo_profile["modules_dir"],
        "assemblies_dir": repo_profile["assemblies_dir"],
        "mode": config["mode"],
        "modules": [
            {"slug": m["slug"], "type": m["type"], "confidence": m["evidence_confidence"]}
            for m in module_configs
        ],
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
