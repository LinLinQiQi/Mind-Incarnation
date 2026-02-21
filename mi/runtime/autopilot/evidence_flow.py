from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class EvidenceAppendDeps:
    """Dependencies for EvidenceLog append + mirror side effects."""

    evidence_append: Callable[[dict[str, Any]], Any]
    append_window: Callable[[list[dict[str, Any]], dict[str, Any]], None]
    segment_add: Callable[[dict[str, Any]], None]
    now_ts: Callable[[], str]
    thread_id: str


def append_evidence_with_tracking(
    *,
    batch_id: str,
    hands_transcript_ref: str,
    mind_transcript_ref: str,
    mi_input: str,
    transcript_observation: dict[str, Any],
    repo_observation: dict[str, Any],
    evidence_obj: dict[str, Any],
    evidence_window: list[dict[str, Any]],
    deps: EvidenceAppendDeps,
) -> dict[str, Any]:
    """Append one evidence event and sync evidence window + segment state."""

    rec = deps.evidence_append(
        {
            "kind": "evidence",
            "batch_id": batch_id,
            "ts": deps.now_ts(),
            "thread_id": deps.thread_id,
            "hands_transcript_ref": str(hands_transcript_ref or ""),
            "mind_transcript_ref": str(mind_transcript_ref or ""),
            "mi_input": str(mi_input or ""),
            "transcript_observation": transcript_observation if isinstance(transcript_observation, dict) else {},
            "repo_observation": repo_observation if isinstance(repo_observation, dict) else {},
            **(evidence_obj if isinstance(evidence_obj, dict) else {}),
        }
    )
    out = rec if isinstance(rec, dict) else {}
    deps.append_window(evidence_window, out)
    deps.segment_add(out)
    return out
