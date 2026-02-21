from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RiskEventAppendDeps:
    """Dependencies for risk event append + tracking side effects."""

    evidence_append: Callable[[dict[str, Any]], Any]
    append_window: Callable[[list[dict[str, Any]], dict[str, Any]], None]
    segment_add: Callable[[dict[str, Any]], None]
    now_ts: Callable[[], str]
    thread_id: str


def append_risk_event_with_tracking(
    *,
    batch_idx: int,
    risk_signals: list[str],
    risk_obj: dict[str, Any],
    risk_mind_ref: str,
    evidence_window: list[dict[str, Any]],
    deps: RiskEventAppendDeps,
) -> dict[str, Any]:
    """Append risk event and mirror it to rolling window + segment state."""

    rec = deps.evidence_append(
        {
            "kind": "risk_event",
            "batch_id": f"b{batch_idx}",
            "ts": deps.now_ts(),
            "thread_id": deps.thread_id,
            "risk_signals": risk_signals,
            "mind_transcript_ref": risk_mind_ref,
            "risk": risk_obj if isinstance(risk_obj, dict) else {},
        }
    )
    risk_rec = rec if isinstance(rec, dict) else {}
    event_id = risk_rec.get("event_id")

    deps.append_window(
        evidence_window,
        {
            "kind": "risk_event",
            "batch_id": f"b{batch_idx}",
            "event_id": event_id,
            **(risk_obj if isinstance(risk_obj, dict) else {}),
        },
    )
    deps.segment_add(
        {
            "kind": "risk_event",
            "batch_id": f"b{batch_idx}",
            "event_id": event_id,
            "risk_signals": risk_signals,
            "category": (risk_obj if isinstance(risk_obj, dict) else {}).get("category"),
            "severity": (risk_obj if isinstance(risk_obj, dict) else {}).get("severity"),
        }
    )
    return risk_rec
