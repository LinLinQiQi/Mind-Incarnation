from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.predecide_user_flow import (
    PredecideNeedsUserDeps,
    PredecidePromptUserDeps,
    PredecideQueueWithChecksDeps,
    PredecideRetryAutoAnswerDeps,
    handle_auto_answer_needs_user as run_handle_auto_answer_needs_user,
    prompt_user_then_queue as run_prompt_user_then_queue,
    retry_auto_answer_after_recall as run_retry_auto_answer_after_recall,
    try_queue_answer_with_checks as run_try_queue_answer_with_checks,
)


@dataclass(frozen=True)
class PredecideUserWiringDeps:
    """Wiring bundle for pre-decide user input resolution (auto-answer -> recall -> prompt)."""

    task: str
    hands_provider: str
    runtime_cfg_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    recent_evidence: list[dict[str, Any]]

    empty_auto_answer: Callable[[], dict[str, Any]]
    maybe_cross_project_recall: Callable[..., None]
    auto_answer_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    append_auto_answer_record: Callable[..., dict[str, Any]]

    get_check_input: Callable[[dict[str, Any] | None], str]
    join_hands_inputs: Callable[[str, str], str]
    queue_next_input: Callable[..., bool]

    read_user_answer: Callable[[str], str]
    append_user_input_record: Callable[..., dict[str, Any]]
    set_blocked: Callable[[str], None]


def retry_auto_answer_after_recall_wired(
    *,
    batch_idx: int,
    question: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    deps: PredecideUserWiringDeps,
) -> tuple[dict[str, Any], str]:
    """Retry auto_answer after recall using runner wiring (behavior-preserving)."""

    return run_retry_auto_answer_after_recall(
        batch_idx=int(batch_idx),
        question=str(question or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        runtime_cfg=deps.runtime_cfg_getter() if callable(deps.runtime_cfg_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        recent_evidence=deps.recent_evidence if isinstance(deps.recent_evidence, list) else [],
        deps=PredecideRetryAutoAnswerDeps(
            empty_auto_answer=deps.empty_auto_answer,
            maybe_cross_project_recall=deps.maybe_cross_project_recall,
            auto_answer_prompt_builder=deps.auto_answer_prompt_builder,
            mind_call=deps.mind_call,
            append_auto_answer_record=deps.append_auto_answer_record,
        ),
    )


def try_queue_answer_with_checks_wired(
    *,
    batch_id: str,
    queue_reason: str,
    answer_text: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    deps: PredecideUserWiringDeps,
) -> bool | None:
    """Queue answer + checks using runner wiring (behavior-preserving)."""

    return run_try_queue_answer_with_checks(
        batch_id=str(batch_id or ""),
        queue_reason=str(queue_reason or ""),
        answer_text=str(answer_text or ""),
        hands_last=str(hands_last or ""),
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        deps=PredecideQueueWithChecksDeps(
            get_check_input=deps.get_check_input,
            join_hands_inputs=deps.join_hands_inputs,
            queue_next_input=deps.queue_next_input,
        ),
    )


def prompt_user_then_queue_wired(
    *,
    batch_idx: int,
    question: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    deps: PredecideUserWiringDeps,
) -> bool:
    """Ask the user and queue answer (+ checks) using runner wiring (behavior-preserving)."""

    return bool(
        run_prompt_user_then_queue(
            batch_idx=int(batch_idx),
            question=str(question or ""),
            hands_last=str(hands_last or ""),
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            deps=PredecidePromptUserDeps(
                read_user_answer=deps.read_user_answer,
                append_user_input_record=deps.append_user_input_record,
                set_blocked=deps.set_blocked,
                try_queue_answer_with_checks=lambda **kwargs: try_queue_answer_with_checks_wired(
                    batch_id=str(kwargs.get("batch_id") or ""),
                    queue_reason=str(kwargs.get("queue_reason") or ""),
                    answer_text=str(kwargs.get("answer_text") or ""),
                    hands_last=str(kwargs.get("hands_last") or ""),
                    repo_obs=(kwargs.get("repo_obs") if isinstance(kwargs.get("repo_obs"), dict) else {}),
                    checks_obj=(kwargs.get("checks_obj") if isinstance(kwargs.get("checks_obj"), dict) else {}),
                    tdb_ctx_batch_obj=(kwargs.get("tdb_ctx_batch_obj") if isinstance(kwargs.get("tdb_ctx_batch_obj"), dict) else {}),
                    deps=deps,
                ),
            ),
        )
    )


def handle_auto_answer_needs_user_wired(
    *,
    batch_idx: int,
    hands_last: str,
    repo_obs: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    checks_obj: dict[str, Any],
    auto_answer_obj: dict[str, Any],
    deps: PredecideUserWiringDeps,
) -> tuple[bool, dict[str, Any]]:
    """Handle pre-decide branch where initial auto_answer requests user input."""

    return run_handle_auto_answer_needs_user(
        batch_idx=int(batch_idx),
        hands_last=str(hands_last or ""),
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
        deps=PredecideNeedsUserDeps(
            retry_auto_answer_after_recall=lambda **kwargs: retry_auto_answer_after_recall_wired(
                batch_idx=int(kwargs.get("batch_idx") or batch_idx),
                question=str(kwargs.get("question") or ""),
                repo_obs=(kwargs.get("repo_obs") if isinstance(kwargs.get("repo_obs"), dict) else {}),
                checks_obj=(kwargs.get("checks_obj") if isinstance(kwargs.get("checks_obj"), dict) else {}),
                tdb_ctx_batch_obj=(kwargs.get("tdb_ctx_batch_obj") if isinstance(kwargs.get("tdb_ctx_batch_obj"), dict) else {}),
                deps=deps,
            ),
            try_queue_answer_with_checks=lambda **kwargs: try_queue_answer_with_checks_wired(
                batch_id=str(kwargs.get("batch_id") or ""),
                queue_reason=str(kwargs.get("queue_reason") or ""),
                answer_text=str(kwargs.get("answer_text") or ""),
                hands_last=str(kwargs.get("hands_last") or ""),
                repo_obs=(kwargs.get("repo_obs") if isinstance(kwargs.get("repo_obs"), dict) else {}),
                checks_obj=(kwargs.get("checks_obj") if isinstance(kwargs.get("checks_obj"), dict) else {}),
                tdb_ctx_batch_obj=(kwargs.get("tdb_ctx_batch_obj") if isinstance(kwargs.get("tdb_ctx_batch_obj"), dict) else {}),
                deps=deps,
            ),
            prompt_user_then_queue=lambda **kwargs: prompt_user_then_queue_wired(
                batch_idx=int(kwargs.get("batch_idx") or batch_idx),
                question=str(kwargs.get("question") or ""),
                hands_last=str(kwargs.get("hands_last") or ""),
                repo_obs=(kwargs.get("repo_obs") if isinstance(kwargs.get("repo_obs"), dict) else {}),
                checks_obj=(kwargs.get("checks_obj") if isinstance(kwargs.get("checks_obj"), dict) else {}),
                tdb_ctx_batch_obj=(kwargs.get("tdb_ctx_batch_obj") if isinstance(kwargs.get("tdb_ctx_batch_obj"), dict) else {}),
                deps=deps,
            ),
        ),
    )

