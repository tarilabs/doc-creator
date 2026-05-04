# JIRA Exploration: Design Principles and Architecture

## What this is

A utility that, given a single JIRA issue key, explores the JIRA hierarchy to build a complete picture of a strategy and its implementation — downloading issues as markdown, walking parent chains to find the owning RHAISTRAT, discovering child Epics, and collecting all PR URLs from engineering tasks underneath.

The output is a self-contained `artifacts/jiraexploration/` directory with one markdown file per issue and a `jiraexploration.md` manifest with YAML frontmatter summarizing the exploration.

## Starting point: aggressive cleanup

The repository originally contained a full strategy-creation pipeline with scoring, pushing, pulling, locking, dashboards, and reports. We stripped it down to the two files that matter for JIRA-to-markdown: `jira_utils.py` (HTTP client, ADF-to-markdown conversion) and `fetch_issue.py` (fetch and write). Everything else — 16 Python scripts, 4 shell scripts, 16 test files — was deleted. This made the codebase small enough to reason about and evolve quickly.

**Principle: delete ruthlessly before building.** A smaller codebase is easier to change than a larger one with "keep it, we might need it" dead code.

## Key architectural decisions

### Flat directory, one file per issue

Early versions used subdirectories (`rfe-tasks/`, `rfe-originals/`) and combined files (`-links.md`, `-comments.md`). We replaced all of this with a flat directory where each issue gets exactly one `{KEY}.md` file. This makes the output trivial to browse, grep, and feed to downstream tools.

### YAML frontmatter as structured metadata

Each markdown file carries a YAML frontmatter block with `jira_key`, `summary`, `issue_type`, `git_pull_requests`, and `links`. The body is the JIRA description converted to markdown. This separation means structured data (for machines) and narrative content (for humans and LLMs) coexist cleanly in the same file.

### The manifest as the exploration's memory

`artifacts/jiraexploration.md` is written with YAML frontmatter recording: the starting issue, the discovered RHAISTRAT, the hierarchy chain, and all collected PR URLs. The body contains the starting issue's description. This file is the single artifact that answers "what was explored, from where, and what did we find?" — without having to re-read every downloaded issue.

### Single issue key as entry point

The script takes exactly one issue key. It doesn't batch multiple unrelated issues — that would complicate the manifest and hierarchy semantics. If you need to explore multiple STRATs, you run the script multiple times. Simple tools compose better than complex ones.

## Efficiency strategy: minimizing API calls

This was the hardest design problem. A RHAISTRAT can have 7+ Epic children, each with 10-40 engineering tasks. Fetching each individually would mean 100+ sequential API calls.

### Insight 1: `parent = {key}` JQL replaces child-by-child fetching

Instead of fetching each child issue individually, one `search_issues` call with `parent = {key}` returns all children at once. This is used in `_fetch_all` for STRAT children and in `_collect_strat_prs` for Epic grandchildren.

### Insight 2: `parent in (KEY1,KEY2,...)` batches across Epics

Real Jira supports `parent in (...)` which fetches ALL grandchildren across ALL Epics in a single JQL call. For RHAISTRAT-1084 (98 grandchildren), this turns 7 JQL calls into 1. The jira-emulator doesn't support this syntax, so the code falls back to per-Epic queries with a warning.

### Insight 3: search_issues returns custom fields

The JQL search API returns `customfield_10875` (Git Pull Request) as full ADF when requested in the `fields` parameter. This means we can extract PR URLs from grandchildren without calling `get_issue` on each one — the search result already contains everything we need.

### Insight 4: detect Epics from disk, not from API

After `_fetch_all` writes the STRAT's children to disk, we read their frontmatter to find which ones are Epics. This costs zero API calls — the data is already on disk with `issue_type` in the frontmatter. This is why we added `issuetype` to the `get_issue` fields and wrote it to frontmatter.

### Insight 5: PR URLs live in three places

