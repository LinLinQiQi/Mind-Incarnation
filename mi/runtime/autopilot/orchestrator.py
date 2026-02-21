from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .batch_pipeline import PreactionDecision
from .contracts import BatchRunRequest, CheckpointRequest
from .run_context import RunMutableState
from .run_engine import RunEngineDeps, run_autopilot_engine
from .services import CheckpointService, DecideBatchService, PipelineService


@dataclass(frozen=True)
class RunLoopOrchestratorDeps:
    """Dependency bundle for run-level orchestration wiring."""

    max_batches: int
    run_predecide_phase: Callable[[BatchRunRequest], bool | PreactionDecision]
    run_decide_phase: Callable[[BatchRunRequest, PreactionDecision], bool]
    next_input_getter: Callable[[], str]
    thread_id_getter: Callable[[], str]
    status_getter: Callable[[], str]
    status_setter: Callable[[str], None]
    notes_getter: Callable[[], str]
    notes_setter: Callable[[str], None]
    last_batch_id_getter: Callable[[], str]
    last_batch_id_setter: Callable[[str], None]
    executed_batches_getter: Callable[[], int]
    checkpoint_enabled: bool
    checkpoint_runner: Callable[[CheckpointRequest], None]
    learn_runner: Callable[[], None]
    why_runner: Callable[[], None]
    snapshot_flusher: Callable[[], None]
    state_warning_flusher: Callable[[], None]


@dataclass(frozen=True)
class RunLoopOrchestrator:
    """Thin run-loop coordinator around pipeline/checkpoint/run-engine hooks."""

    deps: RunLoopOrchestratorDeps

    def run(self) -> RunMutableState:
        decide_batch_service = DecideBatchService(run_decide_phase=self.deps.run_decide_phase)
        pipeline_service = PipelineService(
            run_predecide_phase=self.deps.run_predecide_phase,
            decide_service=decide_batch_service,
        )

        def _run_single_batch(batch_idx: int, batch_id: str) -> bool:
            req = BatchRunRequest(
                batch_idx=int(batch_idx),
                batch_id=str(batch_id or f"b{int(batch_idx)}"),
                next_input=str(self.deps.next_input_getter() or ""),
                thread_id=str(self.deps.thread_id_getter() or ""),
            )
            out = pipeline_service.run_batch(req=req)
            prev_last = str(self.deps.last_batch_id_getter() or "")
            self.deps.last_batch_id_setter(str(out.last_batch_id or prev_last or req.batch_id))
            if not bool(out.continue_loop):
                st = str(out.status_hint or "").strip()
                if st:
                    self.deps.status_setter(st)
                msg = str(out.notes or "").strip()
                if msg:
                    self.deps.notes_setter(msg)
            return bool(out.continue_loop)

        checkpoint_service = CheckpointService(run_checkpoint=self.deps.checkpoint_runner)

        def _run_checkpoint_request(**kwargs: str) -> None:
            checkpoint_service.run(
                request=CheckpointRequest(
                    batch_id=str(kwargs.get("batch_id") or ""),
                    planned_next_input=str(kwargs.get("planned_next_input") or ""),
                    status_hint=str(kwargs.get("status_hint") or ""),
                    note=str(kwargs.get("note") or ""),
                )
            )

        state = RunMutableState(
            status=str(self.deps.status_getter() or ""),
            notes=str(self.deps.notes_getter() or ""),
            last_batch_id=str(self.deps.last_batch_id_getter() or ""),
            max_batches_exhausted=False,
        )
        engine_state = run_autopilot_engine(
            max_batches=int(self.deps.max_batches),
            state=state,
            deps=RunEngineDeps(
                run_single_batch=_run_single_batch,
                executed_batches_getter=lambda: int(self.deps.executed_batches_getter()),
                checkpoint_enabled=bool(self.deps.checkpoint_enabled),
                checkpoint_runner=_run_checkpoint_request,
                learn_runner=self.deps.learn_runner,
                why_runner=self.deps.why_runner,
                snapshot_flusher=self.deps.snapshot_flusher,
                state_warning_flusher=self.deps.state_warning_flusher,
            ),
        )
        if bool(engine_state.max_batches_exhausted):
            self.deps.status_setter(str(engine_state.status or self.deps.status_getter()))
            self.deps.notes_setter(str(engine_state.notes or self.deps.notes_getter()))
        self.deps.last_batch_id_setter(str(engine_state.last_batch_id or self.deps.last_batch_id_getter()))
        return engine_state
