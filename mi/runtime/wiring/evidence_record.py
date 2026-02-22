from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.evidence_flow import EvidenceAppendDeps, append_evidence_with_tracking


@dataclass(frozen=True)
class EvidenceRecordWiringDeps:
    """Wiring bundle for EvidenceLog(kind=evidence) append + side-effect mirroring."""

    evidence_window: list[dict[str, Any]]
    evidence_append: Callable[[dict[str, Any]], Any]
    append_window: Callable[[list[dict[str, Any]], dict[str, Any]], None]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    now_ts: Callable[[], str]
    thread_id_getter: Callable[[], str | None]


def append_evidence_with_tracking_wired(
    *,
    batch_id: str,
    hands_transcript_ref: str,
    mind_transcript_ref: str,
    mi_input: str,
    transcript_observation: dict[str, Any],
    repo_observation: dict[str, Any],
    evidence_obj: dict[str, Any],
    deps: EvidenceRecordWiringDeps,
) -> dict[str, Any]:
    """Append evidence record using runner wiring (behavior-preserving)."""

    thread_id = deps.thread_id_getter() if callable(deps.thread_id_getter) else None
    return append_evidence_with_tracking(
        batch_id=str(batch_id or ""),
        hands_transcript_ref=str(hands_transcript_ref or ""),
        mind_transcript_ref=str(mind_transcript_ref or ""),
        mi_input=str(mi_input or ""),
        transcript_observation=transcript_observation if isinstance(transcript_observation, dict) else {},
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
        evidence_window=deps.evidence_window if isinstance(deps.evidence_window, list) else [],
        deps=EvidenceAppendDeps(
            evidence_append=deps.evidence_append,
            append_window=deps.append_window,
            segment_add=lambda item: (
                deps.segment_add(item if isinstance(item, dict) else {}),
                deps.persist_segment_state(),
            ),
            now_ts=deps.now_ts,
            thread_id=str(thread_id or ""),
        ),
    )