1. `customfield_10875` — the "Git Pull Request" field (ADF with `inlineCard` nodes)
2. The issue description — may contain GitHub/GitLab URLs inline
3. Remote links — separate API endpoint, not available via search

We extract from sources 1 and 2 during the batch search (free). Source 3 would require per-issue API calls and real-world data showed 0 out of 10 sampled grandchildren had remote links, so we skip it.

### API call budget

| Phase | Calls | What |
|-------|-------|------|
| Parent walk | 1-3 | `get_issue` per hop up the chain |
| `_fetch_all` for STRAT | ~10 | 1 main + N children (each: get_issue + remotelink) |
| `_fetch_all` for starting issue | 1-2 | If not already written |
| Epic detection | 0 | Read from disk |
| Grandchild PR collection | 1 | Batch JQL search |

Total for RHAISTRAT-1084 (7 Epics, 98 grandchildren): ~15 API calls instead of ~200.

## The `.env` loading approach

We needed credentials (`JIRA_SERVER`, `JIRA_USER`, `JIRA_TOKEN`) to be loadable from a `.env` file for agent/skill invocation, but didn't want to add `python-dotenv` as a dependency. A 12-line `_load_dotenv()` function in `jira_utils.py` reads `KEY=VALUE` lines from `.env` in the current working directory, skips comments, strips quotes, and never overrides already-set environment variables.

**Principle: keep dependencies minimal.** A stdlib-only `.env` parser is better than a PyPI dependency for this use case.

## Testing strategy

### The jira-emulator as a first-class test dependency

Every integration test runs against the [jira-emulator](https://github.com/jctanner/jira-emulator) — a FastAPI app that emulates the Jira REST API v2/v3 with an in-memory SQLite database. Tests create issues via the admin import API, run the scripts as subprocesses, and assert on the output files. No mocks, no real Jira credentials needed in CI.

### Contributing upstream when the emulator falls short

The emulator was missing two capabilities we needed:
1. **Remote links** — no model, no routes, no storage
2. **Custom field auto-creation** — unknown `customfield_*` keys were silently dropped

Rather than maintaining permanent workarounds in our test fixtures, we cloned the emulator, added the features (RemoteLink model + CRUD router, auto-creation in create/update/import), wrote 11 tests, and opened a PR. This let us remove all the hacks from `conftest.py`.

**Principle: fix the tool, don't work around it permanently.** Temporary workarounds are fine for unblocking; permanent ones accumulate into a second codebase you have to maintain.

### What the tests cover

| Test file | Tests | What |
|-----------|-------|------|
| `test_fetch_issue.py` | 11 | JSON output, markdown conversion, field filtering, frontmatter (key, summary, PRs, remote links, absence of both), missing credentials |
| `test_jira_exploration.py` | 4 | STRAT starting point, parent walk to STRAT, no-STRAT-found path, PR collection from Epic grandchildren |
| `test_markdown_adf.py` | 55 | ADF-to-markdown and markdown-to-ADF round-trip, normalization, metadata stripping |

## File layout

```
scripts/
  jira_utils.py          # HTTP client, ADF conversion, .env loading
  fetch_issue.py         # _write_issue_md, _fetch_all (core fetching)
  jira_exploration.py    # Entrypoint: parent walk, STRAT fetch, PR collection

tests/
  conftest.py            # jira-emulator fixtures, JiraHelper
  test_fetch_issue.py    # fetch_issue.py integration tests
  test_jira_exploration.py # jira_exploration.py integration tests
  test_markdown_adf.py   # ADF conversion unit tests

artifacts/
  jiraexploration.md     # Manifest (YAML frontmatter + description body)
  jiraexploration/       # One {KEY}.md per downloaded issue
```

## What's next

Potential directions discussed but not yet implemented:
- **Parent traversal**: walk UP from the starting issue to fetch the parent chain (not just detect it)
- **Sibling discovery**: find sibling issues under the same parent
- **Remote link collection on grandchildren**: per-issue API calls for remote links (expensive, only if data shows they carry valuable URLs)
