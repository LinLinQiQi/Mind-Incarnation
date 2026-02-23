from __future__ import annotations

import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from ..core.redact import redact_text
from ..core.storage import now_rfc3339
from ..runtime.transcript_store import append_transcript_line
from .interrupts import (
    InterruptConfig,
    compute_escalation_delays_ms,
    escalation_delay_s_for_step,
    signal_from_name,
)


def _append_meta(transcript_path: Path, line: str) -> None:
    append_transcript_line(transcript_path, {"ts": now_rfc3339(), "stream": "meta", "line": str(line or "")})


def run_streaming_process(
    *,
    argv: list[str],
    stdin_text: str,
    transcript_path: Path,
    cwd: Path | None,
    env: dict[str, str] | None,
    interrupt: InterruptConfig | None,
    exit_meta_prefix: str,
    live: bool,
    redact: bool,
    on_live_line: Callable[[str], None] | None,
    on_line: Callable[[str, str, Callable[[str], None], Callable[[str], None], Callable[[str], None]], Any] | None,
    start_timer_before_popen: bool = True,
) -> tuple[int, int]:
    """Run a subprocess and stream stdout/stderr into MI transcript JSONL records.

    This helper is intentionally provider-agnostic: Codex and generic CLI Hands wrappers
    share the same IO/interrupt/transcript plumbing but keep their own parsing/rendering.

    Returns (exit_code, duration_ms).
    """

    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})

    start = time.time() if start_timer_before_popen else 0.0
    proc = subprocess.Popen(
        list(argv),
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=merged_env,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    if not start_timer_before_popen:
        start = time.time()

    if stdin_text:
        proc.stdin.write(stdin_text)
    proc.stdin.close()

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

    interrupt_requested = False
    interrupt_requested_at = 0.0
    next_signal_idx = 0
    sig_sequence: list[str] = list(interrupt.signal_sequence) if interrupt else []
    delays_ms: list[int] = compute_escalation_delays_ms(interrupt.escalation_ms) if interrupt else [0]

    def emit(line: str) -> None:
        if not bool(live):
            return
        s = redact_text(str(line)) if redact else str(line)
        if on_live_line is not None:
            try:
                on_live_line(s)
                return
            except Exception:
                # Fall back to printing.
                pass
        print(s, flush=True)

    def append_meta(line: str) -> None:
        _append_meta(transcript_path, line)

    def request_interrupt(meta_line: str) -> None:
        nonlocal interrupt_requested, interrupt_requested_at, next_signal_idx
        if not interrupt or interrupt_requested:
            return
        interrupt_requested = True
        interrupt_requested_at = time.time()
        next_signal_idx = 0
        append_meta(meta_line)

    try:
        while sel.get_map():
            # Escalate signals on a timer, if requested.
            if interrupt and interrupt_requested and next_signal_idx < len(sig_sequence):
                delay_s = escalation_delay_s_for_step(delays_ms, next_signal_idx)
                if (time.time() - interrupt_requested_at) >= delay_s:
                    sig_name = str(sig_sequence[next_signal_idx])
                    sig = signal_from_name(sig_name)
                    if sig is not None:
                        try:
                            proc.send_signal(sig)
                            append_meta(f"mi.interrupt.sent={sig_name}")
                        except Exception:
                            pass
                    next_signal_idx += 1

            for key, _mask in sel.select(timeout=0.2):
                stream_name = str(key.data or "")
                f = key.fileobj
                line = f.readline()
                if line == "":
                    try:
                        sel.unregister(f)
                    except Exception:
                        pass
                    continue
                line = line.rstrip("\n")
                append_transcript_line(transcript_path, {"ts": now_rfc3339(), "stream": stream_name, "line": line})
                if on_line is not None:
                    on_line(stream_name, line, emit, append_meta, request_interrupt)
    finally:
        try:
            sel.close()
        except Exception:
            pass
        for f in (proc.stdout, proc.stderr):
            try:
                if f:
                    f.close()
            except Exception:
                pass

    exit_code = proc.wait()
    duration_ms = int((time.time() - start) * 1000)
    append_meta(f"{str(exit_meta_prefix or 'mi.proc').strip()}.exit_code={exit_code} duration_ms={duration_ms}")
    return exit_code, duration_ms

