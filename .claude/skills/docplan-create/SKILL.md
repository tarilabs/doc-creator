---
name: docplan-create
description: >
  Create a documentation plan from doccontext artifacts. Assembles
  JIRA requirements, PR summaries, and code repo references into a
  planner input, dispatches a planner agent to produce a structured
  documentation plan, and verifies the result. Use after
  doc_context_bootstrap.py has produced artifacts/doccontext.md.
disable-model-invocation: true
compatibility: Requires Python 3.11+ with pyyaml. Designed for Claude Code.
allowed-tools: Bash(python3 *) Read Agent
---

## Step 1 — Prepare planner input

Run the preparation script to assemble a single input document from
all upstream context artifacts:

```bash
python3 scripts/doc_plan_prepare.py
```

If the user provided `--doccontext` or `--output-dir` arguments via
`$ARGUMENTS`, pass them through.

Verify the script succeeded (exit code 0) before continuing.

Parse the JSON output (last line of stdout) to get the `planner_input`
path and evidence counts.

## Step 2 — Resolve file paths

Determine the absolute paths for the three files the planner agent
will read:

- `planner_input` — from Step 1 JSON output (e.g. `artifacts/docplan/planner-input.md`)
- `framework` — `.claude/skills/docplan-create/docplan-framework.md`
- `template` — `.claude/skills/docplan-create/docplan-template.md`

And the output path:

- `output` — `artifacts/docplan/docplan.md`

**Do NOT read any of these files yourself.** The agent reads its own
files. Your job is path resolution only.

## Step 3 — Dispatch planner agent

Read the prompt template from [prompt-template.md](prompt-template.md)
and fill in these placeholders with the absolute paths from Step 2:

- `{planner_input_path}` — absolute path to planner input
- `{framework_path}` — absolute path to framework
- `{template_path}` — absolute path to template
- `{output_path}` — absolute path to output

Spawn a **single** Agent subagent with the filled prompt.

**Do NOT** read the planner input, framework, or template contents.
**Do NOT** reason about JIRA issues, PRs, or feature scope.
The planner agent handles all analytical work.

## Step 4 — Verify plan

Run the verification script:

```bash
python3 scripts/doc_plan_verify.py
```

- Exit 0 = clean (no errors, no warnings)
- Exit 1 = warnings only (advisory, do NOT stop)
- Exit 2 = errors found (report to user)

If exit 2, read the error output and report the issues to the user.

## Step 5 — Report

Report to the user:

- Plan location: `artifacts/docplan/docplan.md`
- Module count and persona count from the verification output
- Any errors or warnings from the verification
- Evidence counts from Step 1 (JIRA issues, relevant PRs, etc.)
