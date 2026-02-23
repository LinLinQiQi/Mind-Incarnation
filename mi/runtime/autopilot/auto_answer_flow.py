from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AutoAnswerQueryDeps:
    """Dependencies for auto_answer_to_hands prompt/query normalization."""

    auto_answer_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    empty_auto_answer: Callable[[], dict[str, Any]]


def query_auto_answer_to_hands(
    *,
    batch_idx: int,
    batch_id: str,
    task: str,
    hands_provider: str,
    runtime_cfg: dict[str, Any],
    project_overlay: dict[str, Any],
    thought_db_context: dict[str, Any],
    repo_observation: dict[str, Any],
    check_plan: dict[str, Any],
    recent_evidence: list[dict[str, Any]],
    hands_last_message: str,
    deps: AutoAnswerQueryDeps,
) -> tuple[dict[str, Any], str, str]:
    """Query auto_answer_to_hands and normalize fallback output."""

    prompt = deps.auto_answer_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg=runtime_cfg if isinstance(runtime_cfg, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=thought_db_context if isinstance(thought_db_context, dict) else {},
        repo_observation=repo_observation if isinstance(repo_observation, dict) else {},
        check_plan=check_plan if isinstance(check_plan, dict) else {},
        recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
        hands_last_message=str(hands_last_message or ""),
    )
    aa_obj, mind_ref, state = deps.mind_call(
        schema_filename="auto_answer_to_hands.json",
        prompt=prompt,
        tag=f"autoanswer_b{batch_idx}",
        batch_id=batch_id,
    )

    if aa_obj is None:
        auto_answer_obj = deps.empty_auto_answer()
        if str(state or "") == "skipped":
            auto_answer_obj["notes"] = "skipped: mind_circuit_open (auto_answer_to_hands)"
        else:
            auto_answer_obj["notes"] = "mind_error: auto_answer_to_hands failed; see EvidenceLog kind=mind_error"
    else:
        auto_answer_obj = aa_obj if isinstance(aa_obj, dict) else deps.empty_auto_answer()

    return (
        auto_answer_obj if isinstance(auto_answer_obj, dict) else deps.empty_auto_answer(),
        str(mind_ref or ""),
        str(state or ""),
    )
