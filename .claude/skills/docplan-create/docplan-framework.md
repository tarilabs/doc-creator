# Documentation Planning Framework

## Overview

This framework guides the creation of documentation plans for enterprise
software features. It is adapted from the Jobs to Be Done (JTBD)
methodology, tailored for features that have already been through
requirements gathering, code review, and context assembly.

The planner receives pre-analyzed inputs: JIRA requirements with scope
boundaries, PR summaries with verdicts and documentation-impact sections,
and code repository locations. The planner's job is synthesis and
organization, not discovery.

---

## Methodology

### 1. Understand the Feature Scope

Read the Feature Overview first — it is the starting issue body and the
authoritative scope document. Then read the remaining sections. Identify:

- **What the feature does** — problem statement, goals, acceptance criteria
- **What's IN scope** for v1 — the features to document
- **What's DEFERRED** — the deny-list of features NOT to document
- **Contradictions** between JIRA issues — flag these in Open Questions

Pay special attention to scope boundaries. The JIRA often has both
aspirational scope (what was originally planned) and actual scope (what
was delivered). The PRs tell you what was actually built. When scope and
implementation diverge, trust the PRs.

### 2. Identify Personas

If the planner input contains a **UX Context** section, start there —
UX issues are the richest source for personas, job stories, and user
flows. They often contain ready-made persona definitions and interaction
details that should be preferred over synthesized ones.

Extract personas from the JIRA context. For enterprise platform features,
personas are typically:

- **Operators / Administrators** (AIO, PE): deploy, configure, and manage
  infrastructure. They create resources, set policies, and control access.
- **Engineers / Developers** (AIE): consume services, build on APIs, and
  use tools in their workflows. They discover capabilities and integrate them.

**CRITICAL RULE — Persona Separation:**

When a feature serves multiple personas with different workflows, you
MUST create SEPARATE modules for each persona. Do NOT merge operator
procedures with developer procedures into a single module.

Evidence that personas need separation:
- Different RBAC roles or permissions required
- Different UI paths, tools, or interfaces used
- Different goals or outcomes
- Different prerequisite knowledge

A single feature like "deploy an MCP server" may need:
- A **procedure for operators** (how to deploy from the Catalog)
- A **concept for engineers** (what MCP servers are available to them)
- A **procedure for engineers** (how to connect to a deployed MCP server)

### 3. Map the User Journey

Organize the plan around the end-to-end user journey as defined in the
feature scope. A typical journey follows these phases:

1. **Discover** — finding and understanding what's available
2. **Deploy** — installing, activating, or provisioning
3. **Configure** — customizing settings, integrations, and policies
4. **Operate** — day-to-day usage and consumption
5. **Experiment** — interactive exploration, testing, and validation

Not every feature covers all phases. Map only phases that have PR evidence.
If no PRs implement the "configure" phase, don't plan a configuration
module — list it under Unverified Topics instead.

### 4. Synthesize PR Evidence into Topics

Group related PRs into documentation topics. A **topic** is a coherent
unit of documentation that covers a single user goal.

For each topic:

1. Merge "what changed" descriptions from related PRs into a narrative
2. Compile documentation-impact bullets into a content outline
3. Identify the primary persona
4. Assign a journey phase
5. Note which PRs provide the evidence

**Peripheral PRs** refine existing behavior (bug fixes, sort order,
microcopy). Attach their gists to the relevant topic as details to
mention — do NOT create separate topics for them.

**Cross-repo topics:** When a user goal spans multiple repositories
(e.g., backend API + frontend UI), merge the evidence into one topic
organized by user task, not by codebase.

### 5. Write Job Statements

For each planned module, write a job statement:

> When [situation], I want to [motivation], so I can [outcome].

- The **situation** comes from the persona's context and role
- The **motivation** comes from the feature's goals
- The **outcome** comes from the acceptance criteria

If the JIRA already contains job stories, reuse and refine them rather
than inventing new ones. UX issues are the most likely source for
ready-made job stories — check the UX Context section first when it
exists. The JIRA's phrasing is often closer to real user language.

### 6. Classify Module Types

Each module has exactly one type:

- **concept**: Explains what something is, why it matters, and how it fits
  into the larger system. No step-by-step instructions. Use for:
  - Architecture overviews
  - Feature introductions ("What is the MCP Catalog?")
  - Terminology and mental models
  - Security and access control explanations

- **procedure**: Step-by-step instructions for accomplishing a task. Each
  step is a single user action. Use for:
  - Deployment and installation
  - Configuration and customization
  - Operational tasks (create, delete, modify, connect)
  - Troubleshooting workflows

