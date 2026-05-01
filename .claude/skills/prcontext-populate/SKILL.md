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

## Artifact layout

The fetch and filter scripts produce this directory structure
(under `artifacts/prcontext/` by default, or the custom output directory):

```
artifacts/prcontext/
  prcontext.md              # manifest with YAML frontmatter
  raw/{file}.patch           # original patches from gh
  raw/{file}.meta.yaml       # PR metadata (title, body, labels, etc.)
  filtered/{file}.patch      # noise-filtered patches
  {file}.md                  # output summaries (written in Step 4)
```

`{file}` matches the `file` field in each manifest entry
(e.g. `kubeflow__model-registry__2367`).

## Step 3 — Prepare subagent context

Read the manifest at `artifacts/prcontext/prcontext.md` (or the custom
output directory).

Read the **documentation target** body from `artifacts/jiracontext.md`
(everything after the closing `---`).

From the prcontext manifest, collect all entries where `status: fetched`.

## Step 4 — Summarize each PR

For each fetched PR, spawn an Agent subagent with **model: haiku**.

Read the prompt template from [prompt-template.md](prompt-template.md)
and fill in these placeholders:

- `{documentation_target}` — the full body from jiracontext.md
- `{pr_title}` — from the manifest entry's title field
- `{pr_body}` — from `raw/{file}.meta.yaml`, body field
- `{filtered_patch}` — contents of `filtered/{file}.patch`
- `{pr_url}` — the PR URL
- `{repo}` — owner/repo derived from URL
- `{pr_number}` — PR number
- `{output_file}` — `artifacts/prcontext/{file}.md`

If the filtered patch file is empty (0 bytes), skip the subagent.
Instead, directly write a summary with `verdict: noise` and a one-line
explanation ("all changes were filtered as noise").

Spawn subagents in parallel where possible (batch independent PRs).

## Step 5 — Report

Read back the prcontext manifest. Count verdicts across the summary
files (relevant / peripheral / noise). Report a summary table to the
user:

```
| PR | Repo | Verdict | Gist |
|---|---|---|---|
```

Include total counts at the bottom, and write the report to the `prcontext.md` file body.
