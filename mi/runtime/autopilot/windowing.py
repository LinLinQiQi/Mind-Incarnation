from __future__ import annotations

from typing import Any

EVIDENCE_WINDOW_MAX = 8


def trim_evidence_window(evidence_window: list[dict[str, Any]], *, max_len: int = EVIDENCE_WINDOW_MAX) -> None:
    """In-place evidence window trim (keep last N items).

    Use slice assignment so callers holding the list reference see the update.
    """

    n = int(max_len)
    evidence_window[:] = evidence_window[-n:] if n > 0 else []

