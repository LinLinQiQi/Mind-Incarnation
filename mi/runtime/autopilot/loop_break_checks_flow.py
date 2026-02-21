from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class LoopBreakChecksDeps:
    """Dependencies for loop-break check input computation."""

    get_check_input: Callable[[dict[str, Any] | None], str]
    plan_checks_and_record: Callable[..., tuple[dict[str, Any], str, str]]
    resolve_tls_for_checks: Callable[..., tuple[dict[str, Any], str]]
    empty_check_plan: Callable[[], dict[str, Any]]


def loop_break_get_checks_input(
    *,
    base_batch_id: str,
    hands_last_message: str,
    thought_db_context: dict[str, Any] | None,
    repo_observation: dict[str, Any] | None,
    existing_check_plan: dict[str, Any] | None,
    notes_on_skipped: str,
    notes_on_error: str,
    deps: LoopBreakChecksDeps,
) -> tuple[str, str]:
    """Return checks input text for loop_break run_checks_then_continue (best-effort).

    Returns: (checks_input_text, block_reason). block_reason=="" means OK.
    """

    chk_text = deps.get_check_input(existing_check_plan if isinstance(existing_check_plan, dict) else None)
    if chk_text:
        return chk_text, ""

    checks_obj2, _checks_ref2, _state2 = deps.plan_checks_and_record(
        batch_id=f"{base_batch_id}.loop_break_checks",
        tag=f"checks_loopbreak:{base_batch_id}",
        thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        should_plan=True,
        notes_on_skip="",
        notes_on_skipped=notes_on_skipped,
        notes_on_error=notes_on_error,
    )

    checks_obj2, block_reason = deps.resolve_tls_for_checks(
        checks_obj=checks_obj2 if isinstance(checks_obj2, dict) else deps.empty_check_plan(),
        hands_last_message=hands_last_message,
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        user_input_batch_id=f"{base_batch_id}.loop_break",
        batch_id_after_testless=f"{base_batch_id}.loop_break_after_testless",
        batch_id_after_tls_claim=f"{base_batch_id}.loop_break_after_tls_claim",
        tag_after_testless=f"checks_loopbreak_after_tls:{base_batch_id}",
        tag_after_tls_claim=f"checks_loopbreak_after_tls_claim:{base_batch_id}",
        notes_prefix="loop_break",
        source="user_input:testless_strategy(loop_break)",
        rationale="user provided testless verification strategy (loop_break)",
    )
    if block_reason:
        return "", str(block_reason or "")

    return deps.get_check_input(checks_obj2 if isinstance(checks_obj2, dict) else None), ""

