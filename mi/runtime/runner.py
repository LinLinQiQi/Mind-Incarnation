from __future__ import annotations

"""Public entrypoint for the MI runtime loop.

The main implementation is kept in `mi/runtime/runner_core.py` to keep this
module small and stable (reduces import/wiring drift and merge conflicts).
"""

from typing import Any

from . import runner_core as _core

def run_autopilot(
    *,
    task: str,
    project_root: str,
    home_dir: str | None,
    max_batches: int,
    hands_exec: Any | None = None,
    hands_resume: Any = _core._DEFAULT,
    llm: Any | None = None,
    hands_provider: str = "",
    continue_hands: bool = False,
    reset_hands: bool = False,
    why_trace_on_run_end: bool = False,
    live: bool = False,
    quiet: bool = False,
    no_mi_prompt: bool = False,
    redact: bool = False,
) -> _core.AP.AutopilotResult:
    return _core.run_autopilot(
        task=task,
        project_root=project_root,
        home_dir=home_dir,
        max_batches=max_batches,
        hands_exec=hands_exec,
        hands_resume=hands_resume,
        llm=llm,
        hands_provider=hands_provider,
        continue_hands=continue_hands,
        reset_hands=reset_hands,
        why_trace_on_run_end=why_trace_on_run_end,
        live=live,
        quiet=quiet,
        no_mi_prompt=no_mi_prompt,
        redact=redact,
    )


__all__ = ["run_autopilot"]
