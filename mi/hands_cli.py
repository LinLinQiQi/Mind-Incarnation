from __future__ import annotations

import os
import re
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .storage import now_rfc3339
from .transcript_store import append_transcript_line, write_transcript_header


@dataclass(frozen=True)
class CliRunResult:
    thread_id: str
    exit_code: int
    events: list[dict[str, Any]]
    raw_transcript_path: Path
    last_stdout_line: str

    def last_agent_message(self) -> str:
        return self.last_stdout_line or ""

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
) -> tuple[int, str, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=merged_env,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    if stdin_text:
        proc.stdin.write(stdin_text)
    proc.stdin.close()

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

    last_stdout = ""
    found_thread_id = ""
    rx = re.compile(thread_id_regex) if thread_id_regex else None

    start = time.time()
    while sel.get_map():
        for key, _mask in sel.select(timeout=0.2):
            stream_name = key.data
            f = key.fileobj
            line = f.readline()
            if line == "":
                sel.unregister(f)
                continue
            line = line.rstrip("\n")
            append_transcript_line(transcript_path, {"ts": now_rfc3339(), "stream": stream_name, "line": line})

            if stream_name == "stdout" and line.strip():
                last_stdout = line
            if rx and not found_thread_id and line:
                m = rx.search(line)
                if m and m.group(1):
                    found_thread_id = m.group(1)

    exit_code = proc.wait()
    duration_ms = int((time.time() - start) * 1000)
    append_transcript_line(
        transcript_path,
        {"ts": now_rfc3339(), "stream": "meta", "line": f"mi.cli.exit_code={exit_code} duration_ms={duration_ms}"},
    )

    return exit_code, found_thread_id, last_stdout


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
        interrupt: Any | None = None,
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

        t0 = time.time()
        exit_code, extracted_tid, last_stdout = _run_process(
            argv=argv,
            stdin_text=stdin_text,
            cwd=project_root,
            transcript_path=transcript_path,
            env=self._env,
            thread_id_regex=self._thread_id_regex,
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
        interrupt: Any | None = None,
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

        t0 = time.time()
        exit_code, extracted_tid, last_stdout = _run_process(
            argv=argv,
            stdin_text=stdin_text,
            cwd=project_root,
            transcript_path=transcript_path,
            env=self._env,
            thread_id_regex=self._thread_id_regex,
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
