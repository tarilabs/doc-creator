---
starting_issue: [REPLACE: starting JIRA key]
created_at: [REPLACE: ISO 8601 timestamp, e.g. 2026-05-04T10:00:00Z]
feature_name: [REPLACE: short feature name, e.g. MCP Catalog v1]
dev_preview: true
personas:
- id: [REPLACE: short identifier, e.g. aio-pe]
  name: [REPLACE: display name, e.g. AI Operator / Platform Engineer]
  role: [REPLACE: one-line role description]
module_count: [REPLACE: integer — total number of ### Module: headings below]
acceptance_criteria_coverage:
  total: [REPLACE: integer — number of acceptance criteria from JIRA]
  covered: [REPLACE: integer — number mapped to at least one module]
  unmapped:
  - [REPLACE: list any unmapped acceptance criteria verbatim, or remove this list if all are covered]
---

# Documentation Plan: [REPLACE: feature name]

## Executive Summary

[REPLACE: 2-3 sentences describing what documentation is needed, for whom, and why. Ground this in the JIRA scope and PR evidence. State the primary personas and the user journey this documentation supports.]

## Personas

### [REPLACE: persona name]

**Role:** [REPLACE: what this persona does — their organizational responsibility]

**Documentation needs:** [REPLACE: what tasks they perform with this feature, what decisions they make, what information they need from documentation]

[REPLACE: repeat this ### block for each persona — typically 2-3]

## User Journey

[REPLACE: Narrative description of the end-to-end user journey, organized by phases (e.g. Discover, Deploy, Configure, Experiment). Describe what each persona does at each phase and how the modules below map to this journey. This section is a reading guide — it helps a human reviewer understand how the modules fit together.]

## Planned Modules

### Module: [REPLACE: descriptive module title]

- **Type:** [REPLACE: concept | procedure | reference]
- **Persona:** [REPLACE: persona id from frontmatter]
- **Journey Phase:** [REPLACE: discover | deploy | configure | operate | experiment]
- **Job Statement:** When [REPLACE: situation], I want to [REPLACE: motivation], so I can [REPLACE: outcome]
- **Source Evidence:**
  - JIRA: [REPLACE: comma-separated issue keys, e.g. RHAISTRAT-1084, RHOAIENG-48329]
  - PRs: [REPLACE: comma-separated PR refs with repo prefix, e.g. model-registry#2367, odh-dashboard#6907]
- **Content Outline:**
  - [REPLACE: specific bullet list of what this module covers — topics, not vague descriptions]
- **Prerequisites:** [REPLACE: what the reader needs before this module, or "None"]
- **Dev Preview Disclaimer:** required

[REPLACE: repeat this ### Module: block for each planned module. Aim for 5-15 modules — fewer if the feature is focused, more if it spans many personas and journey phases.]

## PR-to-Topic Mapping

| PR | Repo | Module(s) |
|---|---|---|
| [REPLACE: #number] | [REPLACE: org/repo] | [REPLACE: Module title(s) this PR informs] |

[REPLACE: one row per relevant PR. Every relevant PR should appear here. This table shows evidence traceability — it proves that every module is grounded in implementation, and every relevant PR contributed to a module.]

## Acceptance Criteria Coverage

| # | Acceptance Criterion | Covered By | Status |
|---|---|---|---|
| [REPLACE: number] | [REPLACE: criterion text verbatim from JIRA] | [REPLACE: Module title(s)] | [REPLACE: covered | uncovered] |

[REPLACE: one row per acceptance criterion from the starting JIRA issue. Status is "covered" if at least one module addresses it; "uncovered" otherwise. If any criterion is uncovered, explain why in Open Questions.]

## Prerequisite Gaps

[REPLACE: List hard prerequisites that the code assumes exist but that are NOT covered by any planned module and have no PR evidence in this evidence set. These are real dependencies — not aspirational features — that users need before they can complete documented procedures.

For each gap:
- **Name**: what the prerequisite is
- **Blocking modules**: which planned modules depend on it
- **Evidence**: what JIRA text, code gating condition, or PR reference indicates this exists
- **Severity**: blocking (user cannot proceed without it) or informational (workaround exists)
- **Recommended action**: what to do next (consult SME, obtain install procedure, check other repos)

If no prerequisite gaps exist, write "No prerequisite gaps identified."]

## Deferred Topics

[REPLACE: List features explicitly marked as deferred or out-of-scope in the JIRA. For each item, include:
- The feature or capability name
- The JIRA source (which issue says it's deferred)
- Why it's deferred (e.g. "post-Summit", "requires registry integration")

These are NOT planned as modules. They exist here to document the boundary and prevent future pipeline runs from re-discovering them.]

## Unverified Topics

[REPLACE: List features mentioned in JIRA requirements but with NO supporting PR evidence. For each item, include:
- The feature or capability name
- The JIRA source (which issue mentions it)
- Recommended action (wait for implementation, consult SME, check related repos)

These are NOT planned as modules. Planning without evidence risks documenting features that don't exist.]

## Open Questions

[REPLACE: List things that couldn't be resolved from available evidence. Examples:
- Contradictions between JIRA issues (e.g. gateway in scope vs. excluded from dev preview)
- Ambiguous scope boundaries
- Features where PR evidence is unclear
- Missing context that a human reviewer should provide

Each question should note what additional information would resolve it.]
