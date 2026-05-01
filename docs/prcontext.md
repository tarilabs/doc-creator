# PR Context: Design Principles and Architecture

## What this is

A three-phase pipeline that takes PR URLs from the jiracontext manifest, downloads their patches and metadata via `gh`, and produces concise documentation-relevance summaries. Phase 1 is deterministic (fetch, filter, pre-classify). Phase 2 uses LLM subagents to classify each PR. Phase 3 is deterministic (verdict sanity check).

The output is `artifacts/prcontext/` with filtered patches and per-PR summary files, and `artifacts/prcontext.md` as the manifest with a verdict table in its body.

## The problem it solves

JIRA Context collects PR URLs from engineering tickets, but the URLs alone tell a documentation writer nothing. Opening 20+ PRs manually to assess their documentation relevance is slow, inconsistent, and doesn't scale. This pipeline automates the triage by downloading each PR's patch, stripping noise, and having an LLM evaluate whether the changes would alter what documentation says — not just whether they touch relevant code.

## Design: buttons then judgment

Following the CLAUDE.md principle "constrain creativity — prefer buttons over bag of parts," the pipeline front-loads deterministic work (scripts) and reserves LLM judgment for the one step that genuinely requires it.

| Step | Type | Script/Agent |
|------|------|--------------|
| 1. Fetch | deterministic | `pr_context_fetch.py` |
| 2. Filter | deterministic | `pr_context_filter.py` |
| 3. Pre-classify | deterministic | `pr_context_preclassify.py` |
| 4. Prepare prompts | deterministic | `pr_context_prepare.py` |
| 5. Summarize | LLM (haiku) | subagents via skill |
| 6. Sanitize + check | deterministic | `pr_context_sanitize_yaml.py` + `pr_context_verdict_check.py` |
| 7. Report | deterministic | `pr_context_report.py` |

## The six scripts

### `pr_context_fetch.py`

Downloads patches and metadata for each GitHub PR URL in the jiracontext manifest. Uses `gh pr diff --patch` for the unified diff and `gh pr view --json` for title, body, labels, file list, and addition/deletion counts. Writes raw patches and `.meta.yaml` files to `artifacts/prcontext/raw/`. Generates `artifacts/prcontext.md` (sibling of the data directory) with YAML frontmatter listing each entry's URL, file stem, status, and title.

GitLab MRs are marked `skipped` — `gh` doesn't support them.

**Efficiency:** Already-fetched PRs (both `.patch` and `.meta.yaml` exist) are skipped on re-runs, making the script idempotent.

### `pr_context_filter.py`

Parses unified diffs into file entries and hunks, then strips:

- **Noise files** — lock files, generated code, images, source maps (matched by `NOISE_GLOBS`)
- **Test files** — `*_test.go`, `test_*.py`, `*.spec.ts`, etc. (matched by `TEST_GLOBS`), unless the PR is test-only (in which case test hunks are preserved so the LLM can see them)
- **Whitespace-only hunks** — added/removed lines that differ only in whitespace

Output goes to `artifacts/prcontext/filtered/`. An empty (0-byte) filtered patch means the entire PR was noise — the skill writes a `verdict: noise` summary directly without invoking an LLM.

### `pr_context_preclassify.py`

Adds a deterministic `hint` field to each manifest entry before LLM evaluation. Hints are advisory — the LLM can override them.

**Title-based signals** (case-insensitive prefix matching):
- `fix:`, `test:`, `chore:`, `refactor:` → `candidate-peripheral`
- Contains "address review comments" or "review feedback" → `candidate-peripheral`
- Contains `\brevert\b` → `candidate-peripheral`

**File-level signals** (from `.meta.yaml` file list):
- All file paths match `TEST_GLOBS` → `candidate-peripheral`
- Filtered patch is 0 bytes → `candidate-noise`

**Priority:** noise > file-level > title-level > `no-hint`.

**Design insight:** The `fix:` prefix is a weak signal. In a first release (like MCP Catalog v1), many PRs titled `fix:` actually implement *new* functionality that was never working before — they're relevant, not peripheral. The pre-classifier flags them as candidates, but the LLM evaluates the actual patch content against the documentation target to decide. In our test run, 6 of 8 `candidate-peripheral` hints were overridden to `relevant` — correctly, because the "fixes" introduced new API surface or metadata fields.

### `pr_context_prepare.py`

Writes one `.prompt.md` file per PR to `artifacts/prcontext/`. PRs with empty filtered patches get a noise summary written directly (no LLM needed). Each remaining PR gets its own self-contained prompt file.

