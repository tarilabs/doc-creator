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

Groups PRs into batches, fills the prompt template, and writes one `.prompt.md` file per batch to `artifacts/prcontext/`. PRs with empty filtered patches get a noise summary written directly (no LLM needed). Remaining PRs are distributed into ≤5 batches of roughly equal size.

The script reads `prompt-template.md` from the skill directory and replaces placeholders with per-PR metadata (file paths, hint blocks, titles, URLs). Each batch prompt is self-contained — the subagent reads the file as its complete instructions.

Outputs a JSON summary to stdout: `{"batches": ["artifacts/prcontext/batch_0.prompt.md", ...], "noise_written": N}`. The orchestrator parses this to get batch file paths for dispatch — zero batching logic or template filling in the orchestrator.

**Design principle:** This script is the culmination of anti-patterns 1-2. The orchestrator no longer builds prompts (anti-pattern 1) or reasons about grouping (anti-pattern 2). The script makes all grouping and template decisions deterministically; the orchestrator reads JSON and dispatches.

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

## Results: before and after

| Metric | First run (v1 prompt) | Second run (v2 prompt) | Third run (v2, different PRs) | Fourth run (v2, 22 PRs) | Fifth run (v2, 22 PRs, report script) | Sixth run (v2, 22 PRs, batched) | Seventh run (22 PRs, Skill dispatch) | Eighth run (Skill batched dispatch) | Ninth run (prepare script + Agent batch) | Tenth run (redirect prompts) |
|--------|----------------------|------------------------|-------------------------------|-------------------------|---------------------------------------|--------------------------------|--------------------------------------|--------------------------------------|------------------------------------------|------------------------------|
| Relevant | 22 (100%) | 16 (73%) | 19 (86%) | 15 (68%) | 16 (73%) | 12 (55%) | 12 (55%) | 16 (73%) | 15 (68%) | 13 (59%) |
| Peripheral | 0 | 6 (27%) | 3 (14%) | 7 (32%) | 5 (23%) | 6 (27%) | 8 (36%) | 5 (23%) | 7 (32%) | 7 (32%) |
| Noise | 0 | 0 | 0 | 0 | 1 (5%) | 4 (18%) | 2 (9%) | 1 (5%) | 0 | 2 (9%) |
| Distribution skew flag | would have triggered | not triggered | triggered (advisory) | not triggered | not triggered | not triggered | not triggered | not triggered | not triggered | not triggered |
| Hint overrides | n/a | 6 (all justified) | 6 (all justified) | 3 (all justified) | 4 (all justified) | 2 (both justified) | 2 (both justified) | 5 (all justified) | 4 (all justified) | 3 (all justified) |

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

The seventh run (22 PRs, same JIRA source) switched from Agent-based batching to individual Skill tool invocations (`/pr-reviewer` per PR). Results: 12 relevant (55%), 8 peripheral (36%), 2 noise (9%). The relevant count matches run 6 exactly, but the peripheral/noise distribution shifted — 8 peripheral vs 6 in run 6, 2 noise vs 4 in run 6. Two PRs moved from noise to peripheral: #2420 (microcopy — run 6 noise, run 7 peripheral) and #7126 (Cypress tests — run 6 noise, run 7 noise). The 2 hint overrides (#2432 and #2433) are the same as run 6, both justified. The YAML frontmatter quoting bug recurred — `title: feat(mcp-deployments): microcopy...` broke the verdict check script, requiring a manual fix before Step 6 could run.

Observations specific to the Skill dispatch model:
- **Concurrent dispatch, serial collection.** All 22 Skill invocations were sent in a single message. The framework launched each as "forked execution" — they ran concurrently. But results serialized back into the response: each skill's output appeared one after another. The orchestrator could not proceed to Step 6 until all 22 had returned. From the user's perspective, dispatch was fast (no inter-invocation lag) but overall execution was sequential.
- **Skill vs Agent dispatch trade-off.** The Skill tool dispatches faster per-call than the Agent tool (lower overhead — no process spawn, no separate context initialization). This explains the user's observation of "much less lag" between invocations. But the Skill tool doesn't offer `run_in_background` — results must be collected before the orchestrator can continue. The Agent tool with `run_in_background: true` allows the orchestrator to dispatch and immediately move on, though in practice it still has to wait for all completions before Step 6.
- **No batching.** Each PR got its own dedicated Skill invocation rather than being batched with others. This means 22 independent LLM evaluations, each reading the full `jiracontext.md` target spec. More isolated (zero cross-PR contamination risk) but more total LLM calls.
- **YAML quoting still unfixed.** Same frontmatter parsing failure as run 6. The prompt template still doesn't show quoted examples for fields that may contain colons. This is now a two-run regression — it should be fixed before run 8.

