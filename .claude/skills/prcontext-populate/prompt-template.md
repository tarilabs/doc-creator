You are summarizing a pull request for a technical writer. Be BRIEF.

STEP 1: Read the documentation target file at {documentation_target_file}
This is the full feature specification being documented. Use the
"In Scope" and "Acceptance Criteria" sections to judge whether the PR
changes what documentation would say. PRs that only relate to
out-of-scope or deferred items are peripheral, not relevant.

STEP 2: Evaluate the PR described below.

  a. Read the PR metadata file (meta_yaml_path). Extract the `body`
     field — this is the PR description.
  b. Read the filtered patch file (filtered_patch_path). This patch
     has noise already removed — what remains is potentially meaningful.
  c. Note the hint_block if present. If it says "(none)", ignore it.
  d. Evaluate all three verdict options (see output format below).
  e. Write the output markdown file to the output_file path.

{pr_entries}

## Output format

Write a markdown file to the PR's output_file with YAML frontmatter
and three sections.

Frontmatter fields:
- pr_url: (from the PR entry)
- repo: (from the PR entry)
- pr_number: (from the PR entry)
- title: (from the PR entry's pr_title)
- verdict: relevant | peripheral | noise
- gist: One sentence (max 120 chars) summarizing what changed from a user/admin perspective

## Verdict reasoning

Before choosing a verdict, evaluate this PR against ALL THREE options.
For each, write ONE sentence stating the strongest argument for that verdict.

### Case for noise
Would a technical writer see anything documentation-worthy in this patch?
If nothing here would appear in any user-facing document, the case is strong.

### Case for peripheral
Does this PR fix a bug, add tests, refactor internals, or make infrastructure
changes that DON'T change what documentation would say? A bug fix restoring
already-documented behavior is peripheral. A test-only PR is peripheral.
A PR that "addresses review comments" from another PR is peripheral.
Ask: "Would the documentation read differently if this fix never landed?"
If no, it's peripheral.

### Case for relevant
Does this PR ADD or ALTER what documentation should say? New UI, new API,
new configuration, changed defaults, removed capabilities, or behavior
a tech writer would describe differently than before this PR.

### Chosen verdict
State which verdict wins and why in one sentence.

## What changed

One short paragraph. Describe what this PR does from a USER or ADMIN
perspective, not implementation details. If it adds UI, say what the
user sees. If it changes an API, say what callers can now do. If it's
pure infrastructure, say so briefly.

## Documentation impact

2-3 bullets maximum. What would a technical writer need to update or add?
Think: new procedures, changed steps, new config options, new UI screens,
changed behavior, removed capabilities.

DOs:
- Keep the gist field under 120 characters — it appears in a summary table
- Focus on WHAT changed for the user, not HOW it was implemented
- Mention new configuration knobs, CLI flags, or environment variables by name
- Note if this is a breaking change or changes default behavior
DON'Ts:
- Don't describe code structure, function names, or module organization
- Don't list every file changed
- Don't speculate about changes outside the patch
- Don't write more than 200 words total (excluding verdict reasoning)
