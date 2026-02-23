from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.ask_user_flow import (
    AskUserAutoAnswerAttemptDeps,
    AskUserRedecideDeps,
    DecideAskUserFlowDeps,
    ask_user_auto_answer_attempt as run_ask_user_auto_answer_attempt,
    ask_user_redecide_with_input as run_ask_user_redecide_with_input,
    handle_decide_next_ask_user as run_handle_decide_next_ask_user,
)


@dataclass(frozen=True)
class AskUserAutoAnswerAttemptWiringDeps:
    """Wiring bundle for one ask_user auto-answer attempt."""

    task: str
    hands_provider: str
    runtime_cfg_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    recent_evidence: list[dict[str, Any]]

    empty_auto_answer: Callable[[], dict[str, Any]]
    build_thought_db_context_obj: Callable[[str, list[dict[str, Any]]], dict[str, Any]]
    auto_answer_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    append_auto_answer_record: Callable[..., dict[str, Any]]
    get_check_input: Callable[[dict[str, Any] | None], str]
    join_hands_inputs: Callable[[str, str], str]
    queue_next_input: Callable[..., bool]


def ask_user_auto_answer_attempt_wired(
    *,
    batch_idx: int,
    q: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_obj: dict[str, Any],
    batch_suffix: str,
    tag_suffix: str,
    queue_reason: str,
    note_skipped: str,
    note_error: str,
    deps: AskUserAutoAnswerAttemptWiringDeps,
) -> tuple[bool | None, str]:
    """Try one auto_answer attempt for ask_user, using runner wiring (behavior-preserving)."""

    return run_ask_user_auto_answer_attempt(
        batch_idx=int(batch_idx),
        q=str(q or ""),
        hands_last=str(hands_last or ""),
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        batch_suffix=str(batch_suffix or ""),
        tag_suffix=str(tag_suffix or ""),
        queue_reason=str(queue_reason or ""),
        note_skipped=str(note_skipped or ""),
        note_error=str(note_error or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        runtime_cfg=deps.runtime_cfg_getter() if callable(deps.runtime_cfg_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        recent_evidence=deps.recent_evidence if isinstance(deps.recent_evidence, list) else [],
        deps=AskUserAutoAnswerAttemptDeps(
            empty_auto_answer=deps.empty_auto_answer,
            build_thought_db_context_obj=deps.build_thought_db_context_obj,
            auto_answer_prompt_builder=deps.auto_answer_prompt_builder,
            mind_call=deps.mind_call,
            append_auto_answer_record=deps.append_auto_answer_record,
            get_check_input=deps.get_check_input,
            join_hands_inputs=deps.join_hands_inputs,
            queue_next_input=deps.queue_next_input,
        ),
    )


@dataclass(frozen=True)
class AskUserRedecideWithInputWiringDeps:
    """Wiring bundle for re-deciding after collecting user input."""

    task: str
    hands_provider: str
    runtime_cfg_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    workflow_run: dict[str, Any]
    workflow_load_effective: Callable[[], list[dict[str, Any]]]
    recent_evidence: list[dict[str, Any]]

    empty_auto_answer: Callable[[], dict[str, Any]]
    build_decide_context: Callable[..., Any]
    summarize_thought_db_context: Callable[[Any], dict[str, Any]]
    decide_next_prompt_builder: Callable[..., str]
    load_active_workflow: Callable[..., Any]
    mind_call: Callable[..., tuple[Any, str, str]]
    log_decide_next: Callable[..., dict[str, Any] | None]
    append_decide_record: Callable[[dict[str, Any]], None]
    apply_set_testless_strategy_overlay_update: Callable[..., None]
    handle_learn_suggested: Callable[..., Any]
    get_check_input: Callable[[dict[str, Any] | None], str]
    join_hands_inputs: Callable[[str, str], str]
    queue_next_input: Callable[..., bool]
    set_status: Callable[[str], None]
    set_notes: Callable[[str], None]


def ask_user_redecide_with_input_wired(
    *,
    batch_idx: int,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    answer: str,
    deps: AskUserRedecideWithInputWiringDeps,
) -> tuple[bool, dict[str, Any] | None]:
    """Re-decide after user input using runner wiring (behavior-preserving)."""

    return run_ask_user_redecide_with_input(
        batch_idx=int(batch_idx),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        runtime_cfg=deps.runtime_cfg_getter() if callable(deps.runtime_cfg_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        workflow_run=deps.workflow_run if isinstance(deps.workflow_run, dict) else {},
        workflow_load_effective=deps.workflow_load_effective,
        recent_evidence=deps.recent_evidence if isinstance(deps.recent_evidence, list) else [],
        hands_last=str(hands_last or ""),
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        answer=str(answer or ""),
        deps=AskUserRedecideDeps(
            empty_auto_answer=deps.empty_auto_answer,
            build_decide_context=deps.build_decide_context,
            summarize_thought_db_context=deps.summarize_thought_db_context,
            decide_next_prompt_builder=deps.decide_next_prompt_builder,
            load_active_workflow=deps.load_active_workflow,
            mind_call=deps.mind_call,
            log_decide_next=deps.log_decide_next,
            append_decide_record=deps.append_decide_record,
            apply_set_testless_strategy_overlay_update=deps.apply_set_testless_strategy_overlay_update,
            handle_learn_suggested=deps.handle_learn_suggested,
            get_check_input=deps.get_check_input,
            join_hands_inputs=deps.join_hands_inputs,
            queue_next_input=deps.queue_next_input,
            set_status=deps.set_status,
            set_notes=deps.set_notes,
        ),
    )


@dataclass(frozen=True)
class DecideAskUserWiringDeps:
    """Wiring bundle for decide_next(next_action=ask_user) orchestration."""

    maybe_cross_project_recall: Callable[..., None]
    read_user_answer: Callable[[str], str]
    append_user_input_record: Callable[..., dict[str, Any]]
    set_blocked: Callable[[str], None]

    run_auto_answer_attempt: Callable[..., tuple[bool | None, str]]
    redecide_with_input: Callable[..., bool]


def handle_decide_next_ask_user_wired(
    *,
    batch_idx: int,
    task: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_obj: dict[str, Any],
    decision_obj: dict[str, Any],
    deps: DecideAskUserWiringDeps,
) -> bool:
    """Handle decide_next(next_action=ask_user) using runner wiring (behavior-preserving)."""

    return bool(
        run_handle_decide_next_ask_user(
            batch_idx=int(batch_idx),
            task=str(task or ""),
            hands_last=str(hands_last or ""),
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            decision_obj=decision_obj if isinstance(decision_obj, dict) else {},
            deps=DecideAskUserFlowDeps(
                run_auto_answer_attempt=deps.run_auto_answer_attempt,
                maybe_cross_project_recall=deps.maybe_cross_project_recall,
                read_user_answer=deps.read_user_answer,
                append_user_input_record=deps.append_user_input_record,
                redecide_with_input=deps.redecide_with_input,
                set_blocked=deps.set_blocked,
            ),
        )
    )