The script reads `prompt-template.md` from the skill directory and replaces placeholders with per-PR metadata (file paths, hint blocks, titles, URLs). The subagent reads the file as its complete instructions.

Outputs a JSON summary to stdout: `{"prompts": ["artifacts/prcontext/org__repo__123.prompt.md", ...], "noise_written": N}`. The orchestrator parses this to get prompt file paths for dispatch — zero template logic in the orchestrator.

**Design principle:** The orchestrator never touches prompt content. The script makes all template decisions deterministically; the orchestrator reads JSON paths and dispatches redirect prompts (see "Key architectural insight" below).

### `pr_context_sanitize_yaml.py`

Fixes unquoted YAML string values in subagent-written summary files. Runs before the verdict check to prevent `yaml.safe_load()` failures. Processes each summary file line by line: if a frontmatter value contains a colon and isn't already quoted, wraps it in double quotes. Idempotent — running twice produces the same output. Logs which files and fields were repaired. Exit 0 always (repair is best-effort, not fatal).

**Why this exists:** Five consecutive runs (6-10) surfaced YAML parsing failures from unquoted `title` and `gist` fields containing colons (e.g. `title: feat: Add MCP...`). The prompt template instructs quoting but agents remain inconsistent. This script provides a deterministic safety net.

### `pr_context_verdict_check.py`

Post-hoc sanity check on verdict distribution. Catches the failure mode where all PRs get the same verdict (usually "relevant") due to prompt issues or context loss.

**Checks:**
- **Distribution skew** — if ≥5 fetched entries and any single verdict exceeds 80%, flag it
- **Hint overrides** — if a `candidate-peripheral` entry got `verdict: relevant`, flag it (advisory, not necessarily wrong)
- **Missing summaries** — fetched entries without a summary file

Writes `verdict_check.md` with the distribution and flag list. Exit 1 is advisory (flags raised but pipeline continues), not fatal.

### `pr_context_report.py`

Reads all per-PR summary files from `artifacts/prcontext/`, extracts YAML frontmatter fields (`verdict`, `gist`, `pr_url`, `repo`, `pr_number`, `title`), joins with the manifest (`hint`), and generates the markdown verdict table that becomes the body of `artifacts/prcontext.md`. Also appends verdict distribution counts and any flags from `verdict_check.md`.

This script closes anti-pattern 3 (below): the orchestrator no longer reads summary files or parses markdown prose. It calls the script, and the script reads structured frontmatter — zero content processing in the orchestrator.

**Requires:** Each summary file must include a `gist:` field in its YAML frontmatter (max 120 chars). This was added to the prompt template when the script was introduced.

## Prompt design: comparative evaluation

The prompt template (`prompt-template.md`) evolved through two iterations:

### First version: "pick a verdict"

The original prompt listed three verdict options and asked the agent to choose one. Result: 22/22 PRs classified as "relevant." The agent defaulted to the safest answer because it was never forced to articulate the alternative cases.

### Current version: evaluate all three

The revised prompt requires the agent to state the strongest case for each verdict before choosing:

1. **Case for noise** — is anything here documentation-worthy?
2. **Case for peripheral** — would the docs read differently without this fix?
3. **Case for relevant** — does this PR add or alter what docs should say?
4. **Chosen verdict** — which wins and why

This follows the CLAUDE.md principle "an agent that compares options reasons better than one asked for its best guess." The forced comparison means the agent must confront the case for "peripheral" even when its first instinct is "relevant."

The verdict reasoning is preserved on disk in each summary file, enabling post-hoc debugging of classification quality.

### Full documentation target, not a summary

The first run also suffered from the orchestrator compressing the ~5KB documentation target to ~3 sentences. The revised prompt gives each subagent the file path to `jiracontext.md` and instructs it to read the full specification — including "In Scope," "Out of Scope," and "Acceptance Criteria" sections. This prevents lossy compression and gives the agent the scope boundaries it needs to distinguish "touches MCP code" from "changes what MCP docs should say."

## Results: classification quality across runs

Verdict distributions stabilized by run 4 and remained consistent across all subsequent dispatch models. The table below shows the progression from the initial prompt (run 1) through the current architecture (run 11):

| Run | Architecture | Relevant | Peripheral | Noise | Pipeline time |
|-----|-------------|----------|------------|-------|---------------|
| 1 | v1 prompt, sequential agents | 22 (100%) | 0 | 0 | ~15min |
| 2-5 | v2 prompt (comparative eval), various | 15-19 (68-86%) | 3-7 (14-32%) | 0-1 | ~2-5min |
| 6-8 | Batching experiments (Agent, Skill, mixed) | 12-16 (55-73%) | 5-8 (23-36%) | 1-4 (5-18%) | ~80s-6min |
| 9 | Prepare script + Agent batch (5 batches) | 15 (68%) | 7 (32%) | 0 | ~2.5min |
| 10 | Redirect prompts + Agent batch (5 batches) | 13 (59%) | 7 (32%) | 2 (9%) | ~2min |
| 11 | **Redirect prompts + per-PR agents (22 agents)** | **12 (55%)** | **8 (36%)** | **2 (9%)** | **~1.5min** |

