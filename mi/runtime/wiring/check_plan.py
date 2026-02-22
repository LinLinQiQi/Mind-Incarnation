from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.check_plan_flow import CheckPlanFlowDeps, plan_checks_and_record


@dataclass(frozen=True)
class CheckPlanWiringDeps:
    """Wiring bundle for check planning (plan_min_checks + record)."""

    task: str
    hands_provider: str
    mindspec_base_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    evidence_window: list[dict[str, Any]]
    thread_id_getter: Callable[[], str | None]
    now_ts: Callable[[], str]
    evidence_append: Callable[[dict[str, Any]], Any]
    segment_add: Callable[[dict[str, Any]], None]
    persist_segment_state: Callable[[], None]
    plan_min_checks_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    empty_check_plan: Callable[[], dict[str, Any]]


def plan_checks_and_record_wired(
    *,
    batch_id: str,
    tag: str,
    thought_db_context: dict[str, Any] | None,
    repo_observation: dict[str, Any] | None,
    should_plan: bool,
    notes_on_skip: str,
    notes_on_skipped: str,
    notes_on_error: str,
    postprocess: Any | None,
    deps: CheckPlanWiringDeps,
) -> tuple[dict[str, Any], str, str]:
    """Plan checks and append a check_plan record using runner wiring (behavior-preserving)."""

    return plan_checks_and_record(
        batch_id=str(batch_id or ""),
        tag=str(tag or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        mindspec_base=deps.mindspec_base_getter() if callable(deps.mindspec_base_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
        recent_evidence=deps.evidence_window if isinstance(deps.evidence_window, list) else [],
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        should_plan=bool(should_plan),
        notes_on_skip=str(notes_on_skip or ""),
        notes_on_skipped=str(notes_on_skipped or ""),
        notes_on_error=str(notes_on_error or ""),
        evidence_window=deps.evidence_window if isinstance(deps.evidence_window, list) else [],
        postprocess=postprocess,
        deps=CheckPlanFlowDeps(
            empty_check_plan=deps.empty_check_plan,
            evidence_append=deps.evidence_append,
            segment_add=deps.segment_add,
            persist_segment_state=deps.persist_segment_state,
            now_ts=deps.now_ts,
            thread_id=deps.thread_id_getter() if callable(deps.thread_id_getter) else None,
            plan_min_checks_prompt_builder=deps.plan_min_checks_prompt_builder,
            mind_call=deps.mind_call,
        ),
    )
