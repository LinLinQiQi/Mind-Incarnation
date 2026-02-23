from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunnerWiringState:
    """Mutable run state owned by run-loop composition (reduces closure drift)."""

    thread_id: str | None = None
    next_input: str = ""
    status: str = "not_done"
    notes: str = ""
    executed_batches: int = 0
    last_batch_id: str = ""
    last_evidence_rec: dict[str, Any] | None = None
    last_decide_next_rec: dict[str, Any] | None = None

    sent_sigs: list[str] = field(default_factory=list)
    segment_state: dict[str, Any] = field(default_factory=dict)
    segment_records: list[dict[str, Any]] = field(default_factory=list)
    last_checkpoint_key: str = ""


class RunnerStateAccess:
    """Thin getter/setter facade for runner state (behavior-preserving)."""

    def __init__(self, state: RunnerWiringState):
        self._state = state

    # Core run-loop controls (orchestrator-facing).
    def get_thread_id(self) -> str:
        return str(self._state.thread_id or "")

    def get_thread_id_opt(self) -> str | None:
        return self._state.thread_id

    def set_thread_id(self, value: str | None) -> None:
        self._state.thread_id = value

    def get_next_input(self) -> str:
        return str(self._state.next_input or "")

    def set_next_input(self, value: str) -> None:
        self._state.next_input = str(value or "")

    def get_status(self) -> str:
        return str(self._state.status or "")

    def set_status(self, value: str) -> None:
        self._state.status = str(value or "")

    def get_notes(self) -> str:
        return str(self._state.notes or "")

    def set_notes(self, value: str) -> None:
        self._state.notes = str(value or "")

    def get_executed_batches(self) -> int:
        return int(self._state.executed_batches)

    def set_executed_batches(self, value: int) -> None:
        self._state.executed_batches = int(value or 0)

    def get_last_batch_id(self) -> str:
        return str(self._state.last_batch_id or "")

    def set_last_batch_id(self, value: str) -> None:
        self._state.last_batch_id = str(value or "")

    # Runner-only helpers passed into wiring bundles.
    def get_sent_sigs(self) -> list[str]:
        return list(self._state.sent_sigs)

    def set_sent_sigs(self, value: list[str]) -> None:
        self._state.sent_sigs = list(value)

    def set_last_evidence_rec(self, rec: dict[str, Any] | None) -> None:
        self._state.last_evidence_rec = rec if isinstance(rec, dict) else None

    def set_last_decide_rec(self, rec: dict[str, Any] | None) -> None:
        self._state.last_decide_next_rec = rec if isinstance(rec, dict) else None

