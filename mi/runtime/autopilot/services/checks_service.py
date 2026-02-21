from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ChecksService:
    """Checkpoint-level hooks for check-planning orchestration."""

    on_checkpoint_cb: Callable[..., None] | None = None

    def on_checkpoint(self, *, batch_idx: int, batch_id: str) -> None:
        if callable(self.on_checkpoint_cb):
            self.on_checkpoint_cb(batch_idx=batch_idx, batch_id=batch_id)

