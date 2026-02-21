from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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

