You are evaluating JIRA issue files for documentation relevance.

DOCUMENTATION TARGET (this is what will be documented — use it as your
reference throughout):

---
{the full markdown body from jiracontext.md}
---

INPUT DIRECTORY (source files to evaluate): {input_directory}
OUTPUT DIRECTORY (copy selected files here): {output_directory}
STARTING ISSUE (already copied — do NOT read or evaluate this file): {starting_issue}

Your task: for each .md file in the INPUT directory EXCEPT
{starting_issue}.md, read its body (the markdown content after the YAML
frontmatter closing ---) and decide whether it adds meaningful context
that would help a technical writer DOCUMENT the feature described above.

COPY the file (using cp) into the output directory if its body contains
ANY of:
- Technical details: architecture, API surface, implementation specifics
- Requirements, scope, constraints, or success criteria
- Context a technical writer needs beyond what the documentation target
  already provides

SKIP the file (do not copy) if its body:
- Is empty or contains only whitespace
- Is trivially short with no technical substance
- Contains only project management noise (sprint tracking, assignments,
  status updates) with nothing a writer could use
- Fully duplicates content already in the documentation target without
  adding anything new

Process each file, then report a summary table:

| File | Decision | Reason |
|------|----------|--------|
| ... | COPIED / SKIPPED | one-line reason |
