from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PreactionDecision:
    """Result of pre-decide arbitration in one batch.

    final_continue:
    - True/False => batch result is finalized and runner should return this value.
    - None => fall through to decide_next phase.
    """

    final_continue: bool | None
    checks_obj: dict[str, Any]
    auto_answer_obj: dict[str, Any]
    repo_obs: dict[str, Any] | None = None
    hands_last: str = ""


def join_hands_inputs(*parts: str) -> str:
    """Join non-empty instructions sent to Hands."""

    return "\n\n".join([p for p in parts if isinstance(p, str) and p.strip()])


def compose_check_plan_log(checks_obj: dict[str, Any] | None) -> str:
    obj = checks_obj if isinstance(checks_obj, dict) else {}
    return (
        "plan_min_checks "
        + f"should_run_checks={bool(obj.get('should_run_checks', False))} "
        + f"needs_testless_strategy={bool(obj.get('needs_testless_strategy', False))}"
    )


def compose_auto_answer_log(*, state: str, auto_answer_obj: dict[str, Any] | None) -> str:
    obj = auto_answer_obj if isinstance(auto_answer_obj, dict) else {}
    cf = obj.get("confidence")
    try:
        cf_s = f"{float(cf):.2f}" if cf is not None else ""
    except Exception:
        cf_s = str(cf or "")

    line = (
        "auto_answer_to_hands "
        + f"state={str(state or '')} "
        + f"should_answer={bool(obj.get('should_answer', False))} "
        + f"needs_user_input={bool(obj.get('needs_user_input', False))} "
    )
    if cf_s:
        line += f"confidence={cf_s}"
    return line.rstrip()
