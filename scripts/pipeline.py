"""Pipeline DAG runner for doc-creator.

Orchestrates the full documentation pipeline as a dependency graph.
Steps run in topological order with automatic parallelism where the
graph allows.  Checkpoint/resume via artifact existence checks.

All stdlib — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import graphlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DOC_CREATOR_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PipelineStep:
    """Immutable step definition. Uses tuples (not lists) so the dependency graph can't be mutated after construction."""

    name: str
    command: str
    produces: str
    depends_on: tuple[str, ...] = ()
    is_dir: bool = False
    fatal_on_nonzero: bool = True


@dataclass
class StepResult:
    name: str
    status: str  # success | skipped | warning | failed
    exit_code: int | None = None
    duration_s: float = 0.0
    log_file: str | None = None


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

STEPS: dict[str, PipelineStep] = {}


def _register(*args, **kwargs):
    s = PipelineStep(*args, **kwargs)
    STEPS[s.name] = s


_register(
    "jira-explore",
    "uv run python scripts/jira_exploration.py {jira_key}",
    produces="artifacts/jiraexploration.md",
    fatal_on_nonzero=False,
)
_register(
    "jira-context",
    'claude -p "/jiracontext-populate" {claude_flags}',
    produces="artifacts/jiracontext.md",
    depends_on=("jira-explore",),
)
_register(
    "pr-context",
    'claude -p "/prcontext-populate" {claude_flags}',
    produces="artifacts/prcontext.md",
    depends_on=("jira-context",),
)
_register(
    "clone-repos",
    "uv run python scripts/clone_code_repos.py",
    produces="artifacts/codecontext",
    depends_on=("jira-context",),
    is_dir=True,
    fatal_on_nonzero=False,
)
_register(
    "doc-context",
    "uv run python scripts/doc_context_bootstrap.py",
    produces="artifacts/doccontext.md",
    depends_on=("pr-context", "clone-repos"),
)
_register(
    "doc-plan",
    'claude -p "/docplan-create" {claude_flags}',
    produces="artifacts/docplan/docplan.md",
    depends_on=("doc-context",),
)
_register(
    "doc-write",
    'claude -p "/docwrite {write_args}" {claude_flags}',
    produces="artifacts/docwrite/writer-config.json",
    depends_on=("doc-plan",),
)
_register(
    "doc-review",
    'claude -p "/docreview" {claude_flags}',
    produces="artifacts/docreview",
    depends_on=("doc-write",),
    is_dir=True,
)


# ---------------------------------------------------------------------------
# DAG helpers
# ---------------------------------------------------------------------------

def build_dag(steps: dict[str, PipelineStep]) -> graphlib.TopologicalSorter:
    ts: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
    for step in steps.values():
        ts.add(step.name, *step.depends_on)
    ts.prepare()
    return ts


def static_order(steps: dict[str, PipelineStep]) -> list[str]:
    ts: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
    for step in steps.values():
        ts.add(step.name, *step.depends_on)
    return list(ts.static_order())


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

