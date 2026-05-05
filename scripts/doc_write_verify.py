#!/usr/bin/env python3
"""Validate written documentation files against discovered repo conventions.

Reads writer-config.json to determine which files to check and what
conventions were detected. Checks are driven by the repo_profile,
not hardcoded for any specific repository.

Files read (not modified)
    artifacts/docwrite/writer-config.json — writer config with repo_profile
    {target_path} files — written documentation files to validate

Usage:
    python3 scripts/doc_write_verify.py
    python3 scripts/doc_write_verify.py --config artifacts/docwrite/writer-config.json
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("doc_write_verify")

DEFAULT_CONFIG = "artifacts/docwrite/writer-config.json"


def _check_file_exists(target_path):
    """Check that the output file exists."""
    if Path(target_path).exists():
        return []
    return [{"severity": "error", "check": "file_exists", "message": f"File not found: {target_path}"}]


def _check_non_empty(target_path):
    """Check that the file is non-empty."""
    p = Path(target_path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8")
    if len(content.strip()) < 10:
        return [{"severity": "error", "check": "non_empty", "message": "File is empty or nearly empty"}]
    return []


def _check_no_placeholders(target_path):
    """Check for leftover placeholder text."""
    p = Path(target_path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8")
    issues = []
    patterns = [
        (r"\[REPLACE:", "Contains [REPLACE:] placeholder"),
        (r"\[TODO\]", "Contains [TODO] placeholder"),
        (r"\[INSERT\]", "Contains [INSERT] placeholder"),
        (r"\[INSERT ", "Contains [INSERT ...] placeholder"),
    ]
    for pattern, msg in patterns:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append({"severity": "error", "check": "no_placeholders", "message": msg})
    return issues


def _check_asciidoc_content_type(target_path, expected_type):
    """Check :_mod-docs-content-type: attribute (AsciiDoc)."""
    p = Path(target_path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8")

    pattern = re.compile(r":_mod-docs-content-type:\s*(\w+)", re.IGNORECASE)
    alt_pattern = re.compile(r":_module-type:\s*(\w+)", re.IGNORECASE)

    match = pattern.search(content) or alt_pattern.search(content)
    if not match:
        return [{"severity": "error", "check": "content_type_attr",
                 "message": "Missing :_mod-docs-content-type: or :_module-type: attribute"}]

    found_type = match.group(1).upper()
    expected_upper = expected_type.upper()
    if found_type != expected_upper:
        return [{"severity": "warning", "check": "content_type_attr",
                 "message": f"Content type mismatch: found {found_type}, expected {expected_upper}"}]
    return []


def _check_asciidoc_anchor(target_path):
    """Check for [id="..._{context}"] anchor (AsciiDoc)."""
    p = Path(target_path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8")
    if re.search(r'\[id="[^"]+_\{context\}"\]', content):
        return []
    return [{"severity": "warning", "check": "anchor_id",
             "message": "Missing [id=\"..._{context}\"] anchor"}]


def _check_asciidoc_abstract(target_path):
    """Check for [role="_abstract"] (AsciiDoc)."""
    p = Path(target_path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8")
    if '[role="_abstract"]' in content:
        return []
    return [{"severity": "warning", "check": "abstract_role",
             "message": 'Missing [role="_abstract"] tag'}]


def _check_asciidoc_heading_depth(target_path):
    """Check heading depth doesn't exceed 2 levels (AsciiDoc: = and ==)."""
    p = Path(target_path)
    if not p.exists():
        return []
    issues = []
    for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
        if re.match(r"^={3,}\s", line):
            issues.append({"severity": "warning", "check": "heading_depth",
                           "message": f"Line {i}: heading deeper than 2 levels (===+)"})
    return issues


