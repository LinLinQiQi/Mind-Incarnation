from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RiskPredecideDeps:
    query_risk: Callable[..., tuple[dict[str, Any], str]]
    record_risk: Callable[..., dict[str, Any]]
    apply_learn_suggested: Callable[..., None]
    maybe_prompt_continue: Callable[..., bool | None]


def query_risk_judge(
    *,
    batch_idx: int,
    batch_id: str,
    risk_signals: list[str],
    hands_last: str,
    tdb_ctx_batch_obj: dict[str, Any],
    task: str,
    hands_provider: str,
    mindspec_base: dict[str, Any],
    project_overlay: dict[str, Any],
    maybe_cross_project_recall: Callable[..., None],
    risk_judge_prompt_builder: Callable[..., str],
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]],
    build_risk_fallback: Callable[[list[str], str], dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Run recall + risk_judge and normalize fallback output."""

    signals = [str(x) for x in risk_signals if str(x).strip()]
    maybe_cross_project_recall(
        batch_id=f"{batch_id}.risk_recall",
        reason="risk_signal",
        query=(" ".join(signals) + "\n" + str(task or "")).strip(),
    )
    risk_prompt = risk_judge_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        risk_signals=signals,
        hands_last_message=str(hands_last or ""),
    )
    risk_obj, risk_mind_ref, risk_state = mind_call(
        schema_filename="risk_judge.json",
        prompt=risk_prompt,
        tag=f"risk_b{batch_idx}",
        batch_id=batch_id,
    )
    if risk_obj is None or not isinstance(risk_obj, dict):
        risk_obj = build_risk_fallback(signals, state=str(risk_state or ""))
    return risk_obj if isinstance(risk_obj, dict) else {}, str(risk_mind_ref or "")


def maybe_prompt_risk_continue(
    *,
    risk_obj: dict[str, Any],
    should_prompt_risk_user: Callable[..., bool],
    violation_response_cfg: dict[str, Any],
    read_user_answer: Callable[[str], str],
) -> bool | None:
    """Apply runtime violation policy; return False when user blocks run."""

    ro = risk_obj if isinstance(risk_obj, dict) else {}
    severity = str(ro.get("severity") or "low")
    cat = str(ro.get("category") or "other")
    should_prompt = should_prompt_risk_user(
        risk_obj=ro,
        violation_response_cfg=violation_response_cfg if isinstance(violation_response_cfg, dict) else {},
    )
    if not should_prompt:
        return None

    mitig = ro.get("mitigation") or []
    mitig_s = "; ".join([str(x) for x in mitig if str(x).strip()][:3])
    q = f"Risk action detected (category={cat}, severity={severity}). Continue? (y/N)\nMitigation: {mitig_s}"
    answer = read_user_answer(q)
    if answer.strip().lower() not in ("y", "yes"):
        return False
    return None


def run_risk_predecide(
    *,
    batch_idx: int,
    batch_id: str,
    risk_signals: list[str],
    hands_last: str,
    tdb_ctx_batch_obj: dict[str, Any],
    deps: RiskPredecideDeps,
) -> bool | None:
    """Run risk_judge, persist evidence, apply learning hints, and enforce policy."""

    signals = [str(x) for x in risk_signals if str(x).strip()]
    risk_obj, risk_mind_ref = deps.query_risk(
        batch_idx=batch_idx,
        batch_id=batch_id,
        risk_signals=signals,
        hands_last=hands_last,
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
    )
    risk_rec = deps.record_risk(
        batch_idx=batch_idx,
        risk_signals=signals,
        risk_obj=risk_obj if isinstance(risk_obj, dict) else {},
        risk_mind_ref=risk_mind_ref,
    )
    deps.apply_learn_suggested(
        batch_idx=batch_idx,
        risk_obj=risk_obj if isinstance(risk_obj, dict) else {},
        risk_mind_ref=risk_mind_ref,
        risk_event_id=str((risk_rec if isinstance(risk_rec, dict) else {}).get("event_id") or "").strip(),
    )
    return deps.maybe_prompt_continue(risk_obj=risk_obj if isinstance(risk_obj, dict) else {})
