from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mi.runtime import autopilot as AP


@dataclass(frozen=True)
class WorkflowRiskWiringBundle:
    """Runner wiring bundle for workflow_progress + risk gate phase (behavior-preserving)."""

    deps: AP.WorkflowRiskPhaseDeps
    apply_phase: Callable[..., bool | None]


def build_workflow_risk_wiring_bundle(
    *,
    apply_workflow_progress: Callable[..., None],
    detect_risk_signals: Callable[..., list[str]],
    judge_and_handle_risk: Callable[..., bool | None],
) -> WorkflowRiskWiringBundle:
    """Build the shared callback set used by both runner wrapper + predecide service deps."""

    deps = AP.WorkflowRiskPhaseDeps(
        apply_workflow_progress=apply_workflow_progress,
        detect_risk_signals=detect_risk_signals,
        judge_and_handle_risk=judge_and_handle_risk,
    )

    def apply_phase(
        *,
        batch_idx: int,
        batch_id: str,
        result: Any,
        summary: dict[str, Any],
        evidence_obj: dict[str, Any],
        repo_obs: dict[str, Any],
        hands_last: str,
        tdb_ctx_batch_obj: dict[str, Any],
        ctx: AP.BatchExecutionContext,
    ) -> bool | None:
        return AP.run_workflow_and_risk_phase(
            batch_idx=int(batch_idx),
            batch_id=str(batch_id or ""),
            result=result,
            summary=summary if isinstance(summary, dict) else {},
            evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
            repo_obs=repo_obs if isinstance(repo_obs, dict) else {},
            hands_last=str(hands_last or ""),
            tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
            ctx=ctx,
            deps=deps,
        )

    return WorkflowRiskWiringBundle(deps=deps, apply_phase=apply_phase)
