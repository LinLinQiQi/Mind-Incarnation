from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.auto_answer_flow import AutoAnswerQueryDeps, query_auto_answer_to_hands


@dataclass(frozen=True)
class AutoAnswerQueryWiringDeps:
    """Wiring bundle for auto_answer_to_hands query (prompt + Mind call)."""

    task: str
    hands_provider: str
    runtime_cfg_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    recent_evidence: list[dict[str, Any]]

    auto_answer_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    empty_auto_answer: Callable[[], dict[str, Any]]


def query_auto_answer_to_hands_wired(
    *,
    batch_idx: int,
    batch_id: str,
    hands_last: str,
    repo_obs: dict[str, Any],
    checks_obj: dict[str, Any],
    tdb_ctx_batch_obj: dict[str, Any],
    deps: AutoAnswerQueryWiringDeps,
) -> tuple[dict[str, Any], str, str]:
    """Query auto_answer_to_hands using runner wiring (behavior-preserving)."""

    return query_auto_answer_to_hands(
        batch_idx=int(batch_idx),
        batch_id=str(batch_id or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        runtime_cfg=deps.runtime_cfg_getter() if callable(deps.runtime_cfg_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        check_plan=checks_obj if isinstance(checks_obj, dict) else {},
        recent_evidence=deps.recent_evidence if isinstance(deps.recent_evidence, list) else [],
        hands_last_message=str(hands_last or ""),
        deps=AutoAnswerQueryDeps(
            auto_answer_prompt_builder=deps.auto_answer_prompt_builder,
            mind_call=deps.mind_call,
            empty_auto_answer=deps.empty_auto_answer,
        ),
    )

