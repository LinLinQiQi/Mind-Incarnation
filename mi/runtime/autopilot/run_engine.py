from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .batch_engine import run_batch_loop
from .batch_types import BatchLoopDeps, BatchLoopState
from .finalize_flow import finalize_autopilot_run
from .run_context import RunMutableState


@dataclass(frozen=True)
class RunEngineDeps:
    """Dependencies for run-level orchestration."""

    run_single_batch: Callable[[int, str], bool]
    executed_batches_getter: Callable[[], int]
    checkpoint_enabled: bool
    checkpoint_runner: Callable[..., None]
    learn_runner: Callable[[], None]
    why_runner: Callable[[], None]
    snapshot_flusher: Callable[[], None]
    state_warning_flusher: Callable[[], None]


def run_autopilot_engine(*, max_batches: int, state: RunMutableState, deps: RunEngineDeps) -> RunMutableState:
    """Run batch loop and shared finalize sequence using mutable run state."""

    loop_state = BatchLoopState(
        status=str(state.status or ""),
        notes=str(state.notes or ""),
        last_batch_id=str(state.last_batch_id or ""),
        max_batches_exhausted=False,
    )
    loop_state = run_batch_loop(
        max_batches=max_batches,
        state=loop_state,
        deps=BatchLoopDeps(run_single_batch=deps.run_single_batch),
    )
    if bool(loop_state.max_batches_exhausted):
        state.status = str(loop_state.status or state.status)
        state.notes = str(loop_state.notes or state.notes)
    state.last_batch_id = str(loop_state.last_batch_id or state.last_batch_id)
    state.max_batches_exhausted = bool(loop_state.max_batches_exhausted)

    finalize_autopilot_run(
        checkpoint_enabled=bool(deps.checkpoint_enabled),
        executed_batches=int(deps.executed_batches_getter()),
        last_batch_id=str(state.last_batch_id or ""),
        max_batches_exhausted=bool(state.max_batches_exhausted),
        status=str(state.status or ""),
        checkpoint_runner=deps.checkpoint_runner,
        learn_runner=deps.learn_runner,
        why_runner=deps.why_runner,
        snapshot_flusher=deps.snapshot_flusher,
        state_warning_flusher=deps.state_warning_flusher,
    )
    return state
