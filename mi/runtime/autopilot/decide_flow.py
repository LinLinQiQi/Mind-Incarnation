from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DecidePhaseDeps:
    """Callback set used by decide_next phase orchestration."""

    query: Callable[..., tuple[dict[str, Any] | None, str, str, dict[str, Any], dict[str, Any]]]
    handle_missing: Callable[..., bool]
    record_effects: Callable[..., tuple[str, dict[str, Any] | None]]
    route_action: Callable[..., bool]


def run_decide_next_phase(
    *,
    batch_idx: int,
    batch_id: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    auto_answer_obj: dict[str, Any],
    deps: DecidePhaseDeps,
) -> bool:
    """Run decide_next orchestration with pluggable callbacks."""

    decision_obj, decision_mind_ref, decision_state, tdb_ctx_obj, tdb_ctx_summary = deps.query(
        batch_idx=batch_idx,
        batch_id=batch_id,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
    )
    if decision_obj is None or not isinstance(decision_obj, dict):
        return deps.handle_missing(
            batch_idx=batch_idx,
            hands_last=hands_last,
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
            tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
            decision_state=str(decision_state or ""),
        )

    next_action, _decide_rec = deps.record_effects(
        batch_idx=batch_idx,
        decision_obj=decision_obj,
        decision_mind_ref=decision_mind_ref,
        tdb_ctx_summary=tdb_ctx_summary if isinstance(tdb_ctx_summary, dict) else {},
    )
    return deps.route_action(
        batch_idx=batch_idx,
        next_action=next_action,
        hands_last=hands_last,
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        checks_obj=checks_obj if isinstance(checks_obj, dict) else {},
        tdb_ctx_obj=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        decision_obj=decision_obj,
    )
