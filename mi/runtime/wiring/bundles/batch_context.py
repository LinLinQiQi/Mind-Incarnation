from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mi.runtime import autopilot as AP
from mi.runtime.injection import build_light_injection


@dataclass(frozen=True)
class BatchContextWiringBundle:
    """Runner wiring bundle for building BatchExecutionContext (behavior-preserving)."""

    build_context: Callable[[int], AP.BatchExecutionContext]


def build_batch_context_wiring_bundle(
    *,
    transcripts_dir: Path,
    tdb: Any,
    now_ts: Callable[[], str],
    hands_resume: Any | None,
    resumed_from_overlay: bool,
    next_input_getter: Callable[[], str],
    thread_id_getter: Callable[[], str | None],
) -> BatchContextWiringBundle:
    """Build batch context constructor closure used by the predecide phase."""

    def build_context(batch_idx: int) -> AP.BatchExecutionContext:
        return AP.build_batch_execution_context(
            batch_idx=int(batch_idx),
            transcripts_dir=transcripts_dir,
            next_input=str(next_input_getter() or ""),
            thread_id=thread_id_getter(),
            hands_resume=hands_resume,
            resumed_from_overlay=bool(resumed_from_overlay),
            now_ts=now_ts,
            build_light_injection_for_ts=lambda as_of_ts: build_light_injection(tdb=tdb, as_of_ts=as_of_ts),
        )

    return BatchContextWiringBundle(build_context=build_context)
