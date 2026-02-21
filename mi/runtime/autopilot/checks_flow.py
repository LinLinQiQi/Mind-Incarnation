from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PlanChecksAutoAnswerDeps:
    """Callback set for planning checks and optional auto-answer."""

    plan_checks: Callable[..., dict[str, Any]]
    maybe_auto_answer: Callable[..., dict[str, Any]]


def run_plan_checks_and_auto_answer(
    *,
    batch_idx: int,
    batch_id: str,
    summary: dict[str, Any],
    evidence_obj: dict[str, Any],
    repo_obs: dict[str, Any],
    hands_last: str,
    tdb_ctx_batch_obj: dict[str, Any],
    deps: PlanChecksAutoAnswerDeps,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Orchestrate plan_checks + auto_answer in one reusable phase."""

    checks_obj = deps.plan_checks(
        batch_idx=batch_idx,
        batch_id=batch_id,
        summary=summary if isinstance(summary, dict) else {},
        evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        hands_last=hands_last,
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
    )
    auto_answer_obj = deps.maybe_auto_answer(
        batch_idx=batch_idx,
        batch_id=batch_id,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
    )
    return (
        checks_obj if isinstance(checks_obj, dict) else {},
        auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
    )
