from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.loop_break_checks_flow import (
    LoopBreakChecksDeps,
    loop_break_get_checks_input as run_loop_break_get_checks_input,
)


@dataclass(frozen=True)
class LoopBreakChecksWiringDeps:
    """Wiring bundle for loop-break check input computation."""

    get_check_input: Callable[[dict[str, Any] | None], str]
    plan_checks_and_record: Callable[..., tuple[dict[str, Any], str, str]]
    resolve_tls_for_checks: Callable[..., tuple[dict[str, Any], str]]
    empty_check_plan: Callable[[], dict[str, Any]]
    notes_on_skipped: str
    notes_on_error: str


def loop_break_get_checks_input_wired(
    *,
    base_batch_id: str,
    hands_last_message: str,
    thought_db_context: dict[str, Any] | None,
    repo_observation: dict[str, Any] | None,
    existing_check_plan: dict[str, Any] | None,
    deps: LoopBreakChecksWiringDeps,
) -> tuple[str, str]:
    """Return checks input text for loop_break run_checks_then_continue (behavior-preserving)."""

    return run_loop_break_get_checks_input(
        base_batch_id=str(base_batch_id or ""),
        hands_last_message=str(hands_last_message or ""),
        thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        existing_check_plan=existing_check_plan if isinstance(existing_check_plan, dict) else None,
        notes_on_skipped=str(deps.notes_on_skipped or ""),
        notes_on_error=str(deps.notes_on_error or ""),
        deps=LoopBreakChecksDeps(
            get_check_input=deps.get_check_input,
            plan_checks_and_record=deps.plan_checks_and_record,
            resolve_tls_for_checks=deps.resolve_tls_for_checks,
            empty_check_plan=deps.empty_check_plan,
        ),
    )

