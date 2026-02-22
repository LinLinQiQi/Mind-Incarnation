from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...providers.mind_errors import MindCallError
from .batch_effects import append_evidence_window


@dataclass(frozen=True)
class MindCallState:
    """Mutable counters/state for Mind circuit-break behavior."""

    failures_total: int
    failures_consecutive: int
    circuit_open: bool


@dataclass(frozen=True)
class MindCallDeps:
    """Dependencies for Mind call execution + side effects."""

    llm_call: Callable[..., Any]
    evidence_append: Callable[[dict[str, Any]], Any]
    now_ts: Callable[[], str]
    truncate: Callable[[str, int], str]


@dataclass(frozen=True)
class MindCallResult:
    """Result of a single best-effort Mind call."""

    obj: dict[str, Any] | None
    mind_transcript_ref: str
    state: str  # ok | error | skipped
    next_state: MindCallState


def run_mind_call(
    *,
    state: MindCallState,
    thread_id: str,
    batch_id: str,
    schema_filename: str,
    prompt: str,
    tag: str,
    threshold: int,
    evidence_window: list[dict[str, Any]],
    deps: MindCallDeps,
) -> MindCallResult:
    """Best-effort Mind call wrapper with circuit breaker and evidence side effects."""

    if bool(state.circuit_open):
        return MindCallResult(
            obj=None,
            mind_transcript_ref="",
            state="skipped",
            next_state=state,
        )

    try:
        res = deps.llm_call(schema_filename=schema_filename, prompt=prompt, tag=tag)
        obj = getattr(res, "obj", None)
        tp = getattr(res, "transcript_path", None)
        mind_ref = str(tp) if tp else ""
        return MindCallResult(
            obj=(obj if isinstance(obj, dict) else None),
            mind_transcript_ref=mind_ref,
            state="ok",
            next_state=MindCallState(
                failures_total=int(state.failures_total),
                failures_consecutive=0,
                circuit_open=bool(state.circuit_open),
            ),
        )
    except Exception as e:
        mind_ref = ""

        tp = getattr(e, "transcript_path", None)
        if isinstance(tp, Path):
            mind_ref = str(tp)
        elif isinstance(tp, str) and tp.strip():
            mind_ref = tp.strip()
        elif isinstance(e, MindCallError) and e.transcript_path:
            mind_ref = str(e.transcript_path)

        failures_total = int(state.failures_total) + 1
        failures_consecutive = int(state.failures_consecutive) + 1
        circuit_open = bool(state.circuit_open)

        deps.evidence_append(
            {
                "kind": "mind_error",
                "batch_id": str(batch_id),
                "ts": deps.now_ts(),
                "thread_id": str(thread_id or ""),
                "schema_filename": str(schema_filename),
                "tag": str(tag),
                "mind_transcript_ref": str(mind_ref or ""),
                "error": deps.truncate(str(e or ""), 2000),
            }
        )
        append_evidence_window(
            evidence_window,
            {
                "kind": "mind_error",
                "batch_id": str(batch_id),
                "schema_filename": str(schema_filename),
                "tag": str(tag),
                "error": deps.truncate(str(e), 400),
            },
            limit=8,
        )

        if (not circuit_open) and failures_consecutive >= int(threshold):
            circuit_open = True
            deps.evidence_append(
                {
                    "kind": "mind_circuit",
                    "batch_id": str(batch_id),
                    "ts": deps.now_ts(),
                    "thread_id": str(thread_id or ""),
                    "state": "open",
                    "threshold": int(threshold),
                    "failures_total": failures_total,
                    "failures_consecutive": failures_consecutive,
                    "schema_filename": str(schema_filename),
                    "tag": str(tag),
                    "error": deps.truncate(str(e or ""), 2000),
                }
            )
            append_evidence_window(
                evidence_window,
                {
                    "kind": "mind_circuit",
                    "batch_id": str(batch_id),
                    "state": "open",
                    "threshold": int(threshold),
                    "failures_consecutive": failures_consecutive,
                    "note": "opened due to repeated mind_error",
                },
                limit=8,
            )

        return MindCallResult(
            obj=None,
            mind_transcript_ref=str(mind_ref or ""),
            state="error",
            next_state=MindCallState(
                failures_total=failures_total,
                failures_consecutive=failures_consecutive,
                circuit_open=circuit_open,
            ),
        )
