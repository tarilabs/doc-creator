You are summarizing a pull request for a technical writer. Be BRIEF.

DOCUMENTATION TARGET (this is the feature being documented):

---
{documentation_target}
---

PR TITLE: {pr_title}
PR DESCRIPTION:
{pr_body}

FILTERED PATCH (noise already removed — what remains is potentially meaningful):
```
{filtered_patch}
```

OUTPUT FILE: {output_file}

Write a markdown file to OUTPUT FILE with YAML frontmatter and two sections.

Frontmatter fields:
- pr_url: {pr_url}
- repo: {repo}
- pr_number: {pr_number}
- title: "{pr_title}"
- verdict: relevant | peripheral | noise

Verdict criteria:
- relevant: changes that ADD or ALTER what documentation should say —
  new UI, new API surface, new configuration, changed defaults, removed
  capabilities, or behavior that a tech writer would describe differently
  than before this PR
- peripheral: changes that DON'T change what documentation would say —
  bug fixes restoring already-intended behavior, refactoring, infrastructure,
  plumbing, test-only changes. A tech writer would document the correct
  behavior regardless of whether this PR existed.
- noise: nothing documentation-worthy in the patch

To distinguish relevant from peripheral on bug fixes, ask: "Would the
documentation read differently if this fix never landed?" If no (the docs
describe the intended behavior either way), it's peripheral. If yes (the
fix changes the interface, adds a new parameter, or alters documented
behavior), it's relevant.

## What changed

One short paragraph. Describe what this PR does from a USER or ADMIN
perspective, not implementation details. If it adds UI, say what the
user sees. If it changes an API, say what callers can now do. If it's
pure infrastructure, say so briefly.

## Documentation impact

2-3 bullets maximum. What would a technical writer need to update or add?
Think: new procedures, changed steps, new config options, new UI screens,
changed behavior, removed capabilities.

DOs:
- Focus on WHAT changed for the user, not HOW it was implemented
- Mention new configuration knobs, CLI flags, or environment variables by name
- Note if this is a breaking change or changes default behavior

DON'Ts:
- Don't describe code structure, function names, or module organization
- Don't list every file changed
- Don't speculate about changes outside the patch
- Don't write more than 150 words total
