#!/usr/bin/env python3
"""Verify the structure and completeness of a documentation plan.

Reads artifacts/docplan/docplan.md and checks:
- YAML frontmatter is valid and contains required fields
- No [REPLACE:] placeholder markers remain
- Required sections exist
- Module count matches frontmatter declaration
- Dev Preview flag is set
- Personas are defined

Exit codes:
    0 = clean (no errors, no warnings)
    1 = warnings only (advisory)
    2 = errors found (structural problems)

Usage:
    python3 scripts/doc_plan_verify.py
    python3 scripts/doc_plan_verify.py --plan artifacts/docplan/docplan.md
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import yaml

log = logging.getLogger("doc_plan_verify")

DEFAULT_PLAN = "artifacts/docplan/docplan.md"

REQUIRED_FM_FIELDS = [
    "starting_issue",
    "created_at",
    "feature_name",
    "personas",
    "module_count",
]

REQUIRED_SECTIONS = [
    "Executive Summary",
    "Personas",
    "User Journey",
    "Planned Modules",
    "Deferred Topics",
    "Unverified Topics",
]


def _parse_manifest(manifest_path):
    """Read a manifest markdown file and return (frontmatter_dict, body_str)."""
    with open(manifest_path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---\n"):
        raise ValueError(f"{manifest_path} has no YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    return yaml.safe_load(fm_raw), body.strip()


def _check_placeholders(text):
    """Find remaining [REPLACE:...] markers."""
    return re.findall(r"\[REPLACE:[^\]]*\]", text)


def _count_modules(body):
    """Count ### Module: headings."""
    return len(re.findall(r"^###\s+Module:", body, re.MULTILINE))


def _find_sections(body):
    """Find all ## level headings."""
    return re.findall(r"^##\s+(.+)$", body, re.MULTILINE)


def _check_ac_table(body):
    """Check the Acceptance Criteria Coverage table for uncovered items."""
    uncovered = []
    in_table = False
    for line in body.split("\n"):
        if "Acceptance Criterion" in line and "|" in line:
            in_table = True
            continue
        if in_table and line.startswith("|"):
            if "uncovered" in line.lower():
                cells = [c.strip() for c in line.split("|")]
                if len(cells) >= 3:
                    uncovered.append(cells[2])
        elif in_table and not line.strip().startswith("|"):
            in_table = False
    return uncovered


def main():
    parser = argparse.ArgumentParser(
        description="Verify documentation plan structure."
    )
    parser.add_argument(
        "--plan", default=DEFAULT_PLAN,
        help=f"Path to plan file (default: {DEFAULT_PLAN})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not Path(args.plan).exists():
        log.error("Plan file not found: %s", args.plan)
        sys.exit(2)

    errors = []
    warnings = []

    # Parse
    try:
        fm, body = _parse_manifest(args.plan)
    except ValueError as e:
        log.error("Cannot parse plan: %s", e)
        sys.exit(2)

    full_text = yaml.dump(fm, default_flow_style=False) + "\n" + body

    # Required frontmatter fields
    for field in REQUIRED_FM_FIELDS:
        val = fm.get(field)
        if val is None:
            errors.append(f"Missing frontmatter field: {field}")
        elif isinstance(val, str) and "[REPLACE:" in val:
            errors.append(f"Unfilled placeholder in frontmatter: {field}")

    # Placeholder markers in body
    placeholders = _check_placeholders(body)
    if placeholders:
        errors.append(f"Found {len(placeholders)} unfilled [REPLACE:] markers")
        for p in placeholders[:5]:
            errors.append(f"  {p}")
        if len(placeholders) > 5:
            errors.append(f"  ...and {len(placeholders) - 5} more")

    # Required sections
    found_sections = _find_sections(body)
    for section in REQUIRED_SECTIONS:
        if not any(section.lower() in s.lower() for s in found_sections):
            errors.append(f"Missing required section: ## {section}")

    # Module count
    actual_modules = _count_modules(body)
    declared_modules = fm.get("module_count")
    if declared_modules is not None and isinstance(declared_modules, int):
        if actual_modules != declared_modules:
            warnings.append(
                f"Module count mismatch: frontmatter says {declared_modules}, "
                f"body has {actual_modules} '### Module:' headings"
            )
    if actual_modules == 0:
        errors.append("No '### Module:' headings found in plan")

    # Personas
    personas = fm.get("personas", [])
    if not personas:
        errors.append("No personas defined in frontmatter")
    elif len(personas) > 5:
        warnings.append(f"Unusually high persona count: {len(personas)}")

    # Dev Preview flag
    if not fm.get("dev_preview"):
        warnings.append("dev_preview flag not set in frontmatter")

    # Acceptance criteria coverage
    ac_coverage = fm.get("acceptance_criteria_coverage", {})
    if ac_coverage:
        unmapped = ac_coverage.get("unmapped", [])
        if unmapped:
            warnings.append(
                f"Unmapped acceptance criteria: {len(unmapped)} "
                f"({', '.join(str(x) for x in unmapped[:3])}...)"
            )
    uncovered_acs = _check_ac_table(body)
    if uncovered_acs:
        warnings.append(f"Uncovered acceptance criteria in table: {len(uncovered_acs)}")

    # Report
    result = {
        "plan": args.plan,
        "modules": actual_modules,
        "personas": len(personas),
        "errors": len(errors),
        "warnings": len(warnings),
        "error_details": errors,
        "warning_details": warnings,
    }

    print(f"\n{'=' * 60}")
    print(f"Documentation Plan Verification: {args.plan}")
    print(f"{'=' * 60}")
    print(f"Modules planned: {actual_modules}")
    print(f"Personas: {len(personas)}")

    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors:
            print(f"  x {e}")

    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ! {w}")

    if not errors and not warnings:
        print("\nAll checks passed")
    elif not errors:
        print(f"\nNo errors ({len(warnings)} warnings)")

    # JSON on last line for machine parsing
    print(f"\n{json.dumps(result)}")

    if errors:
        sys.exit(2)
    elif warnings:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