The eighth run (22 PRs, same JIRA source) combined Skill dispatch with batching — 5 Skill invocations of `/pr-reviewer key1 key2 ...` with ~4-5 keys each, all dispatched in a single message. Results: 16 relevant (73%), 5 peripheral (23%), 1 noise (5%) — identical to run 5's distribution and a notable swing from runs 6-7 (both 55% relevant). The hint override count rose to 5 (all five model-registry `fix()` PRs judged relevant), returning to run 2-3 behavior where the reviewer recognized these "fixes" as implementing new functionality in a v1 feature.

Observations specific to the Skill-batched dispatch model:
- **Batch 5 misfired twice.** The smallest batch (2 keys: #7131 and #1285) failed to produce verdict files on two consecutive dispatches. Both times the Skill returned output describing the project's refactoring history instead of evaluating the PRs. Only individual Skill dispatch (one key per call) succeeded on the third attempt. Hypothesis: with only 2 positional args and no other batch context, the pr-reviewer skill may have too little signal to anchor on the evaluation task. The 4-5 key batches all succeeded on first attempt. This suggests a minimum batch size of ~3 keys for reliable Skill-based evaluation, or that single-key dispatch (run 7 style) is paradoxically more reliable than small-batch dispatch.
- **YAML quoting bug: third consecutive run.** The `gist` field in #7063's verdict file contained an unquoted `spec:`, breaking the verdict check script. The pr-reviewer SKILL.md already states "All string values that contain colons MUST be quoted with double quotes" — but this constraint targets `title` explicitly while only implying `gist`. The subagent quotes `title` reliably (run 6 fixed that) but doesn't extend the same discipline to `gist`. The DON'T list should explicitly call out gist quoting, or a post-processing script should enforce it.
- **Verdict variance between dispatch models.** Runs 5 and 8 (both 16R/5P/1N) used different dispatch models (run 5: individual Agent per PR; run 8: Skill batched) but produced identical distributions. Runs 6-7 (both 12R) used Agent batching and individual Skill dispatch respectively, and also matched each other. This suggests that dispatch model affects verdict quality less than other factors (prompt template version, model temperature, context ordering), but the sample is too small to confirm.
- **Wall-clock time still dominated by sequential collection.** Despite concurrent forked execution across 5 Skill calls, the orchestrator blocked until all 5 returned. The retry cycle for batch 5 added ~3 minutes to the total. Deterministic steps completed in ~25s; verdict evaluation + retries took ~5-6 minutes total.

The ninth run (22 PRs, same JIRA source) introduced `pr_context_prepare.py` — a new deterministic script for Step 4 that handles batch grouping, prompt template filling, and noise summary writing. The orchestrator no longer builds prompts; it reads the script's JSON output to get batch file paths, then reads each file and dispatches it as an Agent prompt. Results: 15 relevant (68%), 7 peripheral (32%), 0 noise — matching run 4's distribution exactly. The 4 hint overrides (#2367, #2432, #2433, #7021) were all justified: sort/filter fixes that implement new documented behavior in a v1 feature.

The 7 peripheral PRs: microcopy updates (#2420), sort-by-name fix (#2442), securityIndicators refactor (#2461), route refactoring (#7072), review follow-up (#7082), Cypress tests (#7126), test verification (#1285). Notably, #2461 returned to peripheral (matching run 6) after being relevant in runs 2-3 and 5 — the subagent correctly identified it as internal field reorganization with no API surface change.

Observations specific to the prepare-script + Agent dispatch model:
- **Deterministic prompt building eliminates orchestrator reasoning.** The `pr_context_prepare.py` script groups PRs into ≤5 batches, fills the prompt template with file paths and hint blocks, and writes one `.prompt.md` file per batch. The orchestrator's job reduced to: parse JSON, read files, dispatch agents. Zero template logic, zero batching decisions. This closes the gap between "the orchestrator is a router" (CLAUDE.md principle) and what actually happens.
- **All 5 batches dispatched in a single message.** Unlike run 6 (where the orchestrator split 4+1), this run correctly launched all 5 Agent calls in one message. The prepare script's JSON output made this mechanical — the orchestrator iterated a list rather than reasoning about grouping.
- **Agent spawn latency still perceptible but acceptable.** The user observed that individual agent bootstraps were not fast — each Agent call incurs visible ~1-2s dispatch overhead. But with only 5 calls (vs 22 in pre-batching runs), the total dispatch phase was ~10s. Subagent execution took ~65s wall-clock (bounded by the slowest batch at 64.7s). Total pipeline time including YAML fixes: ~2.5 minutes.
- **YAML frontmatter quoting: fourth consecutive run.** Two files (#7131 and #1285) had unquoted `title` fields containing colons, breaking the verdict check script. Both required manual fixes. The prompt template instructs quoting but subagents remain inconsistent. This reinforces the case for a deterministic post-processing script to validate/auto-quote YAML string fields — relying on LLM compliance after four consecutive failures is not a viable strategy.
- **Token usage varied by batch size and patch complexity.** Batch 2 (PRs #6907, #6910, #6977, #6982, #6990 — the core MCP deployment UI PRs with the largest patches) consumed 142K tokens. Batch 0 and batch 4 used ~46K tokens each. The 3x variance suggests token cost is driven by patch size more than PR count per batch.

The tenth run (22 PRs, same JIRA source) introduced **redirect prompts** — the orchestrator no longer reads batch prompt files into its own context. Instead of `Read batch_0.prompt.md` followed by an Agent call with the file's contents, the orchestrator sends a short redirect:

> Read /absolute/path/to/batch_0.prompt.md and follow all instructions exactly.

The agent reads its own prompt file. The orchestrator never sees prompt content, PR patches, metadata, or even the template structure. Its context for Step 5 is ~5 short strings. Results: 13 relevant (59%), 7 peripheral (32%), 2 noise (9%) — the healthiest relevant ratio yet and the first run to produce 2 noise verdicts without over-classifying. The 3 hint overrides (#2432, #2433, #2461) were all justified: `fix(`-prefixed PRs that implement new API surface (tool metadata, sourceLabel filtering, securityIndicators).

The 7 peripheral PRs: sort fix (#2367), microcopy (#2420), sort-by-name fix (#2442), mock endpoints (#6747), URL truncation fix (#7021), review follow-up (#7082), test verification (#1285). The 2 noise PRs: route refactoring (#7072), Cypress tests (#7126). Notably, #6747 (mock BFF endpoints) was correctly classified as peripheral — the production implementation defers to #6990.

Observations specific to the redirect-prompt model:
- **Orchestrator context stays minimal.** In run 9, the orchestrator read each batch prompt file (~5-15KB each) to construct the Agent prompt, loading ~50KB of template+metadata into its context window. In run 10, the orchestrator's Step 5 context is 5 redirect strings totaling ~500 bytes. This is the logical endpoint of the anti-pattern 1 trajectory: the orchestrator went from loading PR content (runs 1-5), to loading prompt files (run 9), to loading nothing (run 10).
- **Agent dispatch was immediate.** With no file reads between Step 4 and Step 5, the orchestrator parsed the JSON output and dispatched all 5 agents in one message with near-zero delay. The lightweight context also meant faster model generation — 5 short Agent calls generate faster than 5 calls with embedded multi-KB prompts.
- **Subagent execution bounded by batch complexity.** Batch durations: 57s (batch 0, 5 PRs), 72s (batch 1, 5 PRs with large patches), 63s (batch 2, 5 PRs), 69s (batch 3, 5 PRs), 34s (batch 4, 2 PRs). Token usage: 48K, 79K, 137K, 57K, 46K. Batch 2 consumed 137K tokens — the core MCP deployment PRs with the largest filtered patches. Wall-clock bounded by batch 1 at 72s.
- **Total pipeline time: ~2 minutes.** Steps 1-4 (deterministic): ~17s. Step 5 (agent dispatch + execution): ~75s. Steps 6-7 (verdict check + report): ~15s (including YAML fix). This is the fastest run to date and approaches the theoretical minimum for 5 batched haiku subagents processing 22 PRs.
- **YAML quoting bug: fifth consecutive run.** Two files: #6907 (unquoted `title: feat: Add MCP...`) and #7082 (unquoted `gist: Cleanup following PR 7063: removes...`). Both fixed with single-line edits. The pattern is now fully predictable — any string field containing a colon will occasionally be written unquoted regardless of prompt instructions. This confirms the need for a deterministic YAML sanitizer script.

**Key architectural insight: redirect prompts complete the "orchestrator is a router" vision.** The progression across 10 runs:

| Run | What the orchestrator loads for Step 5 |
|-----|----------------------------------------|
| 1-5 | PR content (patches, bodies, metadata) — 70KB+ |
| 6-8 | Prompt templates + metadata — ~20KB |
| 9 | Pre-built prompt files — ~50KB |
| 10 | Redirect strings only — ~500 bytes |

Each step removed a category of content from the orchestrator's context. Run 10 reaches the limit: the orchestrator knows nothing about what the agents will do. It knows file paths and that's it. This eliminates the last remaining vector for orchestrator reasoning about PR content — it literally can't reason about content it never sees.

**Future direction: per-PR prompts without batching.** The redirect-prompt model makes the dispatch overhead the only remaining argument for batching. If `pr_context_prepare.py` wrote one prompt file per PR instead of batching into groups of 5, the orchestrator would send 22 redirect strings (~2KB total) instead of 5. The context cost is negligible. The remaining question is whether 22 parallel Agent spawns (~33s dispatch overhead + ~10-15s execution each) would be faster or slower than 5 batched agents (~7s dispatch + ~70s execution). With each agent processing only 1 PR, execution time drops to ~15s per agent, but the ~10 concurrency cap means agents queue in waves. Net wall-clock might be similar (~40-50s vs ~75s), but isolation would be perfect — zero cross-PR contamination risk, individual retry granularity, and simpler prompt templates. Worth testing as run 11.

### Dispatch model comparison (runs 6 vs 7 vs 8 vs 9 vs 10)

The four dispatch models each have distinct trade-offs:

| Aspect | Run 6 (Agent batching) | Run 7 (Skill per-PR) | Run 8 (Skill batching) | Run 9 (prepare script + Agent) | Run 10 (redirect prompts) |
|--------|----------------------|---------------------|----------------------|-------------------------------|--------------------------|
| Tool used | Agent (5 calls × ~4 PRs) | Skill (22 calls × 1 PR) | Skill (5 calls × ~4 PRs) | Agent (5 calls × ~4 PRs) | Agent (5 calls × ~4 PRs) |
| Prompt building | Orchestrator (inline) | Orchestrator (inline) | Orchestrator (inline) | Deterministic script | Deterministic script |
| Prompt delivery | Embedded in Agent call | Embedded in Skill call | Embedded in Skill call | Read file → embed in Agent call | Redirect: "Read {path}" |
| Orchestrator context (Step 5) | ~20KB (templates + metadata) | ~20KB (templates + metadata) | ~20KB (templates + metadata) | ~50KB (prompt file contents) | ~500 bytes (redirect strings) |
| Dispatch mechanism | `run_in_background: true` | Forked execution | Forked execution | Foreground (parallel) | Foreground (parallel) |
| Concurrent execution | Yes (async notifications) | Yes (forked) | Yes (forked) | Yes (parallel Agent calls) | Yes (parallel Agent calls) |
| Result collection | Async (notifications) | Sequential (blocked) | Sequential (blocked) | Parallel (all in one message) | Parallel (all in one message) |
| Inter-dispatch lag | ~1-2s per Agent call | Negligible | Negligible | ~1-2s per Agent call | Near-zero (tiny generation) |
| Cross-PR contamination | Possible within batch | None | Possible within batch | Possible within batch | Possible within batch |
| Total LLM evaluations | 5 (batched) | 22 (individual) | 5 (batched) | 5 (batched) | 5 (batched) |
| Small-batch reliability | Unknown | N/A (always 1) | Failed at 2-key batch | Succeeded at 2-PR batch | Succeeded at 2-PR batch |
| Verdict quality | 12R/6P/4N | 12R/8P/2N | 16R/5P/1N | 15R/7P/0N | 13R/7P/2N |
| YAML quoting failures | 1 | 1 | 1 | 2 | 2 |
| Retries needed | 0 | 0 | 2 (for 1 batch) | 0 | 0 |
| Orchestrator reasoning | Moderate (batching logic) | Low (metadata only) | Moderate (batching logic) | Minimal (read JSON, dispatch) | None (pure redirect) |
| Total pipeline time | ~80s | ~5-6min | ~5-6min | ~2.5min | ~2min |

Key takeaway: run 10's redirect-prompt model completes the trajectory from "orchestrator as content processor" to "orchestrator as pure router." The orchestrator's Step 5 context dropped from ~50KB (run 9, reading prompt files) to ~500 bytes (run 10, redirect strings only). This produced the fastest pipeline time (~2 minutes) and eliminated the last vector for orchestrator reasoning about PR content. The dispatch overhead — previously the dominant bottleneck — shrank because generating 5 short redirect strings is near-instant compared to generating 5 multi-KB embedded prompts. The YAML quoting bug persists across all dispatch models and needs a deterministic fix.

### Dispatch model comparison (runs 6 vs 7, original analysis)

| Aspect | Run 6 (Agent batching) | Run 7 (Skill per-PR) |
|--------|----------------------|---------------------|
| Tool used | Agent (5 calls × ~4 PRs) | Skill (22 calls × 1 PR) |
| Dispatch mechanism | `run_in_background: true` | Forked execution |
| Inter-dispatch lag | ~1-2s per Agent call | Negligible (all in one message) |
| Cross-PR contamination | Possible within batch | None |
| Total LLM evaluations | 5 (batched) | 22 (individual) |
| `jiracontext.md` reads | 5 (once per batch) | 22 (once per PR) |
| Orchestrator context load | Low (metadata only) | Low (metadata only) |
| Verdict quality | 12R/6P/4N | 12R/8P/2N |
| YAML quoting failures | 1 | 1 |

The verdict quality difference is modest and likely within normal LLM variance rather than a systematic effect of the dispatch model. The Skill-based approach trades higher LLM call count and token usage for better isolation and simpler orchestration (no batching logic needed). The Agent-based approach is more token-efficient but requires batch management and carries cross-contamination risk.

The user's key observation — "faster between invocations but sequential overall" — reflects the fundamental difference: Skill tool forking eliminates *dispatch* latency between calls (all sent in one message), but doesn't eliminate *collection* latency (results still serialize). The Agent tool with `run_in_background` has higher *dispatch* latency (per-call overhead) but *collection* is asynchronous (notifications arrive as agents complete, orchestrator doesn't block).

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
- ~~**Prepare script (`pr_context_prepare.py`)**~~ — **done** (run 9). Deterministic script that handles batch grouping, prompt template filling, and noise summary writing. The orchestrator reads the script's JSON output and dispatches pre-built prompt files — zero template logic, zero batching decisions in the orchestrator.
- ~~**Redirect prompts**~~ — **done** (run 10). The orchestrator sends `Read {path} and follow all instructions exactly` instead of reading prompt files into its own context. Agents read their own prompt files. Orchestrator Step 5 context dropped from ~50KB to ~500 bytes, producing the fastest pipeline time (~2 minutes) and eliminating the last vector for orchestrator content reasoning.
- **Per-PR prompts (unbatching)** — now that redirect prompts make orchestrator context cost negligible, the only remaining argument for batching is dispatch overhead. If `pr_context_prepare.py` wrote one prompt file per PR, the orchestrator would send 22 redirect strings (~2KB) instead of 5. Execution time per agent drops from ~70s (4-5 PRs sequentially) to ~15s (1 PR), but the ~10 concurrency cap means agents queue in waves. Net wall-clock might be similar (~40-50s vs ~75s), but isolation is perfect: zero cross-PR contamination, individual retry granularity, simpler templates. The redirect-prompt model makes this architecturally cheap to try.
- ~~**YAML frontmatter quoting enforcement**~~ — **done**. `pr_context_sanitize_yaml.py` runs in Step 6 before the verdict check. Fixes unquoted string values containing colons by line-by-line repair. Idempotent, logs repaired fields. Closes the five-run regression (runs 6-10) permanently without relying on LLM compliance.
- ~~**Minimum batch size guard**~~ — run 8 showed that 2-key Skill batches can misfire, but run 9 succeeded with a 2-PR Agent batch, suggesting the failure was Skill-specific. No longer a priority for Agent-based dispatch.
- **Cypress/E2E test glob expansion** — the current `TEST_GLOBS` miss Cypress-style paths (`packages/cypress/cypress/*.ts`), causing test-only PRs like #7126 to pass through to LLM evaluation without a hint
- **Verdict confidence scoring** — the comparative evaluation produces reasoning; a second pass could score confidence (high/medium/low) based on how close the peripheral vs relevant arguments are
- **Cross-PR deduplication** — PRs that implement the same feature incrementally (e.g., #6747 adds mock endpoints, #6990 replaces them with real ones) could be grouped to avoid redundant documentation impact bullets
