from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..contracts import CheckpointRequest


@dataclass(frozen=True)
class CheckpointService:
    """Service wrapper for checkpoint execution."""

    run_checkpoint: Callable[[CheckpointRequest], None]

    def run(self, *, request: CheckpointRequest) -> None:
        self.run_checkpoint(request)

