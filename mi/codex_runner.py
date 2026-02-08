from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .storage import atomic_write_text, ensure_dir, now_rfc3339


def _is_inside_git_repo(start_dir: Path) -> bool:
    cur = start_dir.resolve()
    while True:
        if (cur / ".git").exists():
            return True
        if cur.parent == cur:
            return False
        cur = cur.parent


def _build_codex_base_args(project_root: Path, skip_git_repo_check: bool) -> list[str]:
    # Keep only global options here. `--skip-git-repo-check` is an `exec` option.
    return ["codex", "--cd", str(project_root)]


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


@dataclass(frozen=True)
class InterruptConfig:
    mode: str  # off|on_high_risk|on_any_external
    signal_sequence: list[str]
    escalation_ms: list[int]


def _signal_from_name(name: str) -> int | None:
    name = name.strip().upper()
    if not name:
        return None
    if not name.startswith("SIG"):
        name = "SIG" + name
    return getattr(signal, name, None)


def _should_interrupt_command(mode: str, command: str) -> bool:
    if mode == "off":
        return False
    lower = command.lower()

    any_external_markers = [
        "pip install",
        "npm install",
        "pnpm install",
        "yarn add",
        "curl ",
        "wget ",
        "git push",
        "rm -rf",
        "sudo ",
    ]
    high_risk_markers = [
        "git push",
        "rm -rf",
        "sudo ",
        "curl | sh",
        "curl|sh",
        "wget | sh",
        "wget|sh",
    ]

    markers = any_external_markers if mode == "on_any_external" else high_risk_markers
    return any(m in lower for m in markers)


def _write_transcript_header(path: Path, meta: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    atomic_write_text(path, json.dumps({"type": "mi.transcript.header", **meta}) + "\n")


def _append_transcript_line(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def run_codex_exec(
    *,
    prompt: str,
    project_root: Path,
    transcript_path: Path,
    full_auto: bool,
    sandbox: str | None,
    output_schema_path: Path | None,
    interrupt: InterruptConfig | None = None,
) -> CodexRunResult:
    skip_git_repo_check = not _is_inside_git_repo(project_root)
    args = _build_codex_base_args(project_root, skip_git_repo_check)
    args.append("exec")
    if skip_git_repo_check:
        args.append("--skip-git-repo-check")
    if full_auto:
        args.append("--full-auto")
    if sandbox:
        args.extend(["--sandbox", sandbox])
    args.append("--json")
    if output_schema_path:
        args.extend(["--output-schema", str(output_schema_path)])
    # Read prompt from stdin to avoid shell escaping/length issues.
    args.append("-")

    _write_transcript_header(
        transcript_path,
        {
            "ts": now_rfc3339(),
            "kind": "exec",
            "cwd": str(project_root),
            "argv": args,
        },
    )

    return _run_codex_process(args=args, stdin_text=prompt, transcript_path=transcript_path, interrupt=interrupt)


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
) -> CodexRunResult:
    skip_git_repo_check = not _is_inside_git_repo(project_root)
    args = _build_codex_base_args(project_root, skip_git_repo_check)
    args.extend(["exec", "resume"])
    if skip_git_repo_check:
        args.append("--skip-git-repo-check")
    if full_auto:
        args.append("--full-auto")
    if sandbox:
        args.extend(["--sandbox", sandbox])
    args.append("--json")
    if output_schema_path:
        args.extend(["--output-schema", str(output_schema_path)])
    args.append(thread_id)
    args.append("-")

    _write_transcript_header(
        transcript_path,
        {
            "ts": now_rfc3339(),
            "kind": "resume",
            "thread_id": thread_id,
            "cwd": str(project_root),
            "argv": args,
        },
    )

    return _run_codex_process(args=args, stdin_text=prompt, transcript_path=transcript_path, interrupt=interrupt)


def _run_codex_process(
    *,
    args: list[str],
    stdin_text: str,
    transcript_path: Path,
    interrupt: InterruptConfig | None,
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

    while sel.get_map():
        # Escalate signals on a timer, if requested.
        if interrupt and interrupt_requested and next_signal_idx < len(interrupt.signal_sequence):
            delays = [0] + [max(0, int(x)) for x in interrupt.escalation_ms]
            if next_signal_idx < len(delays):
                delay_s = delays[next_signal_idx] / 1000.0
            else:
                delay_s = delays[-1] / 1000.0 if delays else 0.0
            if (time.time() - interrupt_requested_at) >= delay_s:
                sig_name = interrupt.signal_sequence[next_signal_idx]
                sig = _signal_from_name(sig_name)
                if sig is not None:
                    try:
                        proc.send_signal(sig)
                        _append_transcript_line(
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
            _append_transcript_line(
                transcript_path,
                {"ts": now_rfc3339(), "stream": stream_name, "line": line},
            )
            if stream_name == "stdout" and line.startswith("{") and line.endswith("}"):
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if isinstance(ev, dict):
                    events.append(ev)
                    if ev.get("type") == "thread.started" and isinstance(ev.get("thread_id"), str):
                        thread_id = ev["thread_id"]
                    if interrupt and not interrupt_requested and ev.get("type") == "item.started":
                        item = ev.get("item")
                        if isinstance(item, dict) and item.get("type") == "command_execution":
                            cmd = str(item.get("command") or "")
                            if _should_interrupt_command(interrupt.mode, cmd):
                                interrupt_requested = True
                                interrupt_requested_at = time.time()
                                next_signal_idx = 0
                                _append_transcript_line(
                                    transcript_path,
                                    {
                                        "ts": now_rfc3339(),
                                        "stream": "meta",
                                        "line": f"mi.interrupt.requested=1 mode={interrupt.mode} command={cmd}",
                                    },
                                )

    exit_code = proc.wait()
    duration_ms = int((time.time() - start) * 1000)
    _append_transcript_line(
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
