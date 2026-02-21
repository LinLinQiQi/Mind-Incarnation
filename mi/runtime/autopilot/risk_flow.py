from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .batch_context import BatchExecutionContext


@dataclass(frozen=True)
class WorkflowRiskPhaseDeps:
    """Callback set for workflow progress + risk orchestration."""

    apply_workflow_progress: Callable[..., None]
    detect_risk_signals: Callable[..., list[str]]
    judge_and_handle_risk: Callable[..., bool | None]


def run_workflow_and_risk_phase(
    *,
    batch_idx: int,
    batch_id: str,
    result: Any,
    summary: dict[str, Any],
    evidence_obj: dict[str, Any],
    repo_obs: dict[str, Any],
    hands_last: str,
    tdb_ctx_batch_obj: dict[str, Any],
    ctx: BatchExecutionContext,
    deps: WorkflowRiskPhaseDeps,
) -> bool | None:
    """Run workflow progress first, then risk gate when signals are present."""

    deps.apply_workflow_progress(
        batch_idx=batch_idx,
        batch_id=batch_id,
        summary=summary if isinstance(summary, dict) else {},
        evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
        repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
        hands_last=hands_last,
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        ctx=ctx,
    )
    risk_signals = deps.detect_risk_signals(result=result, ctx=ctx)
    if not risk_signals:
        return None
    return deps.judge_and_handle_risk(
        batch_idx=batch_idx,
        batch_id=batch_id,
        risk_signals=risk_signals,
        hands_last=hands_last,
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
    )
