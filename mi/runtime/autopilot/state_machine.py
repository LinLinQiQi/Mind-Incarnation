from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .contracts import AutopilotState, StateMachineState, TransitionResult


@dataclass
class StateMachineTrace:
    """Best-effort transition trace for tests/audits."""

    transitions: list[TransitionResult] = field(default_factory=list)


def run_state_machine_loop(
    *,
    max_batches: int,
    run_single_batch: Callable[[int, str], bool],
    on_transition: Callable[[TransitionResult, StateMachineState], None] | None = None,
) -> tuple[StateMachineState, StateMachineTrace]:
    """Run a deterministic orchestration state machine for batch execution.

    Behavior is intentionally aligned with the legacy batch loop:
    - execute at most `max_batches` batches
    - stop early when `run_single_batch(...)` returns False
    - mark exhaustion separately (caller sets status/notes policy)
    """

    st = StateMachineState()
    trace = StateMachineTrace()

    def _emit(next_state: AutopilotState, *, reason: str = "") -> None:
        prev = st.state
        tr = TransitionResult(prev_state=prev, next_state=next_state, reason=reason)
        trace.transitions.append(tr)
        if callable(on_transition):
            on_transition(tr, st)
        st.state = next_state

    _emit(AutopilotState.PREDECIDE, reason="run_start")

    for batch_idx in range(max_batches):
        st.batch_idx = int(batch_idx)
        st.last_batch_id = f"b{batch_idx}"

        _emit(AutopilotState.EXECUTE_HANDS, reason="batch_ready")
        should_continue = bool(run_single_batch(batch_idx, st.last_batch_id))
        st.should_continue = should_continue

        _emit(AutopilotState.POST_BATCH, reason="batch_finished")
        if not should_continue:
            _emit(AutopilotState.RUN_END, reason="batch_requested_stop")
            _emit(AutopilotState.DONE, reason="normal_stop")
            return st, trace

        _emit(AutopilotState.CHECKPOINT, reason="continue")
        if batch_idx + 1 < max_batches:
            _emit(AutopilotState.PREDECIDE, reason="next_batch")

    _emit(AutopilotState.RUN_END, reason="max_batches_exhausted")
    _emit(AutopilotState.BLOCKED, reason="max_batches_exhausted")
    return st, trace


def compact_transition_trace(trace: StateMachineTrace) -> list[dict[str, Any]]:
    """Render a compact trace payload suitable for debug events/tests."""

    out: list[dict[str, Any]] = []
    for tr in trace.transitions:
        out.append({"from": tr.prev_state.value, "to": tr.next_state.value, "reason": tr.reason})
    return out

