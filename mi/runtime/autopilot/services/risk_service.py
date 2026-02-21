from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class RiskService:
    """Post-batch hooks for risk-policy orchestration."""

    on_post_batch_cb: Callable[..., None] | None = None

    def on_post_batch(self, *, batch_idx: int, batch_id: str) -> None:
        if callable(self.on_post_batch_cb):
            self.on_post_batch_cb(batch_idx=batch_idx, batch_id=batch_id)

