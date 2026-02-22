from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class StateWarningsFlusher:
    """Flush best-effort state corruption warnings into EvidenceLog."""

    state_warnings: list[dict[str, Any]]
    evidence_append: Callable[[dict[str, Any]], Any]
    now_ts: Callable[[], str]
    thread_id_getter: Callable[[], str]
    hands_state: dict[str, Any]

    def flush(self, *, batch_id: str = "b0.state_recovery") -> None:
        if not self.state_warnings:
            return

        tid = str(self.thread_id_getter() or "").strip()
        if not tid:
            tid = str(self.hands_state.get("thread_id") or "").strip()

        items = list(self.state_warnings)
        self.state_warnings.clear()
        self.evidence_append(
            {
                "kind": "state_corrupt",
                "batch_id": str(batch_id or "b0.state_recovery"),
                "ts": self.now_ts(),
                "thread_id": tid,
                "items": items,
            }
        )

