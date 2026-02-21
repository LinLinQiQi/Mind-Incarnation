from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..batch_pipeline import PreactionDecision
from ..contracts import BatchRunRequest, BatchRunResult
from .decide_batch_service import DecideBatchService


@dataclass(frozen=True)
class PipelineService:
    """Run one batch through predecide + decide phases."""

    run_predecide_phase: Callable[[BatchRunRequest], bool | PreactionDecision]
    decide_service: DecideBatchService

    def run_batch(self, *, req: BatchRunRequest) -> BatchRunResult:
        out = self.run_predecide_phase(req)
        if isinstance(out, bool):
            return BatchRunResult(
                continue_loop=bool(out),
                status_hint="",
                notes="",
                last_batch_id=str(req.batch_id or ""),
            )
        if not isinstance(out, PreactionDecision):
            return BatchRunResult(
                continue_loop=False,
                status_hint="blocked",
                notes="invalid pre-decide pipeline output",
                last_batch_id=str(req.batch_id or ""),
            )
        ok = self.decide_service.run(req=req, preaction=out)
        return BatchRunResult(
            continue_loop=bool(ok),
            status_hint="",
            notes="",
            last_batch_id=str(req.batch_id or ""),
        )
