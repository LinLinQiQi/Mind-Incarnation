from __future__ import annotations

from typing import Any, Callable

from ...core.storage import now_rfc3339
from ...runtime.prompts import why_trace_prompt
from ...thoughtdb.why import collect_candidate_claims_for_target, query_from_evidence_event


def maybe_run_why_trace_on_run_end(
    *,
    enabled: bool,
    executed_batches: int,
    last_batch_id: str,
    last_decide_next_rec: dict[str, Any] | None,
    last_evidence_rec: dict[str, Any] | None,
    tdb: Any,
    mem_service: Any,
    project_paths: Any,
    why_top_k: int,
    why_write_edges: bool,
    why_min_write_conf: float,
    mind_call: Callable[..., tuple[Any, str, str]],
    evw: Any,
    thread_id: str | None,
) -> None:
    """Best-effort run-end WhyTrace materialization for auditability."""

    if not enabled or executed_batches <= 0 or not last_batch_id:
        return

    target_obj = last_decide_next_rec if isinstance(last_decide_next_rec, dict) else last_evidence_rec
    ev_id = str((target_obj or {}).get("event_id") or "").strip() if isinstance(target_obj, dict) else ""
    if not isinstance(target_obj, dict) or not ev_id:
        return

    as_of_ts = now_rfc3339()
    query = query_from_evidence_event(target_obj)
    candidates = collect_candidate_claims_for_target(
        tdb=tdb,
        mem=mem_service,
        project_paths=project_paths,
        target_obj=target_obj,
        query=query,
        top_k=why_top_k,
        as_of_ts=as_of_ts,
        target_event_id=ev_id,
    )

    kind = str(target_obj.get("kind") or "").strip()
    if not kind:
        kind = "evidence_item"
    target = {
        "target_type": "evidence_event",
        "event_id": ev_id,
        "evidence_kind": kind,
        "batch_id": str(target_obj.get("batch_id") or last_batch_id).strip(),
    }

    why_state = "ok"
    why_ref = ""
    out: dict[str, Any] = {"status": "insufficient", "confidence": 0.0, "chosen_claim_ids": [], "explanation": "", "notes": "no candidates"}
    if candidates:
        prompt = why_trace_prompt(target=target, as_of_ts=as_of_ts, candidate_claims=candidates, notes="auto:run_end")
        why_obj, why_ref, why_state = mind_call(
            schema_filename="why_trace.json",
            prompt=prompt,
            tag=f"why_trace:{last_batch_id}",
            batch_id=f"{last_batch_id}.why_trace",
        )
        if isinstance(why_obj, dict):
            out = why_obj
        else:
            out = {
                "status": "insufficient",
                "confidence": 0.0,
                "chosen_claim_ids": [],
                "explanation": "",
                "notes": ("skipped: mind_circuit_open (why_trace)" if why_state == "skipped" else "mind_error: why_trace failed; see EvidenceLog kind=mind_error"),
            }

    cand_ids = {str(c.get("claim_id") or "").strip() for c in candidates if isinstance(c, dict) and str(c.get("claim_id") or "").strip()}
    raw_chosen = out.get("chosen_claim_ids") if isinstance(out.get("chosen_claim_ids"), list) else []
    chosen2: list[str] = []
    seen: set[str] = set()
    for x in raw_chosen:
        if not isinstance(x, str):
            continue
        cid = x.strip()
        if not cid or cid in seen or cid not in cand_ids:
            continue
        seen.add(cid)
        chosen2.append(cid)
        if len(chosen2) >= 10:
            break
    out["chosen_claim_ids"] = chosen2

    written_edge_ids: list[str] = []
    if why_write_edges and candidates and why_state == "ok":
        try:
            conf = float(out.get("confidence") or 0.0)
        except Exception:
            conf = 0.0
        if str(out.get("status") or "").strip() == "ok" and conf >= float(why_min_write_conf) and chosen2:
            vis_by_id: dict[str, str] = {}
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("claim_id") or "").strip()
                if cid:
                    vis_by_id[cid] = str(c.get("visibility") or "").strip()
            for cid in chosen2:
                try:
                    vis = "private" if vis_by_id.get(cid) == "private" else "project"
                    eid = tdb.append_edge(
                        edge_type="depends_on",
                        from_id=ev_id,
                        to_id=cid,
                        scope="project",
                        visibility=vis,
                        source_event_ids=[ev_id],
                        notes="why_trace materialized",
                    )
                except Exception:
                    continue
                written_edge_ids.append(eid)

    evw.append(
        {
            "kind": "why_trace",
            "batch_id": f"{last_batch_id}.why_trace",
            "ts": now_rfc3339(),
            "thread_id": thread_id or "",
            "target": target,
            "as_of_ts": as_of_ts,
            "query": query,
            "candidate_claim_ids": sorted(cand_ids),
            "state": why_state,
            "mind_transcript_ref": why_ref,
            "output": out,
            "written_edge_ids": written_edge_ids,
        }
    )
