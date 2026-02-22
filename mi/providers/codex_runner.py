from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .interrupts import InterruptConfig, compute_escalation_delays_ms, escalation_delay_s_for_step, signal_from_name, should_interrupt_command
from ..core.storage import now_rfc3339
from ..core.redact import redact_text
from ..runtime.live import render_codex_event
from ..runtime.transcript_store import append_transcript_line, write_transcript_header


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
    start = time.time()
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=os.environ.copy(),
    )
    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write(stdin_text)
    proc.stdin.close()

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

    events: list[dict[str, Any]] = []
    thread_id: str | None = None
    interrupt_requested = False
    interrupt_requested_at = 0.0
    next_signal_idx = 0
    emit_live = bool(live)
    sig_sequence: list[str] = list(interrupt.signal_sequence) if interrupt else []
    delays_ms: list[int] = compute_escalation_delays_ms(interrupt.escalation_ms) if interrupt else [0]

    def emit(line: str) -> None:
        if not emit_live:
            return
        s = redact_text(line) if redact else line
        if on_live_line is not None:
            try:
                on_live_line(s)
                return
            except Exception:
                # Fall back to printing.
                pass
        print(s, flush=True)

    while sel.get_map():
        # Escalate signals on a timer, if requested.
        if interrupt and interrupt_requested and next_signal_idx < len(sig_sequence):
            delay_s = escalation_delay_s_for_step(delays_ms, next_signal_idx)
            if (time.time() - interrupt_requested_at) >= delay_s:
                sig_name = sig_sequence[next_signal_idx]
                sig = signal_from_name(sig_name)
                if sig is not None:
                    try:
                        proc.send_signal(sig)
                        append_transcript_line(
                            transcript_path,
                            {"ts": now_rfc3339(), "stream": "meta", "line": f"mi.interrupt.sent={sig_name}"},
                        )
                    except Exception:
                        pass
                next_signal_idx += 1

        for key, _mask in sel.select(timeout=0.2):
            stream_name = key.data
            f = key.fileobj
            line = f.readline()
            if line == "":
                sel.unregister(f)
                continue
            line = line.rstrip("\n")
            append_transcript_line(
                transcript_path,
                {"ts": now_rfc3339(), "stream": stream_name, "line": line},
            )

            # Best-effort live rendering:
            # - stdout: Codex emits JSON event objects (one per line).
            # - stderr: surface raw stderr lines (often useful in failures).
            if stream_name != "stdout":
                if hands_raw:
                    emit(f"[hands:{stream_name}] {line}")
                else:
                    if line.strip():
                        emit(f"[hands:stderr] {line}")
                continue

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
                continue

            events.append(ev)
            if ev.get("type") == "thread.started" and isinstance(ev.get("thread_id"), str):
                thread_id = ev["thread_id"]
            if interrupt and (not interrupt_requested) and ev.get("type") == "item.started":
                item = ev.get("item")
                if isinstance(item, dict) and item.get("type") == "command_execution":
                    cmd = str(item.get("command") or "")
                    if should_interrupt_command(interrupt.mode, cmd):
                        interrupt_requested = True
                        interrupt_requested_at = time.time()
                        next_signal_idx = 0
                        append_transcript_line(
                            transcript_path,
                            {
                                "ts": now_rfc3339(),
                                "stream": "meta",
                                "line": f"mi.interrupt.requested=1 mode={interrupt.mode} command={cmd}",
                            },
                        )

            if not hands_raw:
                for out_line in render_codex_event(ev):
                    if out_line is None:
                        continue
                    s = str(out_line)
                    if not s.strip():
                        continue
                    emit(f"[hands] {s}")

    exit_code = proc.wait()
    duration_ms = int((time.time() - start) * 1000)
    append_transcript_line(
        transcript_path,
        {"ts": now_rfc3339(), "stream": "meta", "line": f"mi.codex.exit_code={exit_code} duration_ms={duration_ms}"},
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
