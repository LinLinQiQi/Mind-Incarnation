from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class InteractionRecordDeps:
    """Dependencies for user_input/auto_answer evidence recording."""

    evidence_append: Callable[[dict[str, Any]], Any]
    append_window: Callable[[list[dict[str, Any]], dict[str, Any]], None]
    append_segment_record: Callable[[dict[str, Any]], None]
    now_ts: Callable[[], str]
    thread_id: str


def append_user_input_record_with_tracking(
    *,
    batch_id: str,
    question: str,
    answer: str,
    evidence_window: list[dict[str, Any]],
    deps: InteractionRecordDeps,
) -> dict[str, Any]:
    """Append user input evidence and keep evidence window + segment state in sync."""

    rec = deps.evidence_append(
        {
            "kind": "user_input",
            "batch_id": str(batch_id),
            "ts": deps.now_ts(),
            "thread_id": deps.thread_id,
            "question": question,
            "answer": answer,
        }
    )
    out = rec if isinstance(rec, dict) else {}

    deps.append_window(
        evidence_window,
        {
            "kind": "user_input",
            "batch_id": str(batch_id),
            "event_id": out.get("event_id"),
            "question": question,
            "answer": answer,
        },
    )
    if isinstance(rec, dict):
        deps.append_segment_record(out)
    return out


def append_auto_answer_record_with_tracking(
    *,
    batch_id: str,
    mind_transcript_ref: str,
    auto_answer: dict[str, Any],
    evidence_window: list[dict[str, Any]],
    deps: InteractionRecordDeps,
) -> dict[str, Any]:
    """Append auto_answer evidence and keep evidence window + segment state in sync."""

    aa = auto_answer if isinstance(auto_answer, dict) else {}
    rec = deps.evidence_append(
        {
            "kind": "auto_answer",
            "batch_id": str(batch_id),
            "ts": deps.now_ts(),
            "thread_id": deps.thread_id,
            "mind_transcript_ref": str(mind_transcript_ref or ""),
            "auto_answer": aa,
        }
    )
    out = rec if isinstance(rec, dict) else {}

    deps.append_window(
        evidence_window,
        {
            "kind": "auto_answer",
            "batch_id": str(batch_id),
            "event_id": out.get("event_id"),
            **aa,
        },
    )

    seg_item = {"kind": "auto_answer", "batch_id": str(batch_id), "event_id": out.get("event_id"), **aa}
    deps.append_segment_record(seg_item)
    return out
