from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AutopilotState(str, Enum):
    """High-level orchestration states for one `mi run` invocation."""

    PREPARE = "prepare"
    PREDECIDE = "predecide"
    EXECUTE_HANDS = "execute_hands"
    POST_BATCH = "post_batch"
    CHECKPOINT = "checkpoint"
    RUN_END = "run_end"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass
class StateMachineState:
    """Mutable state carried by the state-machine loop."""

    state: AutopilotState = AutopilotState.PREPARE
    batch_idx: int = 0
    last_batch_id: str = ""
    should_continue: bool | None = None


@dataclass(frozen=True)
class TransitionResult:
    """Transition output used for tracing/tests."""

    prev_state: AutopilotState
    next_state: AutopilotState
    reason: str = ""


@dataclass(frozen=True)
class BatchRunRequest:
    """Input contract for one batch pipeline execution."""

    batch_idx: int
    batch_id: str
    next_input: str = ""
    thread_id: str | None = None
    use_resume: bool = False
    attempted_overlay_resume: bool = False


@dataclass(frozen=True)
class BatchRunResult:
    """Output contract for one batch pipeline execution."""

    continue_loop: bool
    status_hint: str = ""
    notes: str = ""
    thread_id_next: str | None = None
    last_batch_id: str = ""
    last_evidence_rec: dict[str, Any] | None = None
    last_decide_next_rec: dict[str, Any] | None = None


@dataclass(frozen=True)
class CheckpointRequest:
    """Input contract for checkpoint execution."""

    batch_id: str
    planned_next_input: str
    status_hint: str
    note: str
