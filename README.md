# doc-creator

TODO: will need revision

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management

Install dependencies:

```bash
make install
```

## Environment Variables

The scripts require three environment variables for Jira connectivity. You can either export them in your shell or place them in a `.env` file at the repository root — the scripts will load it automatically if present (shell variables take precedence).

| Variable | Description | Example |
|---|---|---|
| `JIRA_SERVER` | Jira server base URL | `https://issues.redhat.com` |
| `JIRA_USER` | Jira username or email | `user@redhat.com` |
| `JIRA_TOKEN` | Jira API token (personal access token) | `ABCdef123...` |

Export directly:

```bash
export JIRA_SERVER="https://issues.redhat.com"
export JIRA_USER="user@redhat.com"
export JIRA_TOKEN="your-api-token"
```

Or create a `.env` file in the repo root:

```
JIRA_SERVER=https://issues.redhat.com
JIRA_USER=user@redhat.com
JIRA_TOKEN=your-api-token
```

The `.env` file is gitignored. If any of the three variables is missing, the scripts exit with code 2.

## JIRA Exploration

Given a single issue key, explores the JIRA hierarchy to build a complete picture of a strategy and its implementation:

- Walks up the parent chain to find the owning RHAISTRAT
- Downloads the STRAT, its children, and filtered linked issues as individual markdown files
- Collects PR URLs from engineering tasks under Epic children, using optimized batch JQL calls to minimize API round-trips
- Writes a manifest (`artifacts/jiraexploration.md`) with YAML frontmatter summarizing the hierarchy, PR URLs, and starting issue description

```bash
python scripts/jira_exploration.py RHOAIENG-53404
python scripts/jira_exploration.py RHAISTRAT-1084 --link-filter UX
```

Output: `artifacts/jiraexploration/` with one `{KEY}.md` per issue and a manifest at `artifacts/jiraexploration.md`.

For architecture details and design rationale, see [docs/jiraexploration.md](docs/jiraexploration.md).

## JIRA Context

Takes the raw exploration output and produces a curated, documentation-ready context set. A bootstrap script copies the starting issue and writes a manifest, then a Claude Code skill (`/jiracontext-populate`) uses an LLM subagent to evaluate each remaining issue file and copy in only those with meaningful documentation content. A link extraction script classifies all URLs into pull requests, code repositories (derived from PR URLs), and additional links.

```bash
# Full flow via the skill:
/jiracontext-populate

# Or run the scripts individually:
python scripts/jira_context_bootstrap.py
python scripts/jira_context_links.py
```

Output: `artifacts/jiracontext/` with curated issue files and a manifest at `artifacts/jiracontext.md`.

For design rationale, the copy-in vs prune decision, testing strategy (including LLM tests), and agentskills.io compliance details, see [docs/jiracontext.md](docs/jiracontext.md).

## Low-level usage

Fetch a single issue as JSON:

```bash
python scripts/fetch_issue.py RHAISTRAT-123
```

Fetch with markdown-converted description:

```bash
python scripts/fetch_issue.py RHAISTRAT-123 --markdown
```

Fetch an issue and all linked/child issues as markdown:

```bash
python scripts/fetch_issue.py RHAISTRAT-123 --fetch-all artifacts/jiraexploration
```

## Tests

```bash
make test
```

Tests use [jira-emulator](https://github.com/jctanner/jira-emulator) — no real Jira credentials needed.
