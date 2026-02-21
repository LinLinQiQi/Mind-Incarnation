from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class EvidenceService:
    """Hooks for orchestration-level evidence tracing."""

    on_transition_cb: Callable[..., None] | None = None
    on_run_end_cb: Callable[[], None] | None = None

    def on_transition(self, *, from_state: str, to_state: str, reason: str, batch_idx: int, batch_id: str) -> None:
        if callable(self.on_transition_cb):
            self.on_transition_cb(
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                batch_idx=batch_idx,
                batch_id=batch_id,
            )

    def on_run_end(self) -> None:
        if callable(self.on_run_end_cb):
            self.on_run_end_cb()

