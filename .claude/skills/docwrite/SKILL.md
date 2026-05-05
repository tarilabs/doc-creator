---
name: docwrite
description: >
  Write documentation from a doc plan into a target documentation
  repository. Discovers the target repo's framework and conventions
  via a profiler agent, then dispatches parallel writer agents to
  produce documentation files. Use after docplan-create has produced
  artifacts/docplan/docplan.md.
disable-model-invocation: true
allowed-tools: Bash(python3 *) Read Agent Write
---

## Step 1 — Prepare

Run the preparation script to parse the docplan, extract per-module
evidence, scan the target repo, and produce prompt files:

```bash
python3 scripts/doc_write_prepare.py
```

If the user provided `--target-repo`, `--draft`, `--docplan`, or
`--doccontext` arguments via `$ARGUMENTS`, pass them through.

Verify exit 0 before continuing. Exit 2 = fatal (report and stop).

Parse the JSON output (last line of stdout) to get:
- `config_path` — path to writer-config.json
- `module_count` — number of modules to write
- `framework` — detected documentation framework
- `modules_dir`, `assemblies_dir` — detected directories
- `mode` — "write" or "draft"
- `modules` — list with slug, type, confidence per module

## Step 2 — Profile target repo

Read `artifacts/docwrite/writer-config.json` to extract:
- `repo_profile.sample_file_paths` — sample files for the profiler to read
- `repo_profile.claude_md_path` — target repo's CLAUDE.md (or null)
- `repo_profile.product_attributes_file` — attributes file (or null)
- `repo_profile.framework` — detected framework
- The set of unique module types from the modules list

Read [profiler-prompt-template.md](profiler-prompt-template.md) and
fill in these placeholders:

- `{target_repo}` — the `target_repo` value from writer-config.json
- `{claude_md_path}` — the claude_md_path, or "not found"
- `{product_attributes_path}` — the product_attributes_file, or "not found"
- `{sample_files_json}` — JSON dict of content-type → absolute file path
- `{module_types}` — comma-separated list of module types needed
- `{output_path}` — absolute path to `artifacts/docwrite/format-reference.md`

Spawn a **single** Agent subagent with the filled prompt.

**Do NOT** read sample files, CLAUDE.md, or the product attributes
file yourself. The profiler agent reads them. Your job is placeholder
substitution only.

## Step 3 — Dispatch writer agents

Read `artifacts/docwrite/writer-config.json` to get the list of modules.

For each module, read [prompt-template.md](prompt-template.md) and fill:
- `{prompt_path}` — absolute path to the module's `.prompt.md` file
- `{reference_path}` — absolute path to `artifacts/docwrite/format-reference.md`
- `{target_path}` — the module's `target_path` from the config

Spawn agents with **model: opus**. **Launch ALL agents in a SINGLE
message.** Send one message containing one Agent tool call per module.
Do NOT wait for any agent to complete before launching others.

**Do NOT** read prompt files, evidence, or the format reference.
**Do NOT** reason about documentation content, JIRA issues, PRs,
or feature scope. Verdict judgment and writing are the agents' jobs.
Your job is mechanical: construct redirect prompts and dispatch.

## Step 4 — Integration proposal

After all writer agents complete, prepare the integration agent.

Read `artifacts/docwrite/writer-config.json` again to get:
- `target_repo`
- `repo_profile.modules_dir`
- `repo_profile.assemblies_dir`
- `repo_profile.framework`
- The list of modules (slug, title, type, target_path)

Read [integrator-prompt-template.md](integrator-prompt-template.md)
and fill:
- `{target_repo}` — target repo path
- `{modules_dir}` — detected modules directory
- `{assemblies_dir}` — detected assemblies directory
- `{framework}` — detected framework
- `{written_modules_json}` — JSON list of written modules
- `{format_reference_path}` — absolute path to format-reference.md
- `{output_path}` — absolute path to `artifacts/docwrite/integration-proposal.md`

Spawn a **single** Agent subagent with the filled prompt.

**Do NOT** read assembly files or navigation files yourself.

After the integrator completes, read `artifacts/docwrite/integration-proposal.md`.

If mode is **write**: apply the integration proposal. For "Files to Create",
write each file. For "Files to Modify", apply the described changes.

If mode is **draft**: do not apply. Report the proposal to the user.

## Step 5 — Verify

Run the verification script:

```bash
python3 scripts/doc_write_verify.py
```

- Exit 0 = all modules pass (no errors, no warnings)
- Exit 1 = warnings only (advisory, do NOT stop)
- Exit 2 = errors found (report to user)

Parse the JSON output to get per-module results.

## Step 6 — Report

Report to the user:

- **Framework detected**: the documentation framework and key conventions
- **Modules written**: count and list of written files with their types
- **Evidence confidence**: per-module confidence ratings
- **Integration proposal**: summary of the integrator's recommendation
  (new assembly, modified assembly, book integration)
- **Verification results**: pass/fail per module, any errors or warnings,
  which checks were performed and which were skipped
- **Next steps**: suggest review (technical review, style review) as
  future pipeline stages
