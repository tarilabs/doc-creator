---
name: jira-context-populate
description: >
  Bootstrap a jiracontext directory from jiraexploration artifacts and
  selectively populate it with JIRA issue files that contribute useful
  documentation context. Use after jira_exploration.py has populated
  artifacts/jiraexploration — prepares a lean context set for
  documentation authoring.
disable-model-invocation: true
compatibility: Requires Python 3.11+ with pyyaml. Designed for Claude Code.
allowed-tools: Bash(python3 *) Bash(cp *) Read Agent
---

## Step 1 — Bootstrap jiracontext

Run the bootstrap script to create the output directory, copy the starting
issue file, and write the manifest:

```bash
python3 scripts/jira_context_bootstrap.py
```

If the user provided `--input-dir` or `--output-dir` arguments via `$ARGUMENTS`,
pass them through.

Verify the script succeeded (exit code 0) before continuing.

## Step 2 — Prepare subagent context

Read `artifacts/jiracontext.md` (or the manifest at the output directory's
parent, if a custom `--output-dir` was used).

Extract from the YAML frontmatter:
- `starting_issue` — already copied by the bootstrap script, the subagent
  must skip it
- `output_directory` — where selected files will be copied into

Derive the **input directory** by reading the exploration manifest
(`artifacts/jiraexploration.md`) or from the `--input-dir` argument.
By default this is `artifacts/jiraexploration`.

Extract the **markdown body** (everything after the closing `---`) from
`jiracontext.md`. This is the documentation target the subagent evaluates
files against.

List all `.md` files in the **input** directory (jiraexploration).

## Step 3 — Delegate population to a subagent

Spawn a **single** Agent subagent. Read the prompt template from
[prompt-template.md](prompt-template.md) and fill in these placeholders
with the actual values from Step 2:

- `{the full markdown body from jiracontext.md}` — the full body verbatim
- `{input_directory}` — path to jiraexploration
- `{output_directory}` — path to jiracontext
- `{starting_issue}` — the starting issue key

## Step 4 — Extract links

Run the link extraction script to scan the populated context files and
update the manifest with classified links:

```bash
python3 scripts/jira_context_links.py
```

This adds three deduplicated lists to `jiracontext.md` frontmatter:
`pull_requests`, `code_repositories`, and `additional_links`.

## Step 5 — Report

Relay the subagent's summary table to the user. Include a count of files
copied vs skipped, and the link counts from Step 4.
