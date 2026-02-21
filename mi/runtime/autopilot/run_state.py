from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunState:
    """Mutable runtime state shared by batch flow helpers."""

    thread_id: str | None = None
    executed_batches: int = 0
    status: str = "not_done"
    notes: str = ""
    next_input: str = ""
    evidence_window: list[dict[str, Any]] = field(default_factory=list)
    last_evidence_rec: dict[str, Any] | None = None
    last_decide_next_rec: dict[str, Any] | None = None
