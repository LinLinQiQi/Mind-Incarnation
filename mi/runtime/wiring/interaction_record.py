from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.interaction_record_flow import (
    InteractionRecordDeps,
    append_auto_answer_record_with_tracking as run_append_auto_answer_record,
    append_user_input_record_with_tracking as run_append_user_input_record,
)


@dataclass(frozen=True)
class InteractionRecordWiringDeps:
    """Wiring bundle for user_input/auto_answer evidence recording (runner -> flow)."""

    evidence_window: list[dict[str, Any]]
    evidence_append: Callable[[dict[str, Any]], Any]
    append_window: Callable[[list[dict[str, Any]], dict[str, Any]], None]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    now_ts: Callable[[], str]
    thread_id_getter: Callable[[], str | None]


def append_user_input_record_wired(
    *,
    batch_id: str,
    question: str,
    answer: str,
    deps: InteractionRecordWiringDeps,
) -> dict[str, Any]:
    """Append user_input record using runner wiring (behavior-preserving)."""

    return run_append_user_input_record(
        batch_id=str(batch_id),
        question=str(question),
        answer=str(answer),
        evidence_window=deps.evidence_window,
        deps=InteractionRecordDeps(
            evidence_append=deps.evidence_append,
            append_window=deps.append_window,
            append_segment_record=lambda item: (
                deps.segment_add(item if isinstance(item, dict) else {}),
                deps.persist_segment_state(),
            ),
            now_ts=deps.now_ts,
            thread_id=deps.thread_id_getter() if callable(deps.thread_id_getter) else None,
        ),
    )


def append_auto_answer_record_wired(
    *,
    batch_id: str,
    mind_transcript_ref: str,
    auto_answer: dict[str, Any],
    deps: InteractionRecordWiringDeps,
) -> dict[str, Any]:
    """Append auto_answer record using runner wiring (behavior-preserving)."""

    return run_append_auto_answer_record(
        batch_id=str(batch_id),
        mind_transcript_ref=str(mind_transcript_ref or ""),
        auto_answer=auto_answer if isinstance(auto_answer, dict) else {},
        evidence_window=deps.evidence_window,
        deps=InteractionRecordDeps(
            evidence_append=deps.evidence_append,
            append_window=deps.append_window,
            append_segment_record=lambda item: (
                deps.segment_add(item if isinstance(item, dict) else {}),
                deps.persist_segment_state(),
            ),
            now_ts=deps.now_ts,
            thread_id=deps.thread_id_getter() if callable(deps.thread_id_getter) else None,
        ),
    )

