from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..batch_pipeline import PreactionDecision
from ..contracts import BatchRunRequest


@dataclass(frozen=True)
class DecideBatchService:
    """Service wrapper for batch-level decide-next execution."""

    run_decide_phase: Callable[[BatchRunRequest, PreactionDecision], bool]

    def run(self, *, req: BatchRunRequest, preaction: PreactionDecision) -> bool:
        return bool(self.run_decide_phase(req, preaction))

