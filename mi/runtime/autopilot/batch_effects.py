from __future__ import annotations

from typing import Any, Callable


def append_evidence_window(evidence_window: list[dict[str, Any]], item: dict[str, Any], *, limit: int = 8) -> None:
    """Append one entry and keep the recent evidence window bounded."""

    evidence_window.append(item)
    if len(evidence_window) > limit:
        del evidence_window[:-limit]


def segment_add_and_persist(
    *,
    segment_add: Callable[[dict[str, Any]], None],
    persist_segment_state: Callable[[], None],
    item: dict[str, Any],
) -> None:
    """Write one compact segment record and persist the buffer."""

    segment_add(item)
    persist_segment_state()
