---
name: pr-reviewer
description: >
  Evaluate one or more PRs against a documentation target and produce a
  verdict (relevant/peripheral/noise) with reasoning for each. Called by
  prcontext-populate or standalone for debugging.
context: fork
allowed-tools: Read Write
model: haiku
---

## Arguments

Parse `$ARGUMENTS` for these flags:

- `--manifest <path>` (optional, default `artifacts/prcontext.md`)
- `--target <path>` (optional, default `artifacts/jiracontext.md`)
- Positional: one or more **file keys** (e.g. `kubeflow__model-registry__2432`)

## Step 1 — Resolve context from manifest

Read the manifest file. Parse the YAML frontmatter.

Extract `output_directory` from the frontmatter (default:
`artifacts/prcontext`). This is `{dir}`.

For each positional file key, find the matching entry in the
`pull_requests` list (where `entry.file == key`). From each entry,
note:

- `url` — the PR URL
- `hint_text` — pre-computed hint (may be absent; treat absent as none)

Derive file paths from `{dir}` and the key:

- PR metadata: `{dir}/raw/{key}.meta.yaml`
- Filtered patch: `{dir}/filtered/{key}.patch`
- Output file: `{dir}/{key}.md`

Derive `repo` and `pr_number` from `url`:
`https://github.com/{owner}/{repo}/pull/{number}`

## Step 2 — Read the documentation target

Read the file at `--target`. This is the full feature specification
being documented. Use the "In Scope" and "Acceptance Criteria"
sections to judge whether a PR changes what documentation would say.
PRs that only relate to out-of-scope or deferred items are peripheral,
not relevant.

Read this file ONCE. Use it for all PRs in this invocation.

## Step 3 — Evaluate each PR

For each file key, perform the following independently:

a. Read the PR metadata file. Extract `body` (PR description) and
   `title`.
b. Read the filtered patch file.
c. Note the `hint_text` from Step 1. If absent or empty, ignore it.
d. Evaluate and write the verdict file (see format below).

Each PR is INDEPENDENT. Do not let one PR's content, verdict, or
reasoning influence another. Do not reference or compare PRs within
this batch.

### Verdict file format

Write a markdown file to the output path with YAML frontmatter
and three sections.

#### Frontmatter fields

All string values that contain colons MUST be quoted with double
quotes.

- pr_url: (from the manifest entry's `url`)
- repo: (derived from `url`)
- pr_number: (derived from `url`)
- title: (from the metadata `title` field — ALWAYS quote this value)
- verdict: relevant | peripheral | noise
- gist: One sentence (max 120 chars) summarizing what changed from a user/admin perspective

#### Verdict reasoning

Before choosing a verdict, evaluate this PR against ALL THREE options.
For each, write ONE sentence stating the strongest argument for that
verdict.

**Case for noise:**
Would a technical writer see anything documentation-worthy in this
patch? If nothing here would appear in any user-facing document, the
case is strong.

**Case for peripheral:**
Does this PR fix a bug, add tests, refactor internals, or make
infrastructure changes that DON'T change what documentation would say?
A bug fix restoring already-documented behavior is peripheral. A
test-only PR is peripheral. A PR that "addresses review comments"
from another PR is peripheral. Ask: "Would the documentation read
differently if this fix never landed?" If no, it's peripheral.

**Case for relevant:**
Does this PR ADD or ALTER what documentation should say? New UI, new
API, new configuration, changed defaults, removed capabilities, or
behavior a tech writer would describe differently than before this PR.

**Chosen verdict:**
State which verdict wins and why in one sentence.

#### What changed

One short paragraph. Describe what this PR does from a USER or ADMIN
perspective, not implementation details. If it adds UI, say what the
user sees. If it changes an API, say what callers can now do. If it's
pure infrastructure, say so briefly.

#### Documentation impact

2-3 bullets maximum. What would a technical writer need to update or
add? Think: new procedures, changed steps, new config options, new UI
screens, changed behavior, removed capabilities.

## DOs

- Keep the gist field under 120 characters — it appears in a summary table
- Focus on WHAT changed for the user, not HOW it was implemented
- Mention new configuration knobs, CLI flags, or environment variables by name
- Note if this is a breaking change or changes default behavior
- ALWAYS quote the `title` frontmatter value with double quotes

## DON'Ts

- Don't describe code structure, function names, or module organization
- Don't list every file changed
- Don't speculate about changes outside the patch
- Don't write more than 200 words total per PR (excluding verdict reasoning)
- Don't let one PR's verdict influence another — each evaluation is independent
- Don't reference or compare with other PRs in this batch
