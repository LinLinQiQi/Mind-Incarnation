from __future__ import annotations

from typing import Any, Callable

from ...core.storage import now_rfc3339
from ...runtime.prompts import learn_update_prompt


def maybe_run_learn_update_on_run_end(
    *,
    executed_batches: int,
    last_batch_id: str,
    learn_suggested_records_this_run: list[dict[str, Any]],
    tdb: Any,
    evw: Any,
    mind_call: Callable[..., tuple[Any, str, str]],
    emit_prefixed: Callable[[str, str], None],
    truncate: Callable[[str, int], str],
    task: str,
    hands_provider: str,
    runtime_cfg: dict[str, Any],
    project_overlay: dict[str, Any],
    status: str,
    notes: str,
    thread_id: str | None,
) -> None:
    """Optional run-end consolidation for learned preferences/goals (best-effort)."""

    vr = runtime_cfg.get("violation_response") if isinstance(runtime_cfg.get("violation_response"), dict) else {}
    lu_cfg = vr.get("learn_update") if isinstance(vr.get("learn_update"), dict) else {}
    lu_enabled = bool(lu_cfg.get("enabled", True))
    auto_learn_enabled = bool(vr.get("auto_learn", True))
    if not (lu_enabled and auto_learn_enabled and executed_batches > 0 and last_batch_id):
        return

    try:
        min_sugs = int(lu_cfg.get("min_new_suggestions_per_run", 2) or 2)
    except Exception:
        min_sugs = 2
    min_sugs = max(1, min(10, min_sugs))

    try:
        min_active = int(lu_cfg.get("min_active_learned_claims", 3) or 3)
    except Exception:
        min_active = 3
    min_active = max(0, min(50, min_active))

    try:
        cfg_min_conf = float(lu_cfg.get("min_confidence", 0.9) or 0.9)
    except Exception:
        cfg_min_conf = 0.9
    cfg_min_conf = max(0.0, min(1.0, cfg_min_conf))

    try:
        max_claims = int(lu_cfg.get("max_claims", 6) or 6)
    except Exception:
        max_claims = 6
    max_claims = max(0, min(20, max_claims))

    try:
        max_retracts = int(lu_cfg.get("max_retracts", 6) or 6)
    except Exception:
        max_retracts = 6
    max_retracts = max(0, min(40, max_retracts))

    recent_ls: list[dict[str, Any]] = []
    allowed_event_ids: list[str] = []
    seen_eid: set[str] = set()
    for rec in learn_suggested_records_this_run[-24:]:
        if not isinstance(rec, dict):
            continue
        eid = str(rec.get("event_id") or "").strip()
        if not eid or eid in seen_eid:
            continue
        seen_eid.add(eid)
        allowed_event_ids.append(eid)
        recent_ls.append(
            {
                "event_id": eid,
                "batch_id": str(rec.get("batch_id") or "").strip(),
                "source": str(rec.get("source") or "").strip(),
                "learn_suggested": rec.get("learn_suggested") if isinstance(rec.get("learn_suggested"), list) else [],
                "applied_claim_ids": rec.get("applied_claim_ids") if isinstance(rec.get("applied_claim_ids"), list) else [],
            }
        )

    if len(recent_ls) < min_sugs or not allowed_event_ids:
        return
    allowed_set = set(allowed_event_ids)

    def _compact_learned_claims(view: Any, *, scope: str, limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        ids = getattr(view, "claim_ids_by_asserted_ts_desc", []) or []
        for cid in ids:
            if len(out) >= limit:
                break
            if not isinstance(cid, str):
                continue
            ccid = cid.strip()
            if not ccid:
                continue
            try:
                if view.claim_status(ccid) != "active":
                    continue
            except Exception:
                continue
            c = view.claims_by_id.get(ccid)
            if not isinstance(c, dict):
                continue
            tags = c.get("tags") if isinstance(c.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            if "mi:learned" not in tagset:
                continue
            out.append(
                {
                    "claim_id": ccid,
                    "claim_type": str(c.get("claim_type") or "").strip(),
                    "text": truncate(str(c.get("text") or "").strip(), 400),
                    "scope": scope,
                    "visibility": str(c.get("visibility") or "").strip(),
                    "asserted_ts": str(c.get("asserted_ts") or "").strip(),
                    "tags": sorted(tagset)[:12],
                }
            )
        return out

    learned_proj = _compact_learned_claims(tdb.load_view(scope="project"), scope="project", limit=240)
    learned_glob = _compact_learned_claims(tdb.load_view(scope="global"), scope="global", limit=240)
    if len(learned_proj) < min_active:
        return

    existing_learned = learned_proj + learned_glob
    allowed_retract_ids = [str(x.get("claim_id") or "").strip() for x in existing_learned if isinstance(x, dict) and str(x.get("claim_id") or "").strip()]
    allowed_retract_ids = allowed_retract_ids[:400]
    retract_set = set(allowed_retract_ids)

    prompt = learn_update_prompt(
        task=task,
        hands_provider=hands_provider,
        runtime_cfg=runtime_cfg,
        project_overlay=project_overlay,
        recent_learn_suggested=recent_ls,
        existing_learned_claims=existing_learned,
        allowed_event_ids=allowed_event_ids,
        allowed_retract_claim_ids=allowed_retract_ids,
        min_confidence=cfg_min_conf,
        max_claims=max_claims,
        max_retracts=max_retracts,
        notes=f"source=run_end status={status} batches={executed_batches} notes={truncate(notes, 300)}",
    )
    out_obj, mind_ref, lu_state = mind_call(
        schema_filename="learn_update.json",
        prompt=prompt,
        tag=f"learn_update:{last_batch_id}",
        batch_id=f"{last_batch_id}.learn_update",
    )

    lu_out = out_obj if isinstance(out_obj, dict) else {}
    should_apply = bool(lu_out.get("should_apply", False)) if isinstance(lu_out, dict) else False
    try:
        out_min_conf = float(lu_out.get("min_confidence", 0.0) or 0.0) if isinstance(lu_out, dict) else 0.0
    except Exception:
        out_min_conf = 0.0
    min_conf = max(cfg_min_conf, max(0.0, min(1.0, out_min_conf)))

    patch0 = lu_out.get("patch") if isinstance(lu_out, dict) else None
    patch = patch0 if isinstance(patch0, dict) else {}
    claims_in = patch.get("claims") if isinstance(patch.get("claims"), list) else []
    edges_in = patch.get("edges") if isinstance(patch.get("edges"), list) else []
    patch_norm = {
        "claims": [x for x in claims_in if isinstance(x, dict)],
        "edges": [x for x in edges_in if isinstance(x, dict)],
        "notes": str(patch.get("notes") or "").strip(),
    }

    applied_patch: dict[str, Any] = {"written": [], "linked_existing": [], "written_edges": [], "skipped": []}
    retracted: list[dict[str, str]] = []
    retract_skipped: list[dict[str, str]] = []

    if should_apply:
        try:
            applied_patch = tdb.apply_mined_output(
                output=patch_norm,
                allowed_event_ids=allowed_set,
                min_confidence=min_conf,
                max_claims=max_claims,
            )
        except Exception as e:
            applied_patch = {
                "written": [],
                "linked_existing": [],
                "written_edges": [],
                "skipped": [{"kind": "claim", "reason": f"apply_error:{type(e).__name__}", "detail": truncate(str(e), 200)}],
            }

        retract_in = lu_out.get("retract") if isinstance(lu_out.get("retract"), list) else []
        for raw in [x for x in retract_in if isinstance(x, dict)][:max_retracts]:
            scope = str(raw.get("scope") or "").strip()
            if scope not in ("project", "global"):
                retract_skipped.append({"reason": "invalid_scope", "detail": scope})
                continue
            cid = str(raw.get("claim_id") or "").strip()
            if not cid or cid not in retract_set:
                retract_skipped.append({"reason": "not_retractable", "detail": cid or "(empty)"})
                continue
            rationale = str(raw.get("rationale") or "").strip()
            try:
                cf = float(raw.get("confidence") or 0.0)
            except Exception:
                cf = 0.0
            if cf < min_conf:
                retract_skipped.append({"reason": "below_confidence", "detail": cid})
                continue
            src_raw = raw.get("source_event_ids") if isinstance(raw.get("source_event_ids"), list) else []
            src = [str(x).strip() for x in src_raw if str(x).strip()]
            src2 = [x for x in src if x in allowed_set][:8]
            if not src2:
                retract_skipped.append({"reason": "no_valid_source_event_ids", "detail": cid})
                continue
            try:
                tdb.append_claim_retract(
                    claim_id=cid,
                    scope=scope,
                    rationale=rationale or "learn_update retract",
                    source_event_ids=src2,
                )
            except Exception:
                retract_skipped.append({"reason": "write_error", "detail": cid})
                continue
            retracted.append({"scope": scope, "claim_id": cid})

    applied = dict(applied_patch) if isinstance(applied_patch, dict) else {}
    applied["retracted"] = retracted
    applied["retract_skipped"] = retract_skipped

    evw.append(
        {
            "kind": "learn_update",
            "batch_id": f"{last_batch_id}.learn_update",
            "ts": now_rfc3339(),
            "thread_id": thread_id or "",
            "state": str(lu_state or ""),
            "mind_transcript_ref": str(mind_ref or ""),
            "allowed_event_ids": allowed_event_ids,
            "allowed_retract_claim_ids_count": len(retract_set),
            "allowed_retract_claim_ids_sample": allowed_retract_ids[:12],
            "input_summary": {
                "learn_suggested_events": len(recent_ls),
                "active_learned_claims_project": len(learned_proj),
                "active_learned_claims_global": len(learned_glob),
            },
            "output": lu_out,
            "applied": applied,
        }
    )

    try:
        w = applied.get("written") if isinstance(applied.get("written"), list) else []
        we = applied.get("written_edges") if isinstance(applied.get("written_edges"), list) else []
        rr = applied.get("retracted") if isinstance(applied.get("retracted"), list) else []
        emit_prefixed(
            "[mi]",
            f"learn_update state={str(lu_state or '')} should_apply={str(should_apply).lower()} written={len(w)} edges={len(we)} retracted={len(rr)}",
        )
    except Exception:
        pass
