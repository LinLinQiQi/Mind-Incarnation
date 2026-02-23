from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import autopilot as AP
from . import wiring as W


@dataclass(frozen=True)
class InteractionRecordWiringBundle:
    """Runner wiring bundle for user_input/auto_answer evidence records (behavior-preserving)."""

    append_user_input_record: Callable[..., dict[str, Any]]
    append_auto_answer_record: Callable[..., dict[str, Any]]


def build_interaction_record_wiring_bundle(
    *,
    evidence_window: list[dict[str, Any]],
    evidence_append: Callable[[dict[str, Any]], Any],
    segment_add: Callable[[dict[str, Any]], None],
    persist_segment_state: Callable[[], None],
    now_ts: Callable[[], str],
    thread_id_getter: Callable[[], str | None],
) -> InteractionRecordWiringBundle:
    """Build interaction record helpers used by multiple runner bundles."""

    deps = W.InteractionRecordWiringDeps(
        evidence_window=evidence_window,
        evidence_append=evidence_append,
        append_window=AP.append_evidence_window,
        segment_add=segment_add,
        persist_segment_state=persist_segment_state,
        now_ts=now_ts,
        thread_id_getter=thread_id_getter,
    )

    def append_user_input_record(*, batch_id: str, question: str, answer: str) -> dict[str, Any]:
        """Append user input evidence and keep segment/evidence windows in sync."""

        return W.append_user_input_record_wired(
            batch_id=str(batch_id),
            question=question,
            answer=answer,
            deps=deps,
        )

    def append_auto_answer_record(*, batch_id: str, mind_transcript_ref: str, auto_answer: dict[str, Any]) -> dict[str, Any]:
        """Append auto_answer evidence and keep segment/evidence windows in sync."""

        return W.append_auto_answer_record_wired(
            batch_id=str(batch_id),
            mind_transcript_ref=str(mind_transcript_ref or ""),
            auto_answer=auto_answer if isinstance(auto_answer, dict) else {},
            deps=deps,
        )

    return InteractionRecordWiringBundle(
        append_user_input_record=append_user_input_record,
        append_auto_answer_record=append_auto_answer_record,
    )

