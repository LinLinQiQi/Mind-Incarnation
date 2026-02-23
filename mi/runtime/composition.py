from __future__ import annotations

from typing import Any, Callable

from . import autopilot as AP
from .runner_state import RunnerStateAccess


def build_run_loop_orchestrator(
    *,
    max_batches: int,
    run_predecide_phase: Callable[[AP.BatchRunRequest], bool | AP.PreactionDecision],
    run_decide_phase: Callable[[AP.BatchRunRequest, AP.PreactionDecision], bool],
    checkpoint_enabled: bool,
    checkpoint_runner: Callable[[Any], None],
    learn_runner: Callable[[], None],
    why_runner: Callable[[], None],
    snapshot_flusher: Callable[[], None],
    state_warning_flusher: Callable[[], None],
    state: RunnerStateAccess,
) -> AP.RunLoopOrchestrator:
    """Composition root for the run-loop orchestrator (behavior-preserving)."""

    return AP.RunLoopOrchestrator(
        deps=AP.RunLoopOrchestratorDeps(
            max_batches=int(max_batches),
            run_predecide_phase=run_predecide_phase,
            run_decide_phase=run_decide_phase,
            next_input_getter=state.get_next_input,
            thread_id_getter=state.get_thread_id,
            status_getter=state.get_status,
            status_setter=state.set_status,
            notes_getter=state.get_notes,
            notes_setter=state.set_notes,
            last_batch_id_getter=state.get_last_batch_id,
            last_batch_id_setter=state.set_last_batch_id,
            executed_batches_getter=state.get_executed_batches,
            checkpoint_enabled=bool(checkpoint_enabled),
            checkpoint_runner=checkpoint_runner,
            learn_runner=learn_runner,
            why_runner=why_runner,
            snapshot_flusher=snapshot_flusher,
            state_warning_flusher=state_warning_flusher,
        )
    )


__all__ = ["build_run_loop_orchestrator"]

