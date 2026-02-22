from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .windowing import trim_evidence_window


@dataclass(frozen=True)
class RecallDeps:
    """Dependencies for cross-project recall write-through behavior."""

    mem_recall: Callable[..., Any]
    evidence_append: Callable[[dict[str, Any]], Any]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]


def maybe_cross_project_recall_write_through(
    *,
    batch_id: str,
    reason: str,
    query: str,
    thread_id: str,
    evidence_window: list[dict[str, Any]],
    deps: RecallDeps,
) -> dict[str, Any] | None:
    """Run cross-project recall and write results into EvidenceLog + window + segment state (best-effort)."""

    out = deps.mem_recall(batch_id=batch_id, reason=reason, query=query, thread_id=thread_id)
    if not out:
        return None

    rec = deps.evidence_append(out.evidence_event if hasattr(out, "evidence_event") else {})
    win = dict(out.window_entry) if hasattr(out, "window_entry") else {}
    if isinstance(rec, dict) and isinstance(rec.get("event_id"), str) and rec.get("event_id"):
        win["event_id"] = rec["event_id"]

    evidence_window.append(win if isinstance(win, dict) else {})
    trim_evidence_window(evidence_window)

    if isinstance(rec, dict):
        deps.segment_add(rec)
        deps.persist_segment_state()
    return rec if isinstance(rec, dict) else None
