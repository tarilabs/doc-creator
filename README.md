# doc-creator

Downloads JIRA issues and saves them to disk as markdown, including description, links, and hierarchy (parent/children).

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

## Usage

Fetch a single issue as JSON:

```bash
python scripts/fetch_issue.py RHAISTRAT-123
```

Fetch with markdown-converted description:

```bash
python scripts/fetch_issue.py RHAISTRAT-123 --markdown
```

Fetch all artifacts (description, comments, attachments, linked issues) to disk:

```bash
python scripts/fetch_issue.py RHAISTRAT-123 --fetch-all artifacts/
```

## Tests

```bash
make test
```

Tests use [jira-emulator](https://github.com/jctanner/jira-emulator) — no real Jira credentials needed.
