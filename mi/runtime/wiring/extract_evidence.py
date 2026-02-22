from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .evidence_record import EvidenceRecordWiringDeps, append_evidence_with_tracking_wired


@dataclass(frozen=True)
class ExtractEvidenceContextResult:
    """Normalized outputs from extract_evidence + Thought DB context build."""

    summary: dict[str, Any]
    evidence_obj: dict[str, Any]
    hands_last: str
    tdb_ctx_batch_obj: dict[str, Any]
    evidence_rec: dict[str, Any]


@dataclass(frozen=True)
class ExtractEvidenceContextWiringDeps:
    """Wiring bundle for extract_evidence prompt/query + evidence/context tracking."""

    task: str
    hands_provider: str

    batch_summary_fn: Callable[[Any], dict[str, Any]]
    extract_evidence_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[Any, str, str]]
    empty_evidence_obj: Callable[..., dict[str, Any]]
    extract_evidence_counts: Callable[[dict[str, Any] | None], dict[str, int]]
    emit_prefixed: Callable[[str, str], None]

    evidence_record_deps: EvidenceRecordWiringDeps
    build_decide_context: Callable[..., Any]


def extract_evidence_and_context_wired(
    *,
    batch_idx: int,
    batch_id: str,
    ctx: Any,
    result: Any,
    repo_obs: dict[str, Any],
    deps: ExtractEvidenceContextWiringDeps,
) -> ExtractEvidenceContextResult:
    """Run extract_evidence and build Thought DB context using runner wiring (behavior-preserving)."""

    summary = deps.batch_summary_fn(result)

    extract_prompt = deps.extract_evidence_prompt_builder(
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        light_injection=str(getattr(ctx, "light_injection", "") or ""),
        batch_input=str(getattr(ctx, "batch_input", "") or ""),
        hands_batch_summary=summary if isinstance(summary, dict) else {},
        repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
    )
    evidence_obj, evidence_mind_ref, evidence_state = deps.mind_call(
        schema_filename="extract_evidence.json",
        prompt=extract_prompt,
        tag=f"extract_b{int(batch_idx)}",
        batch_id=str(batch_id or ""),
    )
    if evidence_obj is None:
        if str(evidence_state or "") == "skipped":
            evidence_obj = deps.empty_evidence_obj(note="mind_circuit_open: extract_evidence skipped")
        else:
            evidence_obj = deps.empty_evidence_obj(note="mind_error: extract_evidence failed; see EvidenceLog kind=mind_error")

    counts = deps.extract_evidence_counts(evidence_obj if isinstance(evidence_obj, dict) else None)
    deps.emit_prefixed(
        "[mi]",
        "extract_evidence "
        + f"state={str(evidence_state or '')} "
        + f"facts={counts['facts']} actions={counts['actions']} "
        + f"results={counts['results']} unknowns={counts['unknowns']} risk_signals={counts['risk_signals']}",
    )

    evidence_rec = append_evidence_with_tracking_wired(
        batch_id=str(batch_id or ""),
        hands_transcript_ref=str(getattr(ctx, "hands_transcript", "") or ""),
        mind_transcript_ref=str(evidence_mind_ref or ""),
        mi_input=str(getattr(ctx, "batch_input", "") or ""),
        transcript_observation=(summary if isinstance(summary, dict) else {}).get("transcript_observation") or {},
        repo_observation=repo_obs if isinstance(repo_obs, dict) else {},
        evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else {},
        deps=deps.evidence_record_deps,
    )

    hands_last = str(getattr(result, "last_agent_message", lambda: "")() or "")
    tdb_ctx_batch = deps.build_decide_context(hands_last_message=hands_last, recent_evidence=deps.evidence_record_deps.evidence_window)
    tdb_ctx_batch_obj = tdb_ctx_batch.to_prompt_obj() if hasattr(tdb_ctx_batch, "to_prompt_obj") else {}

    return ExtractEvidenceContextResult(
        summary=summary if isinstance(summary, dict) else {},
        evidence_obj=evidence_obj if isinstance(evidence_obj, dict) else deps.empty_evidence_obj(),
        hands_last=hands_last,
        tdb_ctx_batch_obj=tdb_ctx_batch_obj if isinstance(tdb_ctx_batch_obj, dict) else {},
        evidence_rec=evidence_rec if isinstance(evidence_rec, dict) else {},
    )