- **reference**: Specifications, schemas, API surfaces, configuration
  options, and parameter lists. Organized for lookup, not linear reading.
  Use for:
  - CRD and CR schemas
  - Configuration file formats and environment variables
  - RBAC roles and permissions
  - API endpoints and parameters

### 7. Check Coverage

After drafting all modules, verify:

- Every acceptance criterion from the JIRA maps to at least one module
- The user journey has no gaps (can a user go from discovery to daily
  use following only the planned modules?)
- Both personas have their needs covered
- No deferred features appear in the planned modules
- For discovery/catalog/marketplace features: the documentation notes
  that pre-loaded content ships out of the box (e.g., "the Catalog
  includes pre-loaded MCP servers"). Do NOT plan an inventory of
  specific items — just ensure users know defaults exist.

### 8. Trace Prerequisite Dependencies

For each **procedure** module, trace the prerequisite chain backward:

- What must be **installed** before the user can start? (operators,
  CRDs, CLI tools)
- What must be **configured** before the procedure works? (ConfigMaps,
  registrations, feature flags)
- What **gating conditions** appear in the code? (buttons disabled
  without a CRD, features hidden behind a flag)

Cross-reference these prerequisites with the planned modules. If a
prerequisite is covered by an existing module, it's fine. If it is NOT
covered and has no PR evidence in the evidence set, create an entry in
the **Prerequisite Gaps** section of the plan.

A prerequisite gap is NOT the same as an unverified topic. Unverified
topics are aspirational features with no code. Prerequisite gaps are
**real dependencies that the code assumes exist** — they just weren't
in the PR evidence set because they were implemented elsewhere or are
external to the feature.

Each prerequisite gap entry must include:
- **Name**: what the prerequisite is
- **Blocking modules**: which planned modules depend on it
- **Evidence**: what JIRA text, code gating condition, or PR reference
  indicates this prerequisite exists
- **Severity**: `blocking` (user cannot complete the procedure without
  it) or `informational` (user can work around it)
- **Recommended action**: install procedure from engineering, consult
  SME, check related repos

### 9. Handle Evidence Gaps

Features mentioned in JIRA but with **no supporting PRs** should be
listed in "Unverified Topics" with:

- The JIRA key that mentions them
- Why they're unverified (no code evidence found)
- Recommended action (wait for implementation, consult SME, check
  related repos)

NEVER plan a full module for an unverified feature. This prevents
documenting aspirational features as if they already exist.

---

## Scope Guard Rules

These items are EXPLICITLY out of scope. Do NOT create modules for any of:

- Items listed under "Out of Scope" or "Deferred" in any JIRA issue
- Features described only in "Future Direction" or "Post-Summit" sections
- Capabilities that no PR implements
- Governance, lifecycle management, version pinning, federation, or
  registry integration (unless PR evidence shows otherwise)

If a topic partially overlaps with deferred scope, document ONLY the
implemented portion and note what's deferred.

---

## Dev Preview Requirements

If the feature is a Developer Preview or Technology Preview, every
planned module must note that a disclaimer is required. Set
`dev_preview_disclaimer: required` in each module specification.

This ensures downstream writers include the appropriate technology
maturity notice.

---

## Anti-Proliferation Rule

Before creating a new module, check whether the user's goal fits under
an existing module. Multiple PRs that enhance the same workflow should
be merged into one module, not split into many.

Ask: "Would a user look for this information in the same place as
module X?" If yes, merge them.

A documentation plan with 20 thin modules is worse than one with 8
coherent modules. Users navigate by goal, not by PR.

---

## Self-Verification Checklist

Before saving the plan, verify ALL of these:

1. No `[REPLACE:]` placeholder markers remain anywhere in the output
2. Every acceptance criterion from the JIRA maps to at least one module
3. No module covers deferred or out-of-scope features
4. Persona separation is enforced (operators and engineers have
   separate modules where their workflows differ)
5. Every module has source evidence (JIRA keys and/or PR numbers)
6. User journey is complete (all implemented phases are covered)
7. Module types are appropriate (concepts explain, procedures guide,
   references list)
8. No hallucinated features (nothing claimed beyond what's in evidence)
9. Peripheral PR details are incorporated into relevant modules,
   not created as separate topics
10. Dev Preview disclaimer is marked on all modules
11. Every procedure module's prerequisites are satisfied — either by
    another module or by a Prerequisite Gaps entry. No procedure should
    leave users stuck at an undocumented dependency.
12. For catalog/marketplace features: the documentation notes that
    pre-loaded content exists, without listing specific items
