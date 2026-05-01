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

This adds `hint` and `hint_reason` fields to each manifest entry based
on title patterns and file-level analysis. Exit 0 = success.

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

## Step 4 — Prepare subagent context

Read the manifest at `artifacts/prcontext.md` (or the custom
output directory).

Resolve the following **absolute paths** (needed for subagent prompts):
- `{documentation_target_file}` — absolute path to `artifacts/jiracontext.md`
- `{output_dir}` — absolute path to `artifacts/prcontext`

From the prcontext manifest YAML frontmatter, collect all entries where
`status: fetched`. For each entry, note only: `file`, `title`, `url`,
`hint`, `hint_reason`. These are the only fields you need.

Check which filtered patches are empty (0 bytes). For those, directly
write a summary with `verdict: noise` and a one-line explanation
("all changes were filtered as noise"). Remove them from the list
of entries to summarize.

**DO NOT** read `meta.yaml` files, filtered patches, or PR bodies.
The subagents read those files themselves.

## Step 5 — Summarize each PR

For each fetched PR, spawn an Agent subagent with **model: haiku**.

Read the prompt template from [prompt-template.md](prompt-template.md)
and fill in these placeholders:

- `{documentation_target_file}` — the absolute path to jiracontext.md
- `{meta_yaml_path}` — `{output_dir}/raw/{file}.meta.yaml`
- `{filtered_patch_path}` — `{output_dir}/filtered/{file}.patch`
- `{pr_title}` — from the manifest entry's `title` field
- `{pr_url}` — the PR URL
- `{repo}` — owner/repo derived from URL
- `{pr_number}` — PR number
- `{output_file}` — `{output_dir}/{file}.md`
- `{hint_block}` — constructed from the entry's `hint` and `hint_reason`:
  - If `hint` is `no-hint` or absent: empty string
  - If `hint` is `candidate-peripheral`: `"\nDETERMINISTIC HINT: This PR's metadata suggests it is peripheral (reason: {hint_reason}). Evaluate this critically — override if the PR genuinely changes documented behavior.\n"`
  - If `hint` is `candidate-noise`: `"\nDETERMINISTIC HINT: This PR's metadata suggests it is noise (reason: {hint_reason}). Evaluate this critically.\n"`

**CRITICAL — launch ALL subagents in a SINGLE message.** Every PR
is independent. Send one message containing N Agent tool calls, one
per PR. Do NOT wait for any subagent to complete before launching
others. Do NOT launch them in sequential batches.

**DO NOT** reason about PR content, titles, or verdicts. Verdict
judgment is the subagent's job. Your job is mechanical: read the
manifest fields, fill in the template, launch all subagents at once.

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
