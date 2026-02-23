from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.risk_event_flow import RiskEventAppendDeps, append_risk_event_with_tracking
from ..autopilot.risk_predecide import query_risk_judge as run_query_risk_judge


@dataclass(frozen=True)
class RiskJudgeWiringDeps:
    """Wiring bundle for risk_judge query (recall + prompt + fallback normalization)."""

    task: str
    hands_provider: str
    runtime_cfg_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    maybe_cross_project_recall: Callable[..., None]
    risk_judge_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    build_risk_fallback: Callable[[list[str], str], dict[str, Any]]


def query_risk_judge_wired(
    *,
    batch_idx: int,
    batch_id: str,
    risk_signals: list[str],
    hands_last: str,
    tdb_ctx_batch_obj: dict[str, Any],
    deps: RiskJudgeWiringDeps,
) -> tuple[dict[str, Any], str]:
    """Run risk_judge using runner wiring (behavior-preserving)."""

    return run_query_risk_judge(
        batch_idx=int(batch_idx),
        batch_id=str(batch_id or ""),
        risk_signals=[str(x) for x in (risk_signals if isinstance(risk_signals, list) else [])],
        hands_last=str(hands_last or ""),
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        runtime_cfg=deps.runtime_cfg_getter() if callable(deps.runtime_cfg_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        maybe_cross_project_recall=deps.maybe_cross_project_recall,
        risk_judge_prompt_builder=deps.risk_judge_prompt_builder,
        mind_call=deps.mind_call,
        build_risk_fallback=deps.build_risk_fallback,
    )


@dataclass(frozen=True)
class RiskEventRecordWiringDeps:
    """Wiring bundle for risk_event evidence/segment recording."""

    evidence_window: list[dict[str, Any]]
    evidence_append: Callable[[dict[str, Any]], Any]
    append_window: Callable[[list[dict[str, Any]], dict[str, Any]], None]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    now_ts: Callable[[], str]
    thread_id_getter: Callable[[], str | None]


def append_risk_event_wired(
    *,
    batch_idx: int,
    risk_signals: list[str],
    risk_obj: dict[str, Any],
    risk_mind_ref: str,
    deps: RiskEventRecordWiringDeps,
) -> dict[str, Any]:
    """Append risk_event using runner wiring (behavior-preserving)."""

    thread_id = deps.thread_id_getter() if callable(deps.thread_id_getter) else None
    return append_risk_event_with_tracking(
        batch_idx=int(batch_idx),
        risk_signals=[str(x) for x in (risk_signals if isinstance(risk_signals, list) else [])],
        risk_obj=risk_obj if isinstance(risk_obj, dict) else {},
        risk_mind_ref=str(risk_mind_ref or ""),
        evidence_window=deps.evidence_window if isinstance(deps.evidence_window, list) else [],
        deps=RiskEventAppendDeps(
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

