from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .proc_stream import run_streaming_process
from .interrupts import InterruptConfig, should_interrupt_command
from ..core.storage import now_rfc3339
from ..runtime.live import render_codex_event
from ..runtime.transcript_store import write_transcript_header


def _is_inside_git_repo(start_dir: Path) -> bool:
    cur = start_dir.resolve()
    while True:
        if (cur / ".git").exists():
            return True
        if cur.parent == cur:
            return False
        cur = cur.parent


def _build_codex_base_args(project_root: Path) -> list[str]:
    # Keep only global options here. `--skip-git-repo-check` is an `exec` option.
    return ["codex", "--cd", str(project_root)]


def _append_common_exec_options(
    args: list[str],
    *,
    skip_git_repo_check: bool,
    full_auto: bool,
    sandbox: str | None,
    output_schema_path: Path | None,
) -> None:
    if skip_git_repo_check:
        args.append("--skip-git-repo-check")
    if full_auto:
        args.append("--full-auto")
    if sandbox:
        args.extend(["--sandbox", sandbox])
    args.append("--json")
    if output_schema_path:
        args.extend(["--output-schema", str(output_schema_path)])


@dataclass(frozen=True)
class CodexRunResult:
    thread_id: str
    exit_code: int
    events: list[dict[str, Any]]
    raw_transcript_path: Path

    def last_agent_message(self) -> str:
        last = ""
        for ev in self.events:
            if ev.get("type") == "item.completed" and isinstance(ev.get("item"), dict):
                item = ev["item"]
                if item.get("type") == "agent_message":
                    last = str(item.get("text") or "")
        return last

    def iter_command_executions(self) -> Iterable[dict[str, Any]]:
        for ev in self.events:
            if ev.get("type") == "item.completed" and isinstance(ev.get("item"), dict):
                item = ev["item"]
                if item.get("type") == "command_execution":
                    yield item


def run_codex_exec(
    *,
    prompt: str,
    project_root: Path,
    transcript_path: Path,
    full_auto: bool,
    sandbox: str | None,
    output_schema_path: Path | None,
    interrupt: InterruptConfig | None = None,
    live: bool = False,
    hands_raw: bool = False,
    redact: bool = False,
    on_live_line: Callable[[str], None] | None = None,
) -> CodexRunResult:
    skip_git_repo_check = not _is_inside_git_repo(project_root)
    args = _build_codex_base_args(project_root)
    args.append("exec")
    _append_common_exec_options(
        args,
        skip_git_repo_check=skip_git_repo_check,
        full_auto=full_auto,
        sandbox=sandbox,
        output_schema_path=output_schema_path,
    )
    # Read prompt from stdin to avoid shell escaping/length issues.
    args.append("-")

    write_transcript_header(
        transcript_path,
        {
            "ts": now_rfc3339(),
            "kind": "exec",
            "cwd": str(project_root),
            "argv": args,
        },
    )

    return _run_codex_process(
        args=args,
        stdin_text=prompt,
        transcript_path=transcript_path,
        interrupt=interrupt,
        live=bool(live),
        hands_raw=bool(hands_raw),
        redact=bool(redact),
        on_live_line=on_live_line,
    )


def run_codex_resume(
    *,
    thread_id: str,
    prompt: str,
    project_root: Path,
    transcript_path: Path,
    full_auto: bool,
    sandbox: str | None,
    output_schema_path: Path | None,
    interrupt: InterruptConfig | None = None,
    live: bool = False,
    hands_raw: bool = False,
    redact: bool = False,
    on_live_line: Callable[[str], None] | None = None,
) -> CodexRunResult:
    skip_git_repo_check = not _is_inside_git_repo(project_root)
    args = _build_codex_base_args(project_root)
    args.extend(["exec", "resume"])
    _append_common_exec_options(
        args,
        skip_git_repo_check=skip_git_repo_check,
        full_auto=full_auto,
        sandbox=sandbox,
        output_schema_path=output_schema_path,
    )
    args.append(thread_id)
    args.append("-")

    write_transcript_header(
        transcript_path,
        {
            "ts": now_rfc3339(),
            "kind": "resume",
            "thread_id": thread_id,
            "cwd": str(project_root),
            "argv": args,
        },
    )

    return _run_codex_process(
        args=args,
        stdin_text=prompt,
        transcript_path=transcript_path,
        interrupt=interrupt,
        live=bool(live),
        hands_raw=bool(hands_raw),
        redact=bool(redact),
        on_live_line=on_live_line,
    )


def _run_codex_process(
    *,
    args: list[str],
    stdin_text: str,
    transcript_path: Path,
    interrupt: InterruptConfig | None,
    live: bool,
    hands_raw: bool,
    redact: bool,
    on_live_line: Callable[[str], None] | None,
) -> CodexRunResult:
    events: list[dict[str, Any]] = []
    thread_id: str | None = None
    def _on_line(
        stream_name: str,
        line: str,
        emit: Callable[[str], None],
        _append_meta: Callable[[str], None],
        request_interrupt: Callable[[str], None],
    ) -> None:
        nonlocal thread_id

        # Best-effort live rendering:
        # - stdout: Codex emits JSON event objects (one per line).
        # - stderr: surface raw stderr lines (often useful in failures).
        if stream_name != "stdout":
            if hands_raw:
                emit(f"[hands:{stream_name}] {line}")
            else:
                if line.strip():
                    emit(f"[hands:stderr] {line}")
            return

        ev: dict[str, Any] | None = None
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                ev = parsed

        if hands_raw:
            emit(f"[hands:stdout] {line}")

        if ev is None:
            if (not hands_raw) and line.strip():
                emit(f"[hands:stdout] {line}")
            return

        events.append(ev)
        if ev.get("type") == "thread.started" and isinstance(ev.get("thread_id"), str):
            thread_id = ev["thread_id"]
        if interrupt and ev.get("type") == "item.started":
            item = ev.get("item")
            if isinstance(item, dict) and item.get("type") == "command_execution":
                cmd = str(item.get("command") or "")
                if should_interrupt_command(interrupt.mode, cmd):
                    request_interrupt(f"mi.interrupt.requested=1 mode={interrupt.mode} command={cmd}")

        if not hands_raw:
            for out_line in render_codex_event(ev):
                if out_line is None:
                    continue
                s = str(out_line)
                if not s.strip():
                    continue
                emit(f"[hands] {s}")

    exit_code, _duration_ms = run_streaming_process(
        argv=args,
        stdin_text=stdin_text,
        transcript_path=transcript_path,
        cwd=None,
        env=None,
        interrupt=interrupt,
        exit_meta_prefix="mi.codex",
        live=bool(live),
        redact=bool(redact),
        on_live_line=on_live_line,
        on_line=_on_line,
        start_timer_before_popen=True,
    )

    if not thread_id:
        # Still persist transcript; return a placeholder id so callers can treat this as failed.
        thread_id = "unknown"

    return CodexRunResult(
        thread_id=thread_id,
        exit_code=exit_code,
        events=events,
        raw_transcript_path=transcript_path,
    )
