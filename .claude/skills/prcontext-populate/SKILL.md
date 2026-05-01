---
name: prcontext-populate
description: >
  Fetch PR patches from jiracontext links, filter noise, and produce
  concise documentation-relevant summaries. Use after
  jiracontext-populate has run and artifacts/jiracontext.md contains
  a pull_requests list.
disable-model-invocation: true
compatibility: Requires Python 3.11+ with pyyaml, and gh CLI authenticated. Designed for Claude Code.
allowed-tools: Bash(python3 *) Read Agent Write
---

## Step 1 — Fetch PR patches

Run the fetch script to download patches and metadata via `gh`:

```bash
python3 scripts/pr_context_fetch.py
```

If the user provided `--manifest` or `--output-dir` arguments via
`$ARGUMENTS`, pass them through.

Exit 0 = all fetched, exit 1 = some failed (continue), exit 2 = fatal.
Stop on exit 2 only.

## Step 2 — Filter noise

Run the filter script to strip lock files, generated code, CI configs,
images, and whitespace-only changes:

```bash
python3 scripts/pr_context_filter.py
```

Verify exit 0 before continuing.

## Step 3 — Pre-classify

Run the pre-classifier to add deterministic hints to the manifest:

```bash
python3 scripts/pr_context_preclassify.py
```

This adds `hint`, `hint_reason`, and `hint_text` fields to each
manifest entry based on title patterns and file-level analysis.
`hint_text` is the fully expanded text the subagent consumes directly.
Exit 0 = success.

## Artifact layout

The fetch, filter, and pre-classify scripts produce this directory
structure (under `artifacts/prcontext/` by default, or the custom
output directory):

```
artifacts/
  prcontext.md               # manifest with YAML frontmatter
  prcontext/
    raw/{file}.patch           # original patches from gh
    raw/{file}.meta.yaml       # PR metadata (title, body, labels, etc.)
    filtered/{file}.patch      # noise-filtered patches
    {file}.md                  # output summaries (written in Step 5)
    verdict_check.md           # post-hoc sanity check (written in Step 6)
```

`{file}` matches the `file` field in each manifest entry
(e.g. `kubeflow__model-registry__2367`).

## Step 4 — Prepare batch prompts

Run the prepare script to group PRs into batches and build prompt
files:

```bash
python3 scripts/pr_context_prepare.py
```

The script writes noise summaries for empty-patch entries, groups
remaining PRs into ≤5 batches, fills the prompt template, and
writes one prompt file per batch to `artifacts/prcontext/`.

It prints a JSON summary to stdout:
```json
{"batches": ["artifacts/prcontext/batch_0.prompt.md", ...], "noise_written": 2}
```

Parse the JSON to get the list of batch prompt file paths.
Exit 0 = success, exit 2 = fatal.

## Step 5 — Summarize PR batches

For each batch prompt file from Step 4, spawn an Agent subagent
with **model: haiku**. The prompt is the file's content — read the
file and use it as the Agent prompt directly. No template filling
needed.

**CRITICAL — launch ALL batch subagents in a SINGLE message.** Send
one message containing one Agent tool call per batch. Do NOT wait for
any subagent to complete before launching others.

**DO NOT** reason about PR content, titles, or verdicts. Verdict
judgment is the subagent's job. Your job is mechanical: read the
batch prompt files and dispatch agents.

## Step 6 — Verdict sanity check

Run the post-hoc verdict check:

```bash
python3 scripts/pr_context_verdict_check.py
```

Exit 0 = clean, exit 1 = flags raised (advisory, do NOT stop),
exit 2 = fatal.

Read `artifacts/prcontext/verdict_check.md` if exit 1 to understand
the flags. Report them in Step 7.

## Step 7 — Report

Run the report generator:

```bash
python3 scripts/pr_context_report.py
```

Read `artifacts/prcontext.md` and relay the report body to the user.
The report body (below the YAML frontmatter) contains the summary
table with verdict, hint, and gist for each PR, plus any flags from
the verdict check.