class Pipeline:
    def __init__(
        self,
        jira_key: str,
        write_args: str,
        claude_flags: str,
        *,
        resume: bool = False,
        start_from: str | None = None,
        root: Path = DOC_CREATOR_ROOT,
    ):
        self.root = root
        self.resume = resume
        self.start_from = start_from
        self.results: list[StepResult] = []
        self._placeholders = {
            "jira_key": jira_key,
            "claude_flags": claude_flags,
            "write_args": write_args,
        }

    # -- artifact checks ---------------------------------------------------

    def _artifact_exists(self, step: PipelineStep) -> bool:
        path = self.root / step.produces
        if step.is_dir:
            return path.is_dir() and any(path.iterdir())
        return path.is_file() and path.stat().st_size > 0

    # -- skip-set for --start-from -----------------------------------------

    def _compute_skip_set(self) -> set[str]:
        order = static_order(STEPS)
        idx = order.index(self.start_from)
        return set(order[:idx])

    # -- logging ------------------------------------------------------------

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _log(self, msg: str) -> None:
        print(f"{self._ts()} {msg}", flush=True)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        if seconds <= 0:
            return "-"
        s = int(seconds)
        if s < 60:
            return f"{seconds:.1f}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m {s}s"
        h, m = divmod(m, 60)
        return f"{h}h {m}m {s}s"

    def _log_summary(self) -> None:
        self._log("")
        self._log("PIPELINE SUMMARY")
        self._log("=" * 60)
        self._log(f"{'Step':<16} {'Status':<10} {'Exit':<6} {'Duration':<12}")
        self._log("-" * 60)
        for r in self.results:
            exit_str = str(r.exit_code) if r.exit_code is not None else "-"
            dur = self._fmt_duration(r.duration_s)
            self._log(f"{r.name:<16} {r.status:<10} {exit_str:<6} {dur:<12}")
        self._log("-" * 60)
        total = sum(r.duration_s for r in self.results)
        self._log(f"{'TOTAL':<16} {'':<10} {'':<6} {self._fmt_duration(total):<12}")
        self._log("=" * 60)

    # -- step execution -----------------------------------------------------

    @staticmethod
    def _format_tool(name: str, json_str: str) -> str:
        """One-line summary of a tool call."""
        try:
            p = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            return ""
        if name == "Bash":
            cmd = p.get("command", "")
            desc = p.get("description", "")
            s = f"$ {cmd[:120]}" if cmd else ""
            return f"{s}  # {desc}" if desc else s
        if name == "Read":
            return p.get("file_path", "")
        if name in ("Edit", "Write"):
            return p.get("file_path", "")
        if name == "Agent":
            return p.get("description", "")
        if name == "Skill":
            return f"/{p.get('skill', '')}"
        return ""

    async def _tail_log(
        self, path: Path, step_name: str, stop: asyncio.Event
    ) -> None:
        """Tail a log file, parsing stream-json for claude steps."""
        prefix = f"[{step_name}] "
        tool_name: str | None = None
        tool_json = ""
        at_line_start = True

        def _write(text: str) -> None:
            nonlocal at_line_start
            if not text:
                return
            chunks = text.split("\n")
            for i, chunk in enumerate(chunks):
                if at_line_start and chunk:
                    sys.stdout.write(prefix)
                sys.stdout.write(chunk)
                if i < len(chunks) - 1:
                    sys.stdout.write("\n")
                    at_line_start = True
                elif chunk:
                    at_line_start = False
            sys.stdout.flush()

        def _process(line: str) -> None:
            nonlocal tool_name, tool_json, at_line_start

            stripped = line.strip()
            if not stripped or not stripped.startswith("{"):
                _write(line)
                return

            try:
                msg = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                _write(line)
                return

            msg_type = msg.get("type")

            if msg_type == "system":
                sub = msg.get("subtype", "")
                if sub == "api_retry":
                    _write(f"[retry {msg.get('attempt','?')}] {msg.get('error','')}\n")
                elif sub == "compact_boundary":
                    _write("[context compacted]\n")
                elif sub == "status" and msg.get("status") == "requesting":
                    _write("... waiting for API response\n")
                return

            # Skip complete messages — content already shown via stream deltas
            if msg_type in ("assistant", "user", "result"):
                return

            if msg_type != "stream_event":
                return

            event = msg.get("event", {})
            etype = event.get("type")

            if etype == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "?")
                    tool_json = ""

            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                dtype = delta.get("type")
                if dtype == "text_delta":
                    _write(delta.get("text", ""))
                elif dtype == "input_json_delta":
                    tool_json += delta.get("partial_json", "")

            elif etype == "content_block_stop":
                if tool_name:
                    if not at_line_start:
                        sys.stdout.write("\n")
                        at_line_start = True
                    summary = self._format_tool(tool_name, tool_json)
                    _write(f">> {tool_name} {summary}\n")
                    tool_name = None
                    tool_json = ""

        with open(path, "r") as f:
            while not stop.is_set():
                line = f.readline()
                if not line:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=0.2)
                    except asyncio.TimeoutError:
                        pass
                    continue
                _process(line)
            for line in f:
                _process(line)

    async def _run_step(
        self, step: PipelineStep, log_dir: Path
    ) -> StepResult:
        cmd = step.command.format_map(self._placeholders)
        is_claude = "claude " in cmd

        log_path = log_dir / f"{step.name}.log"

        self._log(f"STARTED   {step.name}")

        start = asyncio.get_event_loop().time()
        if is_claude:
            # Write stdout to file directly (no pipe) and tail it.
            # File I/O is unbuffered at the OS level, avoiding the pipe
            # buffering that prevents stream-json events from streaming.
            stderr_path = log_dir / f"{step.name}.stderr.log"
            log_file = open(log_path, "w")
            stderr_file = open(stderr_path, "w")
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=log_file,
                    stderr=stderr_file,
                    cwd=str(self.root),
                )
                log_file.close()
                stop = asyncio.Event()
                tail = asyncio.create_task(
                    self._tail_log(log_path, step.name, stop)
                )
                exit_code = await proc.wait()
                stop.set()
                await tail
            finally:
                if not log_file.closed:
                    log_file.close()
                stderr_file.close()
        else:
            # Stream via pipe for regular scripts
            with open(log_path, "w") as lf:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(self.root),
                )
                prefix = f"[{step.name}] "
                async for raw_line in proc.stdout:
                    line = raw_line.decode(errors="replace")
                    lf.write(line)
                    sys.stdout.write(prefix + line)
                    sys.stdout.flush()
                exit_code = await proc.wait()
        duration = asyncio.get_event_loop().time() - start

        if exit_code == 0:
            status = "success"
        elif exit_code == 2:
            status = "failed"
        elif step.fatal_on_nonzero:
            status = "failed"
        else:
            status = "warning"

        tag = status.upper()
        self._log(f"{tag:<9} {step.name} (exit={exit_code}, {self._fmt_duration(duration)})")

        return StepResult(
            name=step.name,
            status=status,
            exit_code=exit_code,
            duration_s=round(duration, 1),
            log_file=str(log_path),
        )

    # -- main loop ----------------------------------------------------------

    async def run(self) -> int:
        log_dir = self.root / "artifacts" / "pipeline"
        log_dir.mkdir(parents=True, exist_ok=True)

        dag = build_dag(STEPS)
        skip_before = self._compute_skip_set() if self.start_from else set()

        while dag.is_active():
            ready = dag.get_ready()
            to_run: list[PipelineStep] = []

            for name in ready:
                step = STEPS[name]
                if name in skip_before:
                    self._log(f"SKIPPED   {name} (before --start-from)")
                    self.results.append(
                        StepResult(name=name, status="skipped")
                    )
                    dag.done(name)
                elif self.resume and self._artifact_exists(step):
                    self._log(f"SKIPPED   {name} (artifact exists)")
                    self.results.append(
                        StepResult(name=name, status="skipped")
                    )
                    dag.done(name)
                else:
                    to_run.append(step)

            if not to_run:
                continue

            coros = [self._run_step(s, log_dir) for s in to_run]
            batch_results = await asyncio.gather(*coros, return_exceptions=True)

            failed = False
            for step, result in zip(to_run, batch_results):
                if isinstance(result, BaseException):
                    sr = StepResult(
                        name=step.name, status="failed", exit_code=-1
                    )
                    self.results.append(sr)
                    self._log(f"FAILED    {step.name} (exception: {result})")
                    failed = True
                else:
                    self.results.append(result)
                    if result.status == "failed":
                        failed = True
                dag.done(step.name)

            if failed:
                self._log_summary()
                return 1

        self._log_summary()
        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the doc-creator pipeline as a DAG.",
    )
    parser.add_argument(
        "--jira-key",
        required=True,
        help="JIRA issue key (e.g. RHAISTRAT-1084)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--target-repo",
        help="Path to target documentation repository",
    )
    mode.add_argument(
        "--draft",
        action="store_true",
        help="Draft mode: write to artifacts/docwrite/output/",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip steps whose artifacts already exist",
    )
    parser.add_argument(
        "--start-from",
        choices=list(STEPS.keys()),
        metavar="STEP",
        help="Skip all steps before STEP",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.target_repo:
        target_repo = str(Path(args.target_repo).resolve())
        write_args = f"--target-repo {target_repo}"
    else:
        write_args = "--draft"

    claude_flags = (
        "--dangerously-skip-permissions --no-session-persistence"
        " --output-format stream-json --verbose"
        " --include-partial-messages"
    )

    pipeline = Pipeline(
        jira_key=args.jira_key,
        write_args=write_args,
        claude_flags=claude_flags,
        resume=args.resume,
        start_from=args.start_from,
    )
    return asyncio.run(pipeline.run())


if __name__ == "__main__":
    sys.exit(main())
