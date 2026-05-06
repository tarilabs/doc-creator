---
name: docreview
description: >
  Review written documentation for style compliance and technical accuracy.
  Style reviewers fix issues in place (gerunds→imperative, passive→active,
  formatting). Technical reviewers verify claims against source code and
  fix what they can verify. Use after docwrite has produced written modules
  in the target documentation repository.
disable-model-invocation: true
allowed-tools: Bash(python3 *) Read Agent Write
---

## Step 1 — Prepare

Run the preparation script to parse the writer config, snapshot written
modules, consolidate style guidelines, and produce review prompt files:

```bash
python3 scripts/doc_review_prepare.py
```

If the user provided `--config`, `--doccontext`, or `--output-dir`
arguments via `$ARGUMENTS`, pass them through.

Verify exit 0 or 1 before continuing. Exit 2 = fatal (report and stop).
Exit 1 = warnings (proceed but note them).

Parse the JSON output (last line of stdout) to get:
- `config_path` — path to reviewer-config.json
- `module_count` — number of modules to review
- `has_guidelines` — whether style guidelines were found
- `has_format_reference` — whether format reference exists
- `warnings` — any preparation warnings
- `modules` — list with slug, type, confidence, codecontext_count

## Step 2a — Dispatch style reviewer agents

Read `artifacts/docreview/reviewer-config.json` to get the list of modules.

For each module, read [style-reviewer-prompt.md](style-reviewer-prompt.md)
and fill:
- `{module_path}` — absolute path to the written module file (target_path)
- `{rubric_path}` — absolute path to `artifacts/docreview/style-rubric.md`
- `{format_reference_path}` — absolute path to `artifacts/docwrite/format-reference.md`
- `{output_path}` — absolute path to `artifacts/docreview/{slug}.style-findings.json`

Spawn agents with **model: sonnet**. **Launch ALL agents in a SINGLE
message.** Send one message containing one Agent tool call per module.
Do NOT wait for any agent to complete before launching others.

**Do NOT** read the style rubric, format reference, or module files.
**Do NOT** reason about documentation content or style rules.
Your job is mechanical: construct redirect prompts and dispatch.

**Wait for ALL style reviewer agents to complete before Step 2b.**

## Step 2b — Dispatch technical reviewer agents

Read `artifacts/docreview/reviewer-config.json` again to get the modules.

For each module, read [technical-reviewer-prompt.md](technical-reviewer-prompt.md)
and fill:
- `{module_path}` — absolute path to the written module file (target_path)
- `{evidence_prompt_path}` — absolute path to the module's `.prompt.md` file
  from the writer config (`prompt_file` field in reviewer-config)
- `{codecontext_paths_block}` — formatted list of codecontext directories.
  For each directory in the module's `codecontext_dirs` array, write:
  `   - \`{path}\``
  If no codecontext dirs, write: `   (No codecontext mapped for this module)`
- `{evidence_confidence}` — the module's evidence_confidence value
- `{output_path}` — absolute path to `artifacts/docreview/{slug}.technical-findings.json`

Spawn agents with **model: opus**. **Launch ALL agents in a SINGLE
message.** Send one message containing one Agent tool call per module.
Do NOT wait for any agent to complete before launching others.

**Do NOT** read evidence files, codecontext files, or module files.
**Do NOT** reason about technical claims, source code, or evidence.
Your job is mechanical: construct redirect prompts and dispatch.

## Step 3 — Verify

Run the verification script:

```bash
python3 scripts/doc_review_verify.py
```

- Exit 0 = all modules pass (no reported critical/major findings)
- Exit 1 = warnings only (minor reported findings)
- Exit 2 = critical/major reported findings exist

Parse the JSON output to get per-module results, diff metrics,
aggregated findings, and verdicts.

## Step 4 — Report

Report to the user:

- **Modules reviewed**: count and list
- **Changes applied**: total edits made by style and technical reviewers
- **Diff summary**: lines added/removed per module (from snapshot comparison)
- **Reported findings**: issues that need human/SME attention, grouped by
  severity (critical first, then major, minor, info)
- **Per-module verdicts**: pass / pass_with_warnings / needs_revision / fail
- **Cross-module issues**: any terminology inconsistencies or broken xrefs
- **Next steps**: suggest reviewing the diff (`diff artifacts/docreview/snapshots/
  {slug}.adoc {target_path}`) for modules with many changes, and addressing
  reported findings manually
