# JIRA Context: Design Principles and Architecture

## What this is

A two-phase pipeline that takes the raw output of JIRA Exploration and produces a curated, documentation-ready context set. Phase 1 is deterministic (scripts). Phase 2 uses LLM judgment via a Claude Code skill to decide which issue files are worth keeping.

The output is a self-contained `artifacts/jiracontext/` directory with only the issue files that contribute documentation value, and a `jiracontext.md` manifest with classified links and the same body as the exploration manifest.

## The problem it solves

JIRA Exploration downloads everything reachable from a starting issue: the RHAISTRAT, its Epic children, linked UX issues, and all their descriptions. In practice, many of these files add nothing for a technical writer:

- Tracking Epics with empty descriptions ("[DOCS RHAISTRAT-xxx] ...")
- QE validation placeholders
- Sprint-planning artifacts with no technical content

A documentation agent fed all of these wastes context window on noise. The jiracontext phase filters to signal.

## Design: copy-in, not prune

The original design copied all files from jiraexploration to jiracontext, then used an LLM to delete the irrelevant ones. This failed in practice because Claude Code's sandbox restricts `rm` operations outside the project directory, and pytest's temporary directories live in `/var/folders/` on macOS.

The revised design inverts the approach: the bootstrap script creates an empty jiracontext directory with only the starting issue, then the LLM subagent evaluates each remaining file in jiraexploration and copies in only the ones worth keeping. This requires only `cp` and `Read` permissions — both safe operations with no sandbox friction.

**Principle: design for the permissions you have, not the ones you wish you had.** Copy-in is inherently safer than delete-out, and it makes the LLM's job additive rather than destructive.

## The three scripts

### `jira_context_bootstrap.py`

