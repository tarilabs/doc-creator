#!/usr/bin/env python3
"""Validate review findings, diff snapshots, and generate consolidated report.

Reads reviewer-config.json and all *-findings.json files produced by
reviewer agents. Validates JSON structure, diffs snapshots against
current files to compute change metrics, aggregates findings, and
writes a human-readable report and machine-readable summary.

Files read (not modified)
    artifacts/docreview/reviewer-config.json — reviewer config
    artifacts/docreview/*.style-findings.json — style review findings
    artifacts/docreview/*.technical-findings.json — technical review findings
    artifacts/docreview/snapshots/*.adoc — pre-review snapshots
    {target_path} files — current (post-review) module files

Files written
    artifacts/docreview/review-report.md — human-readable report
    artifacts/docreview/review-summary.json — machine-readable summary

Usage:
    python3 scripts/doc_review_verify.py
    python3 scripts/doc_review_verify.py --config artifacts/docreview/reviewer-config.json
"""

import argparse
import difflib
import json
import logging
import sys
from collections import Counter
from pathlib import Path

log = logging.getLogger("doc_review_verify")

DEFAULT_CONFIG = "artifacts/docreview/reviewer-config.json"

REQUIRED_FINDING_FIELDS = {"severity", "category", "description"}
VALID_SEVERITIES = {"critical", "major", "minor", "info"}
VALID_ACTIONS = {"fixed", "reported", "skipped"}
VALID_VERDICTS = {"pass", "pass_with_warnings", "needs_revision", "fail"}


# ---------------------------------------------------------------------------
# Phase 1-2: Load and validate findings
# ---------------------------------------------------------------------------

def _validate_finding(finding, idx):
    """Validate a single finding dict. Returns list of issues."""
    issues = []
    for field in REQUIRED_FINDING_FIELDS:
        if field not in finding:
            issues.append(f"Finding [{idx}]: missing required field '{field}'")

    severity = finding.get("severity", "")
    if severity and severity not in VALID_SEVERITIES:
        issues.append(f"Finding [{idx}]: invalid severity '{severity}'")

    action = finding.get("action", "")
    if action and action not in VALID_ACTIONS:
        issues.append(f"Finding [{idx}]: invalid action '{action}'")

    return issues


