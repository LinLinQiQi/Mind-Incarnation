from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import autopilot as AP
from ..project.overlay_store import write_project_overlay


@dataclass(frozen=True)
class HandsRunnerBundle:
    """Runner wiring bundle for one Hands batch execution (behavior-preserving)."""

    run_hands_batch: Callable[..., Any]


def build_hands_runner_bundle(
    *,
    project_root: Path,
    transcripts_dir: Path,
    cur_provider: str,
    interrupt_cfg: Any,
    overlay: dict[str, Any],
    hands_exec: Any,
    hands_resume: Any,
    home_dir: Path,
    now_ts: Callable[[], str],
    emit_prefixed: Callable[[str, str], None],
    evidence_append: Callable[[dict[str, Any]], Any],
    no_mi_prompt: bool,
    get_thread_id: Callable[[], str | None],
    set_thread_id: Callable[[str | None], None],
    get_executed_batches: Callable[[], int],
    set_executed_batches: Callable[[int], None],
) -> HandsRunnerBundle:
    """Build the Hands execution closure used by the predecide phase."""

    def run_hands_batch(*, ctx: AP.BatchExecutionContext) -> Any:
        result, hs_state = AP.run_hands_batch(
            ctx=ctx,
            state=AP.RunState(thread_id=get_thread_id(), executed_batches=get_executed_batches()),
            deps=AP.HandsFlowDeps(
                run_deps=AP.RunDeps(
                    emit_prefixed=emit_prefixed,
                    now_ts=now_ts,
                    evidence_append=evidence_append,
                ),
                project_root=project_root,
                transcripts_dir=transcripts_dir,
                cur_provider=cur_provider,
                no_mi_prompt=bool(no_mi_prompt),
                interrupt_cfg=interrupt_cfg,
                overlay=overlay,
                hands_exec=hands_exec,
                hands_resume=hands_resume,
                write_overlay=lambda ov: write_project_overlay(home_dir=home_dir, project_root=project_root, overlay=ov),
            ),
        )
        set_thread_id(hs_state.thread_id)
        set_executed_batches(int(hs_state.executed_batches or 0))
        return result

    return HandsRunnerBundle(run_hands_batch=run_hands_batch)

