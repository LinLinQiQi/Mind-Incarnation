from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ClaimMiningDeps:
    """Dependencies for checkpoint-only atomic Claim mining."""

    build_decide_context: Callable[..., Any]
    mine_claims_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]]
    apply_mined_output: Callable[..., dict[str, Any]]
    evidence_append: Callable[[dict[str, Any]], Any]
    now_ts: Callable[[], str]


def _allowed_event_ids(seg_evidence: list[dict[str, Any]]) -> list[str]:
    allowed: list[str] = []
    seen: set[str] = set()
    for rec in seg_evidence or []:
        if not isinstance(rec, dict):
            continue
        eid = rec.get("event_id")
        if not isinstance(eid, str):
            continue
        e = eid.strip()
        if not e or e in seen:
            continue
        seen.add(e)
        allowed.append(e)
    return allowed


def mine_claims_from_segment(
    *,
    enabled: bool,
    executed_batches: int,
    max_claims: int,
    min_confidence: float,
    seg_evidence: list[dict[str, Any]],
    base_batch_id: str,
    source: str,
    status: str,
    notes: str,
    task: str,
    hands_provider: str,
    mindspec_base: dict[str, Any],
    project_overlay: dict[str, Any],
    thread_id: str,
    segment_id: str,
    deps: ClaimMiningDeps,
) -> None:
    """Mine high-signal atomic Claims into Thought DB (checkpoint-only; best-effort)."""

    if not bool(enabled):
        return
    if int(executed_batches) <= 0:
        return
    if int(max_claims) <= 0:
        return

    allowed = _allowed_event_ids(seg_evidence)
    allowed_set = set(allowed)

    mine_notes = f"source={source} status={status} batches={executed_batches} notes={notes}"
    tdb_ctx = deps.build_decide_context(hands_last_message="", recent_evidence=(seg_evidence or [])[-8:])
    tdb_ctx_obj = tdb_ctx.to_prompt_obj() if hasattr(tdb_ctx, "to_prompt_obj") else {}
    if not isinstance(tdb_ctx_obj, dict):
        tdb_ctx_obj = {}

    prompt = deps.mine_claims_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=tdb_ctx_obj,
        segment_evidence=seg_evidence,
        allowed_event_ids=allowed,
        min_confidence=float(min_confidence),
        max_claims=int(max_claims),
        notes=mine_notes,
    )
    out, mind_ref, state = deps.mind_call(
        schema_filename="mine_claims.json",
        prompt=prompt,
        tag=f"mine_claims:{base_batch_id}",
        batch_id=f"{base_batch_id}.claim_mining",
    )

    applied: dict[str, Any] = {"written": [], "skipped": []}
    if isinstance(out, dict):
        applied = deps.apply_mined_output(
            output=out,
            allowed_event_ids=allowed_set,
            min_confidence=float(min_confidence),
            max_claims=int(max_claims),
        )

    deps.evidence_append(
        {
            "kind": "claim_mining",
            "batch_id": f"{base_batch_id}.claim_mining",
            "ts": deps.now_ts(),
            "thread_id": str(thread_id or ""),
            "segment_id": str(segment_id or ""),
            "state": state,
            "mind_transcript_ref": mind_ref,
            "notes": mine_notes,
            "config": {
                "min_confidence": float(min_confidence),
                "max_claims_per_checkpoint": int(max_claims),
            },
            "output": out if isinstance(out, dict) else {},
            "applied": applied,
        }
    )