Run 11 confirms that per-PR dispatch with redirect prompts is the fastest and cleanest architecture: perfect isolation (zero cross-PR contamination), individual retry granularity, simplest possible prompt template, and the fastest pipeline time to date.

**Verdict variance is dominated by prompt design, not dispatch model.** Runs 6-11 used five different dispatch architectures but produced verdict distributions within normal LLM variance (12-16 relevant, 5-8 peripheral, 0-4 noise). The two changes that genuinely improved classification quality were the v2 comparative-evaluation prompt (run 2) and the full documentation target (run 2+).

## Testing strategy

All tests in `tests/test_pr_context.py`, using the `art_dir` fixture (tmpdir with `artifacts/` subdirectory, chdir'd into).

### Tier 1a: Fetch script (5 tests)

URL parsing, file stem generation, manifest writing, GitLab MR skipping, missing manifest handling.

### Tier 1b: Filter script (9 tests)

Lock files dropped, whitespace-only dropped, source preserved, test-only PRs preserved, mixed PRs drop tests, images dropped, diff headers preserved, missing input dir.

### Tier 1c: Pre-classify script (10 tests)

Title prefix patterns (`fix:`, `test:`, review comments, `feat:`, no prefix), file-level analysis (all-test files, empty patches, mixed files), skipped entries, missing manifest.

### Tier 1d: Verdict check script (7 tests)

Mixed verdicts clean, distribution skew detection, hint overrides, missing summaries, small batches below threshold, report file creation, missing manifest.

### Tier 1g: Sanitize YAML script (7 tests)

Unquoted title with colon, unquoted gist with colon, already-quoted stays unchanged, no-colon stays unchanged, body content preserved, idempotent, referenced script exists.

### Tier 2: Skill YAML validation (7 tests)

Frontmatter fields, name matches directory, name format, under 500 lines, referenced scripts exist, prompt template exists, 7 steps.

### Test budget

| Tier | Tests | Time | Cost |
|------|-------|------|------|
| 1a-1g: Scripts | 38 | ~0.8s | Free |
| 2: Skill YAML | 7 | ~0.1s | Free |

No LLM tests yet — the skill invokes subagents through `Agent` tool calls which aren't subprocess-testable. The deterministic guardrails (pre-classify + verdict check) compensate by catching the most common failure modes without LLM invocation.

## File layout

```
scripts/
  pr_context_fetch.py          # Download patches and metadata via gh
  pr_context_filter.py         # Strip noise hunks from patches
  pr_context_preclassify.py    # Add deterministic hints to manifest
  pr_context_prepare.py        # Build per-PR prompt files
  pr_context_sanitize_yaml.py  # Fix unquoted YAML frontmatter in summaries
  pr_context_verdict_check.py  # Post-hoc sanity check on verdicts
  pr_context_report.py         # Generate verdict table from summary frontmatter

.claude/skills/prcontext-populate/
  SKILL.md                     # Skill definition (7 steps)
  prompt-template.md           # Subagent prompt template

tests/
  test_pr_context.py           # Tiers 1a-1d and Tier 2

artifacts/
  prcontext.md                 # Manifest (YAML frontmatter + verdict table body)
  prcontext/
    raw/{file}.patch             # Original patches from gh
    raw/{file}.meta.yaml         # PR metadata (title, body, labels, files)
    filtered/{file}.patch        # Noise-filtered patches
    {file}.prompt.md             # Per-PR prompt file (agent reads this)
    {file}.md                    # Per-PR summary with verdict reasoning
    verdict_check.md             # Distribution check report
```

## Orchestrator anti-patterns (learned the hard way)

Three anti-patterns surfaced during early runs and drove the architecture toward redirect prompts.

### Anti-pattern 1: Content in the orchestrator's context triggers reasoning about it

The original prompt template used inline content placeholders (`{pr_body}`, `{filtered_patch}`) — the orchestrator loaded ~70KB of PR descriptions and patches, then reasoned extensively about how to structure prompts. None of this reasoning was its job.

**Fix progression:** Inline content → file path placeholders (agent reads its own files) → deterministic script builds prompt files → redirect prompt ("Read {path} and follow all instructions"). Each step removed content from the orchestrator until it had none.

**Principle:** The orchestrator can't reason about content it never sees. If you want pure routing behavior, don't give it content.

### Anti-pattern 2: Soft parallelism instructions get ignored

"Spawn subagents in parallel where possible" resulted in sequential launches. The orchestrator had so much content context that it fell into a sequential reasoning pattern.

**Fix:** Hard constraint: "CRITICAL — launch ALL subagents in a SINGLE message." Combined with redirect prompts (the orchestrator generates ~50 bytes per Agent call instead of ~10KB), this makes parallel dispatch the path of least resistance.

**Principle:** Soft instructions ("where possible", "prefer") are suggestions the model overrides. Hard constraints ("DO NOT", "CRITICAL", "SINGLE message") are boundaries. When the behavior matters, use constraints.

### Anti-pattern 3: Unstructured output forces the orchestrator to parse content

The report step originally required parsing markdown prose from 22 summary files to extract gists.

**Fix:** Added a `gist:` field to the prompt template's YAML frontmatter requirements. The `pr_context_report.py` script reads structured frontmatter — the orchestrator calls the script and relays output.

**Principle:** Every piece of data the orchestrator needs should be in structured frontmatter, not parsed from markdown prose.

## Key architectural insight: build the prompt outside the coordinator

The single most impactful optimization across 11 runs was moving prompt construction entirely outside the orchestrator. The insight has two parts:

### 1. A deterministic script builds each prompt file

`pr_context_prepare.py` reads the manifest, fills the prompt template with per-PR metadata (file paths, hint blocks, titles, URLs), and writes one `.prompt.md` file per PR. The orchestrator never touches template logic, never reasons about grouping, never sees PR content.

### 2. The orchestrator sends a redirect, not a prompt

Instead of reading the prompt file and embedding its contents in the Agent call, the orchestrator sends a one-line redirect:

> Read /absolute/path/to/org__repo__123.prompt.md and follow all instructions exactly.

The agent reads its own prompt file. The orchestrator's total context for Step 5 (dispatching 22 agents) is ~2KB of redirect strings — not the ~50-70KB it would be if it loaded prompt content or PR patches.

### Why this matters

The orchestrator's context window directly affects dispatch speed. When the orchestrator loads prompt files into its context, it must generate longer Agent tool calls (embedding multi-KB prompts), which slows model generation. It also risks reasoning about PR content — exactly the anti-pattern that caused the original 15-minute sequential runs.

With redirect prompts, dispatch becomes mechanical: parse a JSON list of file paths, generate 22 short redirect strings, send them all in a single message. The model generates the dispatch message near-instantly because each Agent call is ~50 bytes. The agents themselves do the heavy lifting — reading their prompt files, reading the documentation target, evaluating the patches.

### Progression

| Phase | What the orchestrator loads for dispatch | Pipeline time |
|-------|------------------------------------------|---------------|
| Runs 1-5 | PR content (patches, bodies, metadata) — 70KB+ | ~5-15min |
| Runs 6-8 | Prompt templates + metadata — ~20KB | ~80s-6min |
| Run 9 | Pre-built prompt files — ~50KB | ~2.5min |
| Runs 10-11 | Redirect strings only — ~500 bytes-2KB | ~1.5-2min |

Each step removed a category of content from the orchestrator's context until it reached the limit: the orchestrator knows nothing about what the agents will do. It knows file paths and that's it.

### Batching is no longer needed

Runs 6-9 explored batching (grouping 4-5 PRs per agent to reduce dispatch overhead from 22 calls to 5). This worked but introduced cross-PR contamination risk, complicated the prompt template with loop structures, and made individual retry impossible. Once redirect prompts made the per-call dispatch cost negligible (~50 bytes vs ~10KB per Agent call), the batching trade-off flipped: 22 per-PR agents with redirect prompts (run 11, ~1.5 minutes) is faster than 5 batched agents (run 10, ~2 minutes) because each agent processes only one PR in ~20s instead of 4-5 PRs sequentially in ~70s.

## What's next

- **Cypress/E2E test glob expansion** — the current `TEST_GLOBS` miss Cypress-style paths (`packages/cypress/cypress/*.ts`), causing test-only PRs like #7126 to pass through to LLM evaluation without a hint
- **Verdict confidence scoring** — the comparative evaluation produces reasoning; a second pass could score confidence (high/medium/low) based on how close the peripheral vs relevant arguments are
- **Cross-PR deduplication** — PRs that implement the same feature incrementally (e.g., #6747 adds mock endpoints, #6990 replaces them with real ones) could be grouped to avoid redundant documentation impact bullets
