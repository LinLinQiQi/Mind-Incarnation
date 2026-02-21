from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class MemoryRecallService:
    """Hooks for cross-project recall orchestration lifecycle."""

    on_run_start_cb: Callable[[], None] | None = None
    on_before_user_prompt_cb: Callable[..., None] | None = None

    def on_run_start(self) -> None:
        if callable(self.on_run_start_cb):
            self.on_run_start_cb()

    def on_before_user_prompt(self, *, batch_idx: int, batch_id: str) -> None:
        if callable(self.on_before_user_prompt_cb):
            self.on_before_user_prompt_cb(batch_idx=batch_idx, batch_id=batch_id)