def load_findings(findings_path):
    """Load and validate a findings JSON file."""
    if not findings_path.exists():
        return None, [f"Findings file not found: {findings_path}"]

    try:
        with open(findings_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON in {findings_path}: {e}"]

    issues = []

    for field in ("module", "review_type", "findings"):
        if field not in data:
            issues.append(f"Missing required field '{field}' in {findings_path.name}")

    if not isinstance(data.get("findings", []), list):
        issues.append(f"'findings' must be a list in {findings_path.name}")
    else:
        for i, finding in enumerate(data["findings"]):
            issues.extend(_validate_finding(finding, i))

    verdict = data.get("verdict", "")
    if verdict and verdict not in VALID_VERDICTS:
        issues.append(f"Invalid verdict '{verdict}' in {findings_path.name}")

    return data, issues


# ---------------------------------------------------------------------------
# Phase 3: Diff snapshots
# ---------------------------------------------------------------------------

def compute_diff_metrics(snapshot_path, current_path):
    """Compute diff between snapshot and current file."""
    if not snapshot_path.exists() or not current_path.exists():
        return {"diffable": False, "reason": "file(s) missing"}

    snapshot_lines = snapshot_path.read_text(encoding="utf-8").splitlines(keepends=True)
    current_lines = current_path.read_text(encoding="utf-8").splitlines(keepends=True)

    diff = list(difflib.unified_diff(snapshot_lines, current_lines, n=0))

    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    return {
        "diffable": True,
        "lines_added": added,
        "lines_removed": removed,
        "total_changes": added + removed,
        "unchanged": added == 0 and removed == 0,
    }


# ---------------------------------------------------------------------------
# Phase 4-5: Aggregate and cross-module checks
# ---------------------------------------------------------------------------

def aggregate_findings(all_findings):
    """Aggregate findings across modules by severity, category, action."""
    severity_counts = Counter()
    category_counts = Counter()
    action_counts = Counter()

    for findings_data in all_findings:
        for finding in findings_data.get("findings", []):
            severity_counts[finding.get("severity", "unknown")] += 1
            category_counts[finding.get("category", "unknown")] += 1
            action_counts[finding.get("action", "unknown")] += 1

    return {
        "by_severity": dict(severity_counts),
        "by_category": dict(category_counts),
        "by_action": dict(action_counts),
        "total_findings": sum(severity_counts.values()),
    }


def compute_verdict(findings_list):
    """Compute verdict from a list of findings, considering only reported/skipped."""
    reported_severities = set()
    for finding in findings_list:
        action = finding.get("action", "reported")
        if action in ("reported", "skipped"):
            reported_severities.add(finding.get("severity", "info"))

    if "critical" in reported_severities:
        return "fail"
    if "major" in reported_severities:
        return "needs_revision"
    if "minor" in reported_severities:
        return "pass_with_warnings"
    return "pass"


def cross_module_checks(modules_data):
    """Run cross-module consistency checks. Returns list of findings."""
    issues = []

    xref_targets = set()
    for mod_data in modules_data:
        slug = mod_data.get("slug", "")
        if slug:
            xref_targets.add(slug)

    for mod_data in modules_data:
        target_path = mod_data.get("target_path", "")
        if not target_path or not Path(target_path).exists():
            continue
        content = Path(target_path).read_text(encoding="utf-8")

        for match in __import__("re").finditer(r'xref:([^\[]+)\[', content):
            ref = match.group(1)
            ref_stem = Path(ref).stem
            if ref_stem and ref_stem not in xref_targets:
                issues.append({
                    "module": mod_data.get("slug", "unknown"),
                    "type": "cross_reference",
                    "description": f"xref target '{ref}' not found among written modules",
                    "severity": "minor",
                })

    return issues


# ---------------------------------------------------------------------------
# Phase 6-7: Generate report and summary
# ---------------------------------------------------------------------------

def write_report(report_path, module_results, aggregation, cross_issues, diff_metrics):
    """Write human-readable review report."""
    lines = ["# Documentation Review Report\n"]

    # Summary table
    agg = aggregation
    lines.append("## Summary\n")
    lines.append(f"- **Total findings:** {agg['total_findings']}")
    lines.append(f"- **Fixed:** {agg['by_action'].get('fixed', 0)}")
    lines.append(f"- **Reported:** {agg['by_action'].get('reported', 0)}")
    lines.append(f"- **Skipped:** {agg['by_action'].get('skipped', 0)}")
    lines.append("")

    if agg["by_severity"]:
        lines.append("### By severity\n")
        for sev in ["critical", "major", "minor", "info"]:
            count = agg["by_severity"].get(sev, 0)
            if count:
                lines.append(f"- **{sev}:** {count}")
        lines.append("")

    # Per-module sections
    lines.append("## Per-Module Results\n")
    for mod_result in module_results:
        slug = mod_result["slug"]
        verdict = mod_result["verdict"]
        dm = diff_metrics.get(slug, {})

        lines.append(f"### {slug}\n")
        lines.append(f"- **Verdict:** {verdict}")

        if dm.get("diffable"):
            lines.append(f"- **Lines added:** {dm.get('lines_added', 0)}")
            lines.append(f"- **Lines removed:** {dm.get('lines_removed', 0)}")

        style_findings = mod_result.get("style_findings", [])
        tech_findings = mod_result.get("technical_findings", [])

        if style_findings:
            lines.append("\n**Style findings:**\n")
            for f in style_findings:
                action_tag = f"[{f.get('action', '?').upper()}]"
                lines.append(f"- {action_tag} ({f.get('severity', '?')}) {f.get('description', '')}")

        if tech_findings:
            lines.append("\n**Technical findings:**\n")
            for f in tech_findings:
                action_tag = f"[{f.get('action', '?').upper()}]"
                lines.append(f"- {action_tag} ({f.get('severity', '?')}) {f.get('description', '')}")

        if not style_findings and not tech_findings:
            lines.append("- No findings")

        lines.append("")

    # Cross-module issues
    if cross_issues:
        lines.append("## Cross-Module Issues\n")
        for issue in cross_issues:
            lines.append(f"- ({issue['severity']}) [{issue['module']}] {issue['description']}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Validate review findings and generate consolidated report."
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG,
        help=f"Path to reviewer-config.json (default: {DEFAULT_CONFIG})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(args.config)
    if not config_path.exists():
        log.error("Reviewer config not found: %s", args.config)
        sys.exit(2)

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    modules = config.get("modules", [])
    snapshot_dir = Path(config.get("snapshot_dir", "artifacts/docreview/snapshots"))
    output_dir = config_path.parent

    if not modules:
        log.error("No modules in reviewer config")
        sys.exit(2)

    # Phase 1-2: Load and validate all findings
    all_findings = []
    validation_errors = []
    module_results = []
    diff_metrics = {}

    for mod in modules:
        slug = mod["slug"]
        target_path = Path(mod["target_path"])

        style_path = output_dir / f"{slug}.style-findings.json"
        tech_path = output_dir / f"{slug}.technical-findings.json"

        style_data, style_issues = load_findings(style_path)
        tech_data, tech_issues = load_findings(tech_path)

        if style_issues:
            validation_errors.extend(style_issues)
        if tech_issues:
            validation_errors.extend(tech_issues)

        combined_findings = []
        style_findings_list = []
        tech_findings_list = []

        if style_data:
            all_findings.append(style_data)
            style_findings_list = style_data.get("findings", [])
            combined_findings.extend(style_findings_list)

        if tech_data:
            all_findings.append(tech_data)
            tech_findings_list = tech_data.get("findings", [])
            combined_findings.extend(tech_findings_list)

        # Phase 3: Diff
        snapshot_name = target_path.name
        snapshot_path = snapshot_dir / snapshot_name
        dm = compute_diff_metrics(snapshot_path, target_path)
        diff_metrics[slug] = dm

        verdict = compute_verdict(combined_findings)

        module_results.append({
            "slug": slug,
            "target_path": str(target_path),
            "verdict": verdict,
            "style_findings": style_findings_list,
            "technical_findings": tech_findings_list,
            "diff": dm,
            "style_present": style_data is not None,
            "technical_present": tech_data is not None,
        })

        status = "PASS" if verdict == "pass" else verdict.upper()
        fixed_count = sum(1 for f in combined_findings if f.get("action") == "fixed")
        reported_count = sum(1 for f in combined_findings if f.get("action") == "reported")
        log.info(
            "%s %s — %d fixed, %d reported, diff: %s",
            status, slug, fixed_count, reported_count,
            f"+{dm.get('lines_added', '?')}/-{dm.get('lines_removed', '?')}" if dm.get("diffable") else "N/A",
        )

    # Phase 4-5: Aggregate and cross-module checks
    aggregation = aggregate_findings(all_findings)
    cross_issues = cross_module_checks(modules)

    # Phase 6: Write report
    report_path = output_dir / "review-report.md"
    write_report(report_path, module_results, aggregation, cross_issues, diff_metrics)
    log.info("Wrote review report to %s", report_path)

    # Phase 7: Write summary JSON
    summary = {
        "total_modules": len(modules),
        "modules_reviewed": sum(1 for m in module_results if m["style_present"] or m["technical_present"]),
        "modules_with_both": sum(1 for m in module_results if m["style_present"] and m["technical_present"]),
        "aggregation": aggregation,
        "verdicts": {m["slug"]: m["verdict"] for m in module_results},
        "diff_metrics": diff_metrics,
        "cross_module_issues": len(cross_issues),
        "validation_errors": validation_errors,
        "module_results": [
            {
                "slug": m["slug"],
                "verdict": m["verdict"],
                "style_count": len(m["style_findings"]),
                "technical_count": len(m["technical_findings"]),
                "fixed": sum(1 for f in m["style_findings"] + m["technical_findings"] if f.get("action") == "fixed"),
                "reported": sum(1 for f in m["style_findings"] + m["technical_findings"] if f.get("action") == "reported"),
                "diff": m["diff"],
            }
            for m in module_results
        ],
    }

    summary_path = output_dir / "review-summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    log.info("Wrote review summary to %s", summary_path)
    print(json.dumps(summary, indent=2))

    # Exit code based on verdicts
    all_verdicts = [m["verdict"] for m in module_results]
    if "fail" in all_verdicts:
        sys.exit(2)
    elif "needs_revision" in all_verdicts or validation_errors:
        sys.exit(2)
    elif "pass_with_warnings" in all_verdicts:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
