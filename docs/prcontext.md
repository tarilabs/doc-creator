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
| 4-5. Summarize | LLM (haiku) | subagents via skill |
| 6. Verdict check | deterministic | `pr_context_verdict_check.py` |
| 7. Report | deterministic | `pr_context_report.py` |

## The five scripts

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

## Results: before and after

| Metric | First run (v1 prompt) | Second run (v2 prompt) | Third run (v2, different PRs) | Fourth run (v2, 22 PRs) | Fifth run (v2, 22 PRs, report script) | Sixth run (v2, 22 PRs, batched) |
|--------|----------------------|------------------------|-------------------------------|-------------------------|---------------------------------------|--------------------------------|
| Relevant | 22 (100%) | 16 (73%) | 19 (86%) | 15 (68%) | 16 (73%) | 12 (55%) |
| Peripheral | 0 | 6 (27%) | 3 (14%) | 7 (32%) | 5 (23%) | 6 (27%) |
| Noise | 0 | 0 | 0 | 0 | 1 (5%) | 4 (18%) |
| Distribution skew flag | would have triggered | not triggered | triggered (advisory) | not triggered | not triggered | not triggered |
| Hint overrides | n/a | 6 (all justified) | 6 (all justified) | 3 (all justified) | 4 (all justified) | 2 (both justified) |

The second run's 6 peripheral PRs: Cypress tests (#7126), test verification (#1285), UX polish (#7131), route refactoring (#7072), review follow-up (#7082), and YAML simplification (#7063). All correctly identified as not changing what documentation would say.

