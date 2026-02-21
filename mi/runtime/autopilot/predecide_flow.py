from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .batch_context import BatchExecutionContext
from .batch_pipeline import PreactionDecision
from .checks_flow import PlanChecksAutoAnswerDeps, run_plan_checks_and_auto_answer
from .extract_flow import ExtractEvidenceDeps, run_extract_evidence_phase
from .preaction_flow import PreactionPhaseDeps, run_preaction_phase
from .risk_flow import WorkflowRiskPhaseDeps, run_workflow_and_risk_phase


@dataclass(frozen=True)
class BatchPredecideDeps:
    """Dependencies for one full pre-decide batch orchestration."""

    build_context: Callable[[int], BatchExecutionContext]
    run_hands: Callable[..., Any]
    observe_repo: Callable[[], dict[str, Any]]
    dict_or_empty: Callable[[Any], dict[str, Any]]
    extract_deps: ExtractEvidenceDeps
    workflow_risk_deps: WorkflowRiskPhaseDeps
    checks_deps: PlanChecksAutoAnswerDeps
    preaction_deps: PreactionPhaseDeps


@dataclass(frozen=True)
class BatchPredecideResult:
    """Pre-decide result payload with concrete batch id."""

    batch_id: str
    out: bool | PreactionDecision


def run_batch_predecide(*, batch_idx: int, deps: BatchPredecideDeps) -> BatchPredecideResult:
    """Execute pre-decide phases for one batch (behavior-preserving orchestration)."""

    try:
        ctx = deps.build_context(batch_idx=batch_idx)
    except TypeError:
        ctx = deps.build_context(batch_idx)
    batch_id = str(ctx.batch_id or f"b{batch_idx}")
    result = deps.run_hands(ctx=ctx)

    repo_obs = deps.observe_repo()
    summary, evidence_obj, hands_last, tdb_ctx_batch_obj = run_extract_evidence_phase(
        batch_idx=batch_idx,
        batch_id=batch_id,
        ctx=ctx,
        result=result,
        repo_obs=deps.dict_or_empty(repo_obs),
        deps=deps.extract_deps,
    )

    risk_out = run_workflow_and_risk_phase(
        batch_idx=batch_idx,
        batch_id=batch_id,
        result=result,
        summary=deps.dict_or_empty(summary),
        evidence_obj=deps.dict_or_empty(evidence_obj),
        repo_obs=deps.dict_or_empty(repo_obs),
        hands_last=hands_last,
        tdb_ctx_batch_obj=deps.dict_or_empty(tdb_ctx_batch_obj),
        ctx=ctx,
        deps=deps.workflow_risk_deps,
    )
    if isinstance(risk_out, bool):
        return BatchPredecideResult(batch_id=batch_id, out=risk_out)

    checks_obj, auto_answer_obj = run_plan_checks_and_auto_answer(
        batch_idx=batch_idx,
        batch_id=batch_id,
        summary=deps.dict_or_empty(summary),
        evidence_obj=deps.dict_or_empty(evidence_obj),
        repo_obs=deps.dict_or_empty(repo_obs),
        hands_last=hands_last,
        tdb_ctx_batch_obj=deps.dict_or_empty(tdb_ctx_batch_obj),
        deps=deps.checks_deps,
    )

    pre, checks_obj = run_preaction_phase(
        batch_idx=batch_idx,
        hands_last=hands_last,
        repo_obs=deps.dict_or_empty(repo_obs),
        tdb_ctx_batch_obj=deps.dict_or_empty(tdb_ctx_batch_obj),
        checks_obj=deps.dict_or_empty(checks_obj),
        auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else {},
        deps=deps.preaction_deps,
    )
    if pre is not None:
        return BatchPredecideResult(batch_id=batch_id, out=pre)

    return BatchPredecideResult(
        batch_id=batch_id,
        out=PreactionDecision(
            final_continue=None,
            repo_obs=deps.dict_or_empty(repo_obs),
            hands_last=str(hands_last or ""),
            checks_obj=deps.dict_or_empty(checks_obj),
            auto_answer_obj=auto_answer_obj if isinstance(auto_answer_obj, dict) else deps.preaction_deps.empty_auto_answer(),
        ),
    )