Deterministic setup. Creates the output directory, copies the starting issue file (always relevant — it's what triggered the exploration), and writes the `jiracontext.md` manifest with frontmatter carrying over `starting_issue`, `rhaistrat`, and `hierarchy` from the exploration manifest. Intentionally drops exploration-specific fields like `link_filter` and `pull_requests` (the link script re-derives these from actual content).

### `jira_context_links.py`

Deterministic link extraction. Scans all `.md` files in jiracontext plus the exploration manifest's `pull_requests` field, extracts every URL, and classifies them into three lists:

- **pull_requests** — GitHub PRs and GitLab MRs (matched by `_PR_URL_RE`)
- **code_repositories** — derived from PR URLs (strip `/pull/N` to get repo root) plus any bare repo URLs in content
- **additional_links** — everything else (Google Docs, Miro boards, prototypes)

All lists are deduplicated and sorted. PR URLs never appear in repos, repos never appear in additional links.

**Key insight: PR URLs imply code repositories.** A PR at `github.com/org/repo/pull/42` tells us `github.com/org/repo` is an impacted repository. This derivation is free and catches repos that might not appear as bare URLs anywhere in the content.

### The skill: `jiracontext-populate`

Orchestrates the full flow as a Claude Code skill (invoked via `/jiracontext-populate`):

1. Run bootstrap script
2. Read the manifest and list files in jiraexploration
3. Delegate to a subagent for content evaluation
4. Run link extraction
5. Report results

The skill has `disable-model-invocation: true` because it modifies files — manual invocation only.

## The subagent prompt

The prompt template lives in its own file (`prompt-template.md`) rather than inline in `SKILL.md`. This is the single source of truth: the skill reads it at runtime, and tests read it at test time. No duplication.

The prompt gives the subagent:
- The jiracontext.md body as the **documentation target** (anchored at the top of the prompt)
- The input and output directory paths
- The starting issue key to skip (already copied by bootstrap)
- Clear COPY/SKIP criteria with concrete examples

**Principle: the subagent gets a self-contained prompt.** It doesn't need to discover anything — all context is in the prompt. One read pass through the files, one batch of decisions.

## Skill design: agentskills.io compliance

The skill follows both the [agentskills.io specification](https://agentskills.io/specification) and Claude Code conventions:

| Requirement | How |
|---|---|
| `name` matches directory | `jiracontext-populate/SKILL.md` with `name: jiracontext-populate` |
| `name` format: lowercase, hyphens, no consecutive hyphens | Validated by test |
| `description` under 1024 chars | Describes what and when |
| Body under 500 lines | Validated by test |
| Progressive disclosure | Core instructions in SKILL.md, prompt template in separate file |
| `compatibility` field | Notes Python 3.11+ and Claude Code |

Claude Code extensions used: `disable-model-invocation`, `allowed-tools`.

## Testing strategy

Three tiers, all in `tests/test_jira_context.py`:

### Tier 1: Script tests (deterministic, no LLM)

`TestJiraContextBootstrap` — tests the bootstrap script with pytest's `art_dir` fixture (tmpdir with `artifacts/` subdirectory). Verifies: only starting issue is copied, manifest frontmatter is correct, body preserved, timestamp is fresh, custom output dir works, missing input exits non-zero.

`TestJiraContextLinks` — tests link extraction with known URLs embedded in fixture files. Verifies: PRs from exploration manifest, PRs from context bodies, GitLab MRs, deduplication, repo derivation from PRs, classification exclusivity (PR not in repos, repos not in additional), body preservation.

### Tier 2: Skill YAML validation (deterministic, no LLM)

`TestSkillDefinition` — reads `SKILL.md` and validates agentskills.io compliance: required fields present, name matches directory, name format valid, under 500 lines, referenced script exists, prompt template file exists.

### Tier 3: LLM populate test (requires Claude CLI)

`TestPopulateDecision` — runs `claude -p` with the prompt template against known test fixtures inside the project directory (`.tmp_llm_test/`). Uses `--permission-mode acceptEdits` for sandbox compatibility.

Key design decisions for LLM testing:

- **Class-scoped fixture**: Claude runs once, all 5 test methods assert against the same result. No redundant LLM invocations.
- **Streaming output**: Uses `subprocess.Popen` with line-by-line printing so you can see what Claude is doing in real time (`pytest -s`).
- **Project-local temp directory**: `.tmp_llm_test/` inside the project root, not `/var/folders/`. Claude Code's sandbox allows file operations here.
- **`--permission-mode acceptEdits`**: The key discovery. `--allowedTools` pre-approves tool patterns but doesn't override the sandbox's write restrictions. `acceptEdits` does.
- **Unambiguous fixtures**: Empty-body files and rich-requirements files. The correct decision is obvious enough that LLM non-determinism doesn't cause flaky tests.
- **`@pytest.mark.llm`**: Excluded from fast CI via `pytest -m "not llm"`.

### Test budget

| Tier | Tests | Time | Cost |
|------|-------|------|------|
| 1: Scripts | 17 | ~0.5s | Free |
| 2: Skill YAML | 6 | ~0.1s | Free |
| 3: LLM | 5 | ~20-30s | 1 Claude API call |

## File layout

```
scripts/
  jira_context_bootstrap.py   # Create output dir, copy starting issue, write manifest
  jira_context_links.py       # Extract and classify links into manifest frontmatter

.claude/skills/jiracontext-populate/
  SKILL.md                    # Skill definition (5 steps)
  prompt-template.md          # Subagent prompt template (single source of truth)

tests/
  test_jira_context.py        # All three tiers

artifacts/
  jiracontext.md              # Manifest (YAML frontmatter + description body)
  jiracontext/                # Curated issue files (subset of jiraexploration/)
```

## What's next

Potential directions discussed but not yet implemented:

- **PR content fetching**: download PR descriptions and diffs from the `code_repositories` to give the documentation agent actual implementation context, not just issue descriptions
- **Comment extraction**: JIRA comments sometimes contain architectural decisions or acceptance criteria not in the description
- **Iterative refinement**: re-run the populate step after manual review, adding or removing files from jiracontext
