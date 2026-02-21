from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .contracts import AutopilotState
from .finalize_flow import finalize_autopilot_run
from .run_context import RunMutableState
from .state_machine import run_state_machine_loop
from .services import (
    ChecksService,
    DecideService,
    EvidenceService,
    LearnService,
    MemoryRecallService,
    RiskService,
    WorkflowService,
)


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
    checks_service: ChecksService | None = None
    risk_service: RiskService | None = None
    workflow_service: WorkflowService | None = None
    learn_service: LearnService | None = None
    memory_recall_service: MemoryRecallService | None = None
    decide_service: DecideService | None = None
    evidence_service: EvidenceService | None = None


def run_autopilot_engine(*, max_batches: int, state: RunMutableState, deps: RunEngineDeps) -> RunMutableState:
    """Run batch loop and shared finalize sequence using mutable run state."""

    checks_service = deps.checks_service or ChecksService()
    risk_service = deps.risk_service or RiskService()
    workflow_service = deps.workflow_service or WorkflowService()
    learn_service = deps.learn_service or LearnService()
    memory_recall_service = deps.memory_recall_service or MemoryRecallService()
    decide_service = deps.decide_service or DecideService()
    evidence_service = deps.evidence_service or EvidenceService()

    workflow_service.on_run_start()
    memory_recall_service.on_run_start()

    def _on_transition(tr, sm_state) -> None:
        batch_id = str(sm_state.last_batch_id or f"b{int(sm_state.batch_idx)}")
        if tr.next_state == AutopilotState.POST_BATCH:
            risk_service.on_post_batch(batch_idx=int(sm_state.batch_idx), batch_id=batch_id)
            decide_service.on_post_batch(batch_idx=int(sm_state.batch_idx), batch_id=batch_id)
            workflow_service.on_post_batch(batch_idx=int(sm_state.batch_idx), batch_id=batch_id)
        elif tr.next_state == AutopilotState.CHECKPOINT:
            checks_service.on_checkpoint(batch_idx=int(sm_state.batch_idx), batch_id=batch_id)
        evidence_service.on_transition(
            from_state=tr.prev_state.value,
            to_state=tr.next_state.value,
            reason=str(tr.reason or ""),
            batch_idx=int(sm_state.batch_idx),
            batch_id=batch_id,
        )

    sm_state, _trace = run_state_machine_loop(
        max_batches=max_batches,
        run_single_batch=deps.run_single_batch,
        on_transition=_on_transition,
    )

    state.last_batch_id = str(sm_state.last_batch_id or state.last_batch_id)
    exhausted = sm_state.state == AutopilotState.BLOCKED
    state.max_batches_exhausted = bool(exhausted)
    if exhausted:
        state.status = "blocked"
        state.notes = f"reached max_batches={max_batches}"

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
    learn_service.on_run_end()
    workflow_service.on_run_end()
    evidence_service.on_run_end()
    return state