The third run (different PR set, 23 PRs from RHAISTRAT-1084) produced 3 peripheral: route refactoring (#7072), review follow-up (#7082), test verification (#1285). The higher relevant ratio (86%) is expected — this PR set is tightly scoped to MCP Catalog v1 Summit delivery. The distribution skew flag fired but is advisory; the skew reflects the feature's focus, not a classification failure.

The fourth run (22 PRs, same JIRA source) produced the healthiest distribution: 68% relevant, 32% peripheral. Hint override rate dropped to 3 of 8 (vs 6 of 8 in run 2), suggesting subagents are calibrating better — accepting the peripheral hint when the fix genuinely restores existing behavior (#2367 sort fix, #2432 YAML data flow, #2442 sort-by-name) while still overriding when the "fix" introduces new documented capability (#2433 sourceLabel filtering, #2461 securityIndicators, #7021 URL display behavior). The 7 peripheral PRs: sort fix (#2367), YAML data fix (#2432), sort-by-name fix (#2442), route refactoring (#7072), review follow-up (#7082), Cypress tests (#7126), test verification (#1285).

The fifth run (22 PRs, same JIRA source) used the new `pr_context_report.py` script for Step 7 instead of orchestrator-built reports. Results: 16 relevant (73%), 5 peripheral (23%), 1 noise (5%) — the first run to produce a noise verdict (#7072, internal route utility refactor). The 4 hint overrides (#2432 tool metadata, #2433 sourceLabel filtering, #2461 securityIndicators, #7021 URL display behavior) were all justified: these `fix(`-prefixed PRs implement or enable spec-required capabilities. Notably, #2432 flipped from peripheral (run 4) to relevant (run 5) — the subagent correctly identified that preserving `accessType` and `parameters` fields is a core catalog metadata requirement, not a bug fix.

The sixth run (22 PRs, same JIRA source) was the first run with subagent batching (Option B). Results: 12 relevant (55%), 6 peripheral (27%), 4 noise (18%) — the healthiest distribution yet and the first run where noise exceeded a single PR. The 4 noise verdicts: UI microcopy (#2420), route refactoring (#7072), Cypress tests (#7126), and test verification (#1285). Only 2 hint overrides (#2432 and #2433), both justified — they implement new API behavior despite `fix(` prefix. The lower override count (2 vs 4 in run 5) suggests batched subagents are slightly more conservative, which is appropriate: #2461 (securityIndicators) was correctly classified as peripheral this time (internal YAML field reorganization, no API surface change), and #7021 (URL truncation fix) was also correctly peripheral.

Observations specific to the batching implementation:
- **Orchestrator launched 4 of 5 batches, then 1 separately.** This was an orchestrator mistake — the skill says "launch ALL batch subagents in a SINGLE message" but the orchestrator split across two messages. The fifth batch launched only after a completion notification arrived for batch 1. Net effect: batch 5 started ~60s later than it should have. This suggests that "single message" is necessary but not sufficient — the orchestrator still needs to count its batches and verify all are included before sending.
- **YAML frontmatter quoting fragility.** One subagent (batch 2) wrote `title: fix(catalog): populate securityIndicators...` without quoting the value. The colon after `fix(catalog)` broke YAML parsing in the verdict check script. This is the first time a subagent produced invalid frontmatter — prior runs always quoted titles with colons. The prompt template's frontmatter example should explicitly show quoted values for string fields that may contain colons.
- **Wall-clock time.** Steps 1-3 (deterministic): ~10s. Subagent dispatch + execution: ~65s from first launch to last completion (would have been ~50s if all 5 batches launched together). Steps 6-7: ~5s. Total: ~80s. Compared to pre-batching runs where 22 sequential subagents took ~15 minutes, and theoretical best-case of ~35s estimated in the Option B analysis.
- **Spawn latency still perceptible.** Even with 5 Agent calls instead of 22, the user observes visible per-call pauses during dispatch. The improvement is significant in total time (80s vs 15min) but the UX of watching 5 sequential spawns is still noticeably slower than the deterministic script phases.

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

### Tier 2: Skill YAML validation (7 tests)

Frontmatter fields, name matches directory, name format, under 500 lines, referenced scripts exist, prompt template exists, 7 steps.

### Test budget

| Tier | Tests | Time | Cost |
|------|-------|------|------|
| 1a-1d: Scripts | 31 | ~0.8s | Free |
| 2: Skill YAML | 7 | ~0.1s | Free |

No LLM tests yet — the skill invokes subagents through `Agent` tool calls which aren't subprocess-testable. The deterministic guardrails (pre-classify + verdict check) compensate by catching the most common failure modes without LLM invocation.

## File layout

```
scripts/
  pr_context_fetch.py          # Download patches and metadata via gh
  pr_context_filter.py         # Strip noise hunks from patches
  pr_context_preclassify.py    # Add deterministic hints to manifest
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
    {file}.md                    # Per-PR summary with verdict reasoning
    verdict_check.md             # Distribution check report
```

## Orchestrator anti-patterns (learned the hard way)

Two issues surfaced during the first real run with 22 PRs that led to skill redesign.

### Anti-pattern 1: Inline content placeholders pull data into orchestrator context

The original prompt template used `{pr_body}` and `{filtered_patch}` as inline placeholders — the orchestrator was expected to read each PR's `meta.yaml` and filtered patch file, then embed the content into the subagent prompt. With 22 PRs, this meant the orchestrator loaded ~70KB of PR descriptions and reasoned extensively about how to structure prompts, whether patches were "too large" to embed, what hybrid approach to take, etc. None of this reasoning was its job.

**Fix:** Replace inline content placeholders with file path placeholders (`{meta_yaml_path}`, `{filtered_patch_path}`). The subagent reads its own input files. The orchestrator never sees PR content — it only handles metadata from the manifest (title, URL, file stem, hint).

**Principle:** Template placeholders shape orchestrator behavior. Inline content placeholders force the orchestrator to become a content processor. File path placeholders keep it a mechanical dispatcher.

### Anti-pattern 2: Soft parallelism instructions get ignored

The original skill said "Spawn subagents in parallel where possible (batch independent PRs)." In practice, the orchestrator launched one subagent at a time, waited for its completion notification, then launched the next. All 22 PRs were independent — this should have been a single message with 22 Agent tool calls.

The cause: the orchestrator spent so much context reasoning about PR content (anti-pattern 1) that by the time it started launching agents, it was in a sequential mindset — sending one, processing the result, sending the next.

**Fix:** Replace the soft instruction with a hard constraint: "CRITICAL — launch ALL subagents in a SINGLE message. Do NOT wait for any subagent to complete before launching others. Do NOT launch them in sequential batches." Add a second guardrail: "DO NOT reason about PR content, titles, or verdicts. Your job is mechanical."

**Principle:** Soft instructions ("where possible", "prefer", "try to") are suggestions the model can override based on its own judgment. Hard constraints ("DO NOT", "CRITICAL", "SINGLE message") are boundaries it must respect. When the behavior matters, use constraints, not suggestions.

### Combined effect

In the first run, the orchestrator took ~15 minutes to launch 22 subagents sequentially, with extensive reasoning between each launch. After the fix, the expected behavior is: read the manifest (~2 seconds), fill in 22 templates mechanically (~5 seconds), launch all 22 in one message (~1 second), wait for completions (~15 seconds for all in parallel). Total: ~25 seconds of orchestrator work instead of ~15 minutes.

### Anti-pattern 3: Report-building pulls unstructured content into orchestrator context

The report step (Step 7) requires a one-line gist for each PR in the summary table. This gist lives in the markdown body of each summary file under "## What changed" — not in YAML frontmatter. The orchestrator must read 22 files and parse free-text markdown to extract it.

In the fourth run, 5 of 22 files resisted automated extraction (awk/grep) because subagents used slightly different formatting: blank lines between heading and paragraph, different heading capitalization ("What Changed" vs "What changed"), or multi-paragraph sections where only the first paragraph was wanted.

**Root cause:** The gist isn't a structured field. It's a natural-language paragraph that the orchestrator must compress to one line for the table. This forces the orchestrator into a content-processing role — exactly what anti-pattern 1 warned against.

**Fix (implemented, run 5):** Added a `gist:` field to the prompt template's YAML frontmatter requirements (one sentence, max 120 characters). The `pr_context_report.py` script reads all summary files' frontmatter (verdict + gist), joins with the manifest (hint, repo, PR number), and writes the report table into the manifest body. The orchestrator calls the script and relays the output — zero file reads, zero content parsing.

**Principle:** Every piece of data the orchestrator needs for the report should be in structured frontmatter, not parsed from markdown prose. If the subagent produces it, the subagent should put it where a script can find it.

## Known limitations

### Subagent spawn latency

Launching N subagents in a single message satisfies the "launch ALL in a SINGLE message" constraint, but tool dispatch still serializes — each Agent call incurs a ~1-2s pause before the next starts. With 22 PRs, this means ~20-40s of spawn time before the first subagent even begins work, even though the subagents themselves complete in ~15-20s once launched.

The spawn phase is the slowest part of the pipeline. The deterministic scripts (Steps 1-3, 6-7) complete in seconds. The subagent execution (Step 5) completes in ~15-20s wall-clock because all run in parallel. But the dispatch overhead to launch them dominates.

There are two independent bottlenecks stacking:

1. **Generation time.** A single message with 22 Agent tool calls is ~22KB of JSON the model must generate. The entire response is generated before *any* tool call dispatches. With streaming, early tool calls may start sooner, but the model still generates all 22 prompts sequentially within one inference pass.

2. **Dispatch overhead.** Each Agent tool call requires API setup, process spawning, and context initialization — roughly 1-2s per call. This serializes even within a single message.

Additionally, [community reports](https://claudefa.st/blog/guide/agents/sub-agent-best-practices) suggest a **concurrency cap of ~10 simultaneous subagents** in Claude Code. Beyond 10, subagents queue and execute in batches — the runtime waits for an entire batch to finish before starting the next. If true, 22 agents would run in 3 waves (10 + 10 + 2), not 22-way parallel. This would explain why subagent *execution* still takes ~15-20s despite all being "launched at once."

#### Option A: Multi-message dispatch (relaxing the SINGLE message constraint)

The "SINGLE message" constraint (anti-pattern 2) was designed to prevent the orchestrator from *waiting for completions* and *reasoning about content* between launches. But with `run_in_background: true`, the orchestrator gets an instant async response — it never waits, and never sees subagent content.

So the real invariant isn't "single message" — it's:

> **Don't load PR content into orchestrator context. Don't reason about PR content. Don't wait for completions before launching more agents.**

Relaxing to multi-message dispatch would look like:

- Message 1: launch 8 agents with `run_in_background: true` → returns immediately
- Message 2: launch 8 more → returns immediately
- Message 3: launch 6 more → returns immediately
- Wait for all completion notifications

**Potential benefit:** The first batch of agents starts executing while the model generates Message 2. With single-message, no agent starts until all 22 tool call blocks are fully generated. Multi-message pipelines generation with execution.

**Likely non-benefit:** The per-tool-call dispatch overhead (~1-2s) is the same regardless of how calls are grouped. And each additional message requires its own inference pass (reading accumulated context, generating the batch), which adds overhead. For 22 agents, the dispatch overhead dominates — splitting across 3 messages might save ~5-10s of generation pipelining but adds ~3-5s of inter-message overhead.

**Verdict:** Marginal improvement at best. Addresses the generation bottleneck but not the dispatch bottleneck. Worth testing empirically, but not a fundamental fix.

#### Option B: Subagent batching (fewer, larger subagents)

Instead of 22 subagents × 1 PR each, use **~5 subagents × 4-5 PRs each**. Each subagent processes its batch sequentially, writing a separate output file per PR.

**Benefits:**
- Dispatch overhead drops from ~33s (22 × 1.5s) to ~7s (5 × 1.5s) — an 80% reduction
- Generation time drops proportionally (5 tool calls to generate instead of 22)
- Well under the ~10 concurrency cap, so all 5 run truly in parallel
- `jiracontext.md` (the documentation target) is read once per subagent instead of 22 times
- Orchestrator stays mechanical — groups PRs into batches, fills a batch template, dispatches

**Costs:**
- Context contamination: one PR's content could influence the next verdict within the same subagent. Mitigated by processing each PR with explicit "reset" instructions and separate output files.
- Individual retry: if a subagent fails, 4-5 PRs must be re-evaluated instead of 1. Mitigated by the fact that haiku subagents rarely fail on well-scoped tasks.
- Template complexity: the batch template needs a loop structure ("for each PR in this batch, do steps 1-3 and write a separate file").

**Estimated wall-clock time:** ~7s dispatch + ~25-30s execution (each subagent processes 4-5 PRs sequentially at ~5-6s each) = ~35s total. Versus current: ~33s dispatch + ~20s execution = ~53s. Net improvement: ~18s (~34%), and the UX is dramatically better — 5 fast dispatches instead of 22 slow ones.

**Verdict:** Best available option. Reduces the dominant bottleneck (dispatch count) by 80% with manageable trade-offs.

#### Option C: External orchestration via script

Replace Agent tool calls entirely with a Python script that launches N `claude` CLI processes in parallel using `subprocess` / `asyncio`. Each process runs a single-shot prompt that reads its input files and writes its output file. OS-level process parallelism, no Agent tool dispatch.

**Benefits:**
- Bypasses the Agent tool dispatch layer entirely
- True OS-level parallelism with no artificial concurrency cap
- Testable, auditable, aligns with "scripts are buttons"

**Costs:**
- Requires `claude` CLI configured with API keys in the script's environment
- Each CLI process pays its own API cold-start cost
- No integration with Claude Code's task panel, completion notifications, or permission system
- Moves subagent orchestration outside of Claude Code — harder to debug, inspect, or evolve

**Verdict:** Architecturally clean but operationally complex. Worth considering if the pipeline grows beyond ~50 PRs, but over-engineering for the current scale.

#### Implementation: Option B (subagent batching)

**Implemented.** The prompt template and skill Steps 4-5 were rewritten:

- `prompt-template.md` now accepts a `{pr_entries}` placeholder containing a structured list of PRs instead of single-PR placeholders. The subagent reads `jiracontext.md` once, then loops through each PR in its batch, writing a separate output file per PR.
- Step 4 groups entries into batches of `max(1, ceil(N/5))` PRs each, pre-computes hint blocks, and builds the `{pr_entries}` text block for each batch.
- Step 5 launches one subagent per batch (~5 Agent calls instead of ~22) in a single message.
- Anti-contamination: explicit independence instruction ("do not let one PR's verdict influence another"), fresh file reads per PR, separate output files, and a DON'T ("don't reference or compare with other PRs in this batch").
- Downstream scripts unchanged — per-PR `.md` output files keep the same frontmatter and structure.

## What's next

Potential directions discussed but not yet implemented:

- ~~**Report script (`pr_context_report.py`)**~~ — **done** (run 5). Reads YAML frontmatter from summary files, generates the markdown verdict table, and appends flags from verdict check. The orchestrator calls the script and relays output — zero file reads, zero content parsing.
- ~~**Subagent batching**~~ — **done** (run 6). Prompt template and skill rewritten to batch ~4-5 PRs per Agent call (see Option B above). Reduces dispatch from ~22 calls to ~5.
- **YAML frontmatter quoting enforcement** — add explicit quoting examples to the prompt template's frontmatter spec (`title: "..."`, `gist: "..."`), or add a post-processing script that validates/fixes frontmatter before downstream consumption. Run 6 surfaced the first parsing failure from an unquoted colon in a title field.
- **Cypress/E2E test glob expansion** — the current `TEST_GLOBS` miss Cypress-style paths (`packages/cypress/cypress/*.ts`), causing test-only PRs like #7126 to pass through to LLM evaluation without a hint
- **Verdict confidence scoring** — the comparative evaluation produces reasoning; a second pass could score confidence (high/medium/low) based on how close the peripheral vs relevant arguments are
- **Cross-PR deduplication** — PRs that implement the same feature incrementally (e.g., #6747 adds mock endpoints, #6990 replaces them with real ones) could be grouped to avoid redundant documentation impact bullets
