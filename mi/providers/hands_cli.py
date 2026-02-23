from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .proc_stream import run_streaming_process
from .interrupts import InterruptConfig, should_interrupt_command
from ..core.storage import now_rfc3339
from ..runtime.transcript_store import write_transcript_header
from ..runtime.transcript import last_agent_message_from_transcript


@dataclass(frozen=True)
class CliRunResult:
    thread_id: str
    exit_code: int
    events: list[dict[str, Any]]
    raw_transcript_path: Path
    last_stdout_line: str

    def last_agent_message(self) -> str:
        # Prefer transcript parsing so stream-json (e.g., Claude Code) yields human text.
        return last_agent_message_from_transcript(self.raw_transcript_path) or (self.last_stdout_line or "")

    def iter_command_executions(self) -> Iterable[dict[str, Any]]:
        return iter(())


def _format_args(args: list[str], *, project_root: Path, thread_id: str, prompt: str, prompt_mode: str) -> tuple[list[str], str]:
    out: list[str] = []
    stdin_text = prompt

    for a in args:
        a2 = (
            a.replace("{project_root}", str(project_root))
            .replace("{thread_id}", str(thread_id))
            .replace("{prompt}", prompt if prompt_mode == "arg" else "{prompt}")
        )
        out.append(a2)

    if prompt_mode == "arg":
        stdin_text = ""
        if "{prompt}" in " ".join(args):
            # Prompt is already injected.
            pass
        else:
            # Append as a final argument as a fallback.
            out.append(prompt)

    return out, stdin_text


def _run_process(
    *,
    argv: list[str],
    stdin_text: str,
    cwd: Path,
    transcript_path: Path,
    env: dict[str, str] | None,
    thread_id_regex: str,
    interrupt: InterruptConfig | None,
    live: bool,
    hands_raw: bool,
    redact: bool,
    on_live_line: Callable[[str], None] | None,
) -> tuple[int, str, str]:
    last_stdout = ""
    stdout_tail: deque[str] = deque(maxlen=80)
    found_thread_id = ""
    found_session_id = ""
    rx = re.compile(thread_id_regex) if thread_id_regex else None

    def _on_line(
        stream_name: str,
        line: str,
        emit: Callable[[str], None],
        _append_meta: Callable[[str], None],
        request_interrupt: Callable[[str], None],
    ) -> None:
        nonlocal last_stdout, found_thread_id, found_session_id

        if hands_raw:
            emit(f"[hands:{stream_name}] {line}")
        else:
            if line.strip():
                emit(f"[hands:{stream_name}] {line}")

        if stream_name == "stdout" and line.strip():
            last_stdout = line
            stdout_tail.append(line)
            if not found_session_id:
                s = line.strip()
                if s.startswith("{") and s.endswith("}"):
                    try:
                        ev = json.loads(s)
                    except Exception:
                        ev = None
                    if isinstance(ev, dict):
                        sid = ev.get("session_id") or ev.get("sessionId")
                        if isinstance(sid, str) and sid.strip():
                            found_session_id = sid.strip()
        if rx and not found_thread_id and line:
            m = rx.search(line)
            if m and m.group(1):
                found_thread_id = m.group(1)
        if interrupt and line:
            mode = str(interrupt.mode or "")
            if mode in ("on_high_risk", "on_any_external") and should_interrupt_command(mode, line):
                request_interrupt(f"mi.interrupt.requested=1 mode={mode} text={line[:200]}")

    exit_code, _duration_ms = run_streaming_process(
        argv=argv,
        stdin_text=stdin_text,
        transcript_path=transcript_path,
        cwd=cwd,
        env=env,
        interrupt=interrupt,
        exit_meta_prefix="mi.cli",
        live=bool(live),
        redact=bool(redact),
        on_live_line=on_live_line,
        on_line=_on_line,
        start_timer_before_popen=False,
    )

    if not found_thread_id and found_session_id:
        found_thread_id = found_session_id

    tail = "\n".join(list(stdout_tail)).strip()
    return exit_code, found_thread_id, tail if tail else last_stdout


class CliHandsAdapter:
    def __init__(
        self,
        *,
        exec_argv: list[str],
        resume_argv: list[str] | None,
        prompt_mode: str,
        env: dict[str, str] | None,
        thread_id_regex: str,
    ):
        self._exec_argv = [str(x) for x in exec_argv]
        self._resume_argv = [str(x) for x in resume_argv] if resume_argv else []
        self._prompt_mode = str(prompt_mode or "stdin")
        self._env = env or {}
        self._thread_id_regex = str(thread_id_regex or "")

    @property
    def supports_resume(self) -> bool:
        return bool(self._resume_argv)

    def exec(
        self,
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
    ) -> CliRunResult:
        argv, stdin_text = _format_args(
            self._exec_argv,
            project_root=project_root,
            thread_id="",
            prompt=prompt,
            prompt_mode=self._prompt_mode,
        )

        write_transcript_header(
            transcript_path,
            {
                "ts": now_rfc3339(),
                "kind": "cli.exec",
                "cwd": str(project_root),
                "argv": argv,
            },
        )

        exit_code, extracted_tid, last_stdout = _run_process(
            argv=argv,
            stdin_text=stdin_text,
            cwd=project_root,
            transcript_path=transcript_path,
            env=self._env,
            thread_id_regex=self._thread_id_regex,
            interrupt=interrupt,
            live=bool(live),
            hands_raw=bool(hands_raw),
            redact=bool(redact),
            on_live_line=on_live_line,
        )

        # If we extracted a thread id, use it; otherwise just use "unknown".
        thread_id = extracted_tid if extracted_tid else "unknown"
        last_stdout_line = last_stdout
        return CliRunResult(
            thread_id=thread_id,
            exit_code=exit_code,
            events=[],
            raw_transcript_path=transcript_path,
            last_stdout_line=str(last_stdout_line or ""),
        )

    def resume(
        self,
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
    ) -> CliRunResult:
        if not self.supports_resume:
            # Fallback: run exec again.
            return self.exec(
                prompt=prompt,
                project_root=project_root,
                transcript_path=transcript_path,
                full_auto=full_auto,
                sandbox=sandbox,
                output_schema_path=output_schema_path,
                interrupt=interrupt,
                live=bool(live),
                hands_raw=bool(hands_raw),
                redact=bool(redact),
                on_live_line=on_live_line,
            )

        argv, stdin_text = _format_args(
            self._resume_argv,
            project_root=project_root,
            thread_id=thread_id,
            prompt=prompt,
            prompt_mode=self._prompt_mode,
        )
        write_transcript_header(
            transcript_path,
            {
                "ts": now_rfc3339(),
                "kind": "cli.resume",
                "thread_id": thread_id,
                "cwd": str(project_root),
                "argv": argv,
            },
        )

        exit_code, extracted_tid, last_stdout = _run_process(
            argv=argv,
            stdin_text=stdin_text,
            cwd=project_root,
            transcript_path=transcript_path,
            env=self._env,
            thread_id_regex=self._thread_id_regex,
            interrupt=interrupt,
            live=bool(live),
            hands_raw=bool(hands_raw),
            redact=bool(redact),
            on_live_line=on_live_line,
        )

        new_tid = extracted_tid if extracted_tid else thread_id
        last_stdout_line = last_stdout
        return CliRunResult(
            thread_id=str(new_tid or thread_id or "unknown"),
            exit_code=exit_code,
            events=[],
            raw_transcript_path=transcript_path,
            last_stdout_line=str(last_stdout_line or ""),
        )
