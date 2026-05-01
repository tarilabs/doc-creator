# doc-creator

Multi-agent pipeline for bulk data processing: starting from JIRA issues, pull requests, and code repositories, to write (add, change, amend) technical product documentation.

## Tooling

This project uses **uv** exclusively for dependency management and script execution. Always use `uv run` to run Python scripts and tests, and `uv sync` to install dependencies. Never use bare `python`, `pip install`, or `python -m pytest`.

```bash
uv sync                        # install deps
uv run pytest tests/ -v        # run tests (exclude LLM: -m 'not llm')
uv run python scripts/...      # run any script
```

## Project structure

```
scripts/           # Deterministic Python scripts (the "buttons")
.claude/skills/    # Claude Code skills (orchestrate scripts + subagents)
artifacts/         # Pipeline output — the source of truth for pipeline state
docs/              # Design docs and architecture rationale
tests/             # Pytest tests using jira-emulator (no real JIRA needed)
```

## Pipeline principles

### If it isn't on disk, it doesn't exist

When examining a pipeline run, rely on files in `artifacts/`. The manifest files (`jiraexploration.md`, `jiracontext.md`) with YAML frontmatter are the pipeline's memory. Don't infer state from conversation context — read the files.

### Compaction doesn't just drop data, it changes behavior

Keep agent/LLM invocations isolated to minimize the chance of context compaction occurring. Each subagent should receive a self-contained prompt with all the context it needs, not rely on conversation history.

### Yesterday's context is today's bug

When writing agents or skills: isolate context, trust nothing, start fresh. A subagent gets a self-contained prompt — it doesn't discover, it doesn't remember prior runs.

### Constrain creativity — prefer buttons over bag of parts

Skills must use scripts for deterministic phases and delegate to subagents for LLM judgment. Scripts are the "buttons" — they do one thing predictably. The skill orchestrates which buttons to press and in what order.

### Don't leak internals — Claude will use them

Never expose script internals (implementation details, internal data structures, error stack traces) into agent/LLM context. When something fails, surface the failure cleanly — don't hand Claude a screwdriver to rewire the button panel. Scripts should exit with clear status codes and short error messages, not tracebacks.

### Be liberal in what you accept, be conservative in what you send

Cast a wide net when gathering context (JIRA issues, PRs, code). Synthesize aggressively in each phase so downstream consumers get signal, not noise. Each pipeline stage should reduce volume while preserving meaning.

### Give it the destination, not the directions

When planning modifications to this project (Plan mode), state the declarative intent — what the goal is and what invariants must hold — not step-by-step procedures. The implementation finds the path; the plan defines the destination.

### An agent that compares options reasons better than one asked for its best guess

When designing agent prompts, prefer evaluation of options over asking for a single answer. Present the agent with choices to assess rather than asking it to generate from scratch.

### Deterministic guardrails encode judgments the agent can't make about itself

When defining or changing skills/agents for documentation writing, always consider which adversarial review (skill/agent) is needed in the overall pipeline. The author can't review itself.

### Define invariants — or the agent will "optimize"

Every skill and agent must have explicit DOs and DON'Ts. Without constraints, agents will "helpfully" optimize in ways that break the pipeline.

### Constrain tools, restrict permissions, limit context

When writing agents/skills: grant only the tools needed, restrict file access to relevant directories, and limit context to what the task requires. More access means more ways to go wrong.

### Save everything — you don't know what you'll need

Be considerate that the developer may not have thought about all artifacts worth saving. But don't save things for the sake of it — every saved artifact must be functional to the overall documentation goal.

### The agents that create the complexity are also the fastest way to see through it

Always be on the lookout for pipeline stages that aren't producing the expected output. When something drifts, investigate immediately — don't accumulate silent failures.

## Avoid MCP

Do not use MCP servers. Use helper scripts in `scripts/` instead. Scripts are testable, auditable, and don't leak context.

## Testing

Tests use [jira-emulator](https://github.com/jctanner/jira-emulator) — no real JIRA credentials needed. LLM-dependent tests are marked `@pytest.mark.llm` and excluded from fast CI with `-m 'not llm'`.

```bash
uv run pytest tests/ -v --tb=short           # all tests except LLM
uv run pytest tests/ -v -m llm               # LLM tests only
```

## Environment variables

JIRA connectivity requires `JIRA_SERVER`, `JIRA_USER`, `JIRA_TOKEN` — either exported or in a `.env` file at the repo root. Scripts load `.env` automatically; shell variables take precedence.
