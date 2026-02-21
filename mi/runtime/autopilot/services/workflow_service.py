from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class WorkflowService:
    """Hooks for run/batch workflow orchestration lifecycle."""

    on_run_start_cb: Callable[[], None] | None = None
    on_post_batch_cb: Callable[..., None] | None = None
    on_run_end_cb: Callable[[], None] | None = None

    def on_run_start(self) -> None:
        if callable(self.on_run_start_cb):
            self.on_run_start_cb()

    def on_post_batch(self, *, batch_idx: int, batch_id: str) -> None:
        if callable(self.on_post_batch_cb):
            self.on_post_batch_cb(batch_idx=batch_idx, batch_id=batch_id)

    def on_run_end(self) -> None:
        if callable(self.on_run_end_cb):
            self.on_run_end_cb()

