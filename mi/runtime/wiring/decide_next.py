from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.decide_query_flow import (
    DecideNextQueryDeps,
    DecideRecordEffectsDeps,
    DecideRecordEffectsResult,
    query_decide_next as run_query_decide_next,
    record_decide_next_effects as run_record_decide_next_effects,
)


@dataclass(frozen=True)
class DecideNextQueryWiringDeps:
    """Wiring bundle for decide_next prompt/query flow."""

    task: str
    hands_provider: str
    mindspec_base_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    workflow_run: dict[str, Any]
    workflow_load_effective: Callable[[], list[dict[str, Any]]]
    recent_evidence: list[dict[str, Any]]

    build_decide_context: Callable[..., Any]
    summarize_thought_db_context: Callable[[Any], dict[str, Any]]
    decide_next_prompt_builder: Callable[..., str]
    load_active_workflow: Callable[..., Any]
    mind_call: Callable[..., tuple[Any, str, str]]


def query_decide_next_wired(
    *,
    batch_idx: int,
    batch_id: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    auto_answer_obj: dict[str, Any],
    deps: DecideNextQueryWiringDeps,
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any], dict[str, Any]]:
    """Build decide_next prompt, call Mind, and return decision plus prompt context."""

    return run_query_decide_next(
        batch_idx=int(batch_idx),
        batch_id=str(batch_id or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        mindspec_base=deps.mindspec_base_getter() if callable(deps.mindspec_base_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        workflow_run=deps.workflow_run if isinstance(deps.workflow_run, dict) else {},
        workflow_load_effective=deps.workflow_load_effective,
        recent_evidence=deps.recent_evidence if isinstance(deps.recent_evidence, list) else [],
        hands_last=str(hands_last or ""),
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
        deps=DecideNextQueryDeps(
            build_decide_context=deps.build_decide_context,
            summarize_thought_db_context=deps.summarize_thought_db_context,
            decide_next_prompt_builder=deps.decide_next_prompt_builder,
            load_active_workflow=deps.load_active_workflow,
            mind_call=deps.mind_call,
        ),
    )


@dataclass(frozen=True)
class DecideRecordEffectsWiringDeps:
    """Wiring bundle for decide_next side-effect recording."""

    log_decide_next: Callable[..., dict[str, Any] | None]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    apply_set_testless_strategy_overlay_update: Callable[..., None]
    handle_learn_suggested: Callable[..., Any]
    emit_prefixed: Callable[[str, str], None]


def record_decide_next_effects_wired(
    *,
    batch_idx: int,
    decision_obj: dict[str, Any],
    decision_mind_ref: str,
    tdb_ctx_summary: dict[str, Any],
    deps: DecideRecordEffectsWiringDeps,
) -> DecideRecordEffectsResult:
    """Persist decide_next outputs and apply declared side effects (behavior-preserving)."""

    return run_record_decide_next_effects(
        batch_idx=int(batch_idx),
        decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
        decision_mind_ref=str(decision_mind_ref or ""),
        tdb_ctx_summary=tdb_ctx_summary if isinstance(tdb_ctx_summary, dict) else {},
        deps=DecideRecordEffectsDeps(
            log_decide_next=deps.log_decide_next,
            segment_add=deps.segment_add,
            persist_segment_state=deps.persist_segment_state,
            apply_set_testless_strategy_overlay_update=deps.apply_set_testless_strategy_overlay_update,
            handle_learn_suggested=deps.handle_learn_suggested,
            emit_prefixed=deps.emit_prefixed,
        ),
    )