def _check_asciidoc_procedure_sections(target_path, expected_type):
    """Check PROCEDURE modules have .Prerequisites, .Procedure, .Verification."""
    if expected_type.lower() != "procedure":
        return []
    p = Path(target_path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8")
    issues = []
    for section in [".Prerequisites", ".Procedure"]:
        if section not in content:
            issues.append({"severity": "warning", "check": "procedure_sections",
                           "message": f"Missing {section} section in procedure module"})
    if ".Verification" not in content:
        issues.append({"severity": "warning", "check": "procedure_sections",
                       "message": "Missing .Verification section (recommended for procedures)"})
    return issues


def _check_hardcoded_product_names(target_path, product_attrs_file):
    """Warn if hardcoded product names appear instead of attributes."""
    if not product_attrs_file:
        return []
    p = Path(target_path)
    if not p.exists():
        return []

    attrs_path = Path(product_attrs_file)
    if not attrs_path.exists():
        return []

    attr_values = set()
    try:
        for line in attrs_path.read_text(encoding="utf-8").split("\n"):
            m = re.match(r"^:(\w[\w-]*):\s+(.+)$", line)
            if m:
                val = m.group(2).strip()
                if len(val) > 3 and not val.startswith("{") and not val.startswith("pass:"):
                    attr_values.add(val)
    except Exception:
        return []

    if not attr_values:
        return []

    content = p.read_text(encoding="utf-8")
    issues = []
    for val in sorted(attr_values):
        if val in content and len(val) > 8:
            issues.append({"severity": "warning", "check": "hardcoded_product_name",
                           "message": f"Hardcoded product name found: '{val}' — consider using an attribute"})
    return issues


def _check_mkdocs_frontmatter(target_path):
    """Check MkDocs files have YAML frontmatter."""
    p = Path(target_path)
    if not p.exists():
        return []
    content = p.read_text(encoding="utf-8")
    if content.startswith("---\n"):
        return []
    return [{"severity": "warning", "check": "mkdocs_frontmatter",
             "message": "Missing YAML frontmatter"}]


def verify_module(module_config, repo_profile):
    """Run all applicable checks on a single module."""
    target_path = module_config["target_path"]
    expected_type = module_config.get("type", "")
    framework = repo_profile.get("framework", "unknown")
    product_attrs_file = repo_profile.get("product_attributes_file")

    all_issues = []
    checks_performed = []
    checks_skipped = []

    # Universal checks
    for check_fn, name in [
        (_check_file_exists, "file_exists"),
        (_check_non_empty, "non_empty"),
        (_check_no_placeholders, "no_placeholders"),
    ]:
        checks_performed.append(name)
        all_issues.extend(check_fn(target_path))

    # Framework-specific checks
    if framework == "asciidoc":
        for check_fn, name, args in [
            (_check_asciidoc_content_type, "content_type_attr", (target_path, expected_type)),
            (_check_asciidoc_anchor, "anchor_id", (target_path,)),
            (_check_asciidoc_abstract, "abstract_role", (target_path,)),
            (_check_asciidoc_heading_depth, "heading_depth", (target_path,)),
            (_check_asciidoc_procedure_sections, "procedure_sections", (target_path, expected_type)),
        ]:
            checks_performed.append(name)
            all_issues.extend(check_fn(*args))
    else:
        for name in ["content_type_attr", "anchor_id", "abstract_role", "heading_depth", "procedure_sections"]:
            checks_skipped.append({"check": name, "reason": f"not applicable for {framework} framework"})

    if framework == "mkdocs":
        checks_performed.append("mkdocs_frontmatter")
        all_issues.extend(_check_mkdocs_frontmatter(target_path))

    # Product name checks
    if product_attrs_file:
        checks_performed.append("hardcoded_product_name")
        all_issues.extend(_check_hardcoded_product_names(target_path, product_attrs_file))
    else:
        checks_skipped.append({"check": "hardcoded_product_name",
                               "reason": "no product attributes file detected"})

    errors = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]

    return {
        "slug": module_config["slug"],
        "target_path": target_path,
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "checks_performed": checks_performed,
        "checks_skipped": checks_skipped,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate written documentation files against repo conventions."
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG,
        help=f"Path to writer-config.json (default: {DEFAULT_CONFIG})",
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

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    repo_profile = config.get("repo_profile", {})
    modules = config.get("modules", [])

    if not modules:
        log.error("No modules in writer config")
        sys.exit(2)

    results = []
    for mod in modules:
        result = verify_module(mod, repo_profile)
        results.append(result)

        status = "PASS" if result["passed"] else "FAIL"
        warn_count = len(result["warnings"])
        err_count = len(result["errors"])
        log.info(
            "%s %s — %d errors, %d warnings",
            status, result["slug"], err_count, warn_count,
        )

    total_errors = sum(len(r["errors"]) for r in results)
    total_warnings = sum(len(r["warnings"]) for r in results)
    all_passed = all(r["passed"] for r in results)

    summary = {
        "total_modules": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "modules": results,
    }

    print(json.dumps(summary, indent=2))

    if total_errors > 0:
        sys.exit(2)
    elif total_warnings > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
