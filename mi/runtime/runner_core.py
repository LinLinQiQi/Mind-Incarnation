from __future__ import annotations

import sys

from . import autopilot as AP
from . import wiring as W
from .wiring.run_from_boot import run_autopilot_from_boot
from ..providers.types import HandsExecFn, HandsResumeFn, MindProvider


_DEFAULT = object()


def _read_user_answer(question: str) -> str:
    print(question.strip(), file=sys.stderr)
    print("> ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def run_autopilot(
    *,
    task: str,
    project_root: str,
    home_dir: str | None,
    max_batches: int,
    hands_exec: HandsExecFn | None = None,
    hands_resume: HandsResumeFn | None | object = _DEFAULT,
    llm: MindProvider | None = None,
    hands_provider: str = "",
    continue_hands: bool = False,
    reset_hands: bool = False,
    why_trace_on_run_end: bool = False,
    live: bool = False,
    quiet: bool = False,
    no_mi_prompt: bool = False,
    redact: bool = False,
) -> AP.AutopilotResult:
    boot = W.bootstrap_autopilot_run(
        task=task,
        project_root=project_root,
        home_dir=home_dir,
        hands_provider=hands_provider,
        continue_hands=continue_hands,
        reset_hands=reset_hands,
        llm=llm,
        hands_exec=hands_exec,
        hands_resume=hands_resume,
        hands_resume_default_sentinel=_DEFAULT,
        live=live,
        quiet=quiet,
        redact=redact,
        read_user_answer=_read_user_answer,
    )
    return run_autopilot_from_boot(
        boot=boot,
        task=task,
        max_batches=max_batches,
        continue_hands=continue_hands,
        reset_hands=reset_hands,
        why_trace_on_run_end=why_trace_on_run_end,
        no_mi_prompt=no_mi_prompt,
    )


__all__ = ["run_autopilot"]
