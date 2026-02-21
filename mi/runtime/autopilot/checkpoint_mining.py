from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class WorkflowMiningDeps:
    build_decide_context: Callable[..., Any]
    suggest_workflow_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]]
    evidence_append: Callable[[dict[str, Any]], Any]
    load_workflow_candidates: Callable[[], dict[str, Any]]
    write_workflow_candidates: Callable[[dict[str, Any]], None]
    flush_state_warnings: Callable[[], None]
    write_workflow: Callable[[dict[str, Any]], None]
    new_workflow_id: Callable[[], str]
    enabled_effective_workflows: Callable[[], list[dict[str, Any]]]
    sync_hosts: Callable[[list[dict[str, Any]]], dict[str, Any]]
    now_ts: Callable[[], str]


@dataclass(frozen=True)
class PreferenceMiningDeps:
    build_decide_context: Callable[..., Any]
    mine_preferences_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]]
    evidence_append: Callable[[dict[str, Any]], Any]
    load_preference_candidates: Callable[[], dict[str, Any]]
    write_preference_candidates: Callable[[dict[str, Any]], None]
    flush_state_warnings: Callable[[], None]
    existing_signature_map: Callable[[str], dict[str, str]]
    claim_signature_fn: Callable[..., str]
    preference_signature_fn: Callable[..., str]
    handle_learn_suggested: Callable[..., list[str]]
    now_ts: Callable[[], str]


def mine_workflow_from_segment(
    *,
    enabled: bool,
    executed_batches: int,
    wf_cfg: dict[str, Any],
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
    wf_sigs_counted_in_run: set[str],
    deps: WorkflowMiningDeps,
) -> None:
    if not bool(enabled):
        return
    if int(executed_batches) <= 0:
        return

    auto_enable = bool(wf_cfg.get("auto_enable", True))
    auto_sync = bool(wf_cfg.get("auto_sync_on_change", True))
    allow_single_high = bool(wf_cfg.get("allow_single_if_high_benefit", True))
    try:
        min_occ = int(wf_cfg.get("min_occurrences", 2) or 2)
    except Exception:
        min_occ = 2
    if min_occ < 1:
        min_occ = 1

    mine_notes = f"source={source} status={status} batches={executed_batches} notes={notes}"
    tdb_ctx = deps.build_decide_context(hands_last_message="", recent_evidence=seg_evidence[-8:])
    tdb_ctx_obj = tdb_ctx.to_prompt_obj()
    prompt = deps.suggest_workflow_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        recent_evidence=seg_evidence,
        notes=mine_notes,
    )
    out, mind_ref, state = deps.mind_call(
        schema_filename="suggest_workflow.json",
        prompt=prompt,
        tag=f"suggest_workflow:{base_batch_id}",
        batch_id=f"{base_batch_id}.workflow_suggestion",
    )

    deps.evidence_append(
        {
            "kind": "workflow_suggestion",
            "batch_id": f"{base_batch_id}.workflow_suggestion",
            "ts": deps.now_ts(),
            "thread_id": thread_id or "",
            "state": state,
            "mind_transcript_ref": mind_ref,
            "notes": mine_notes,
            "output": out if isinstance(out, dict) else {},
        }
    )

    if not isinstance(out, dict) or not bool(out.get("should_suggest", False)):
        return
    sug = out.get("suggestion")
    if not isinstance(sug, dict):
        return

    signature = str(sug.get("signature") or "").strip()
    if not signature:
        return
    benefit = str(sug.get("benefit") or "").strip()
    reason_s = str(sug.get("reason") or "").strip()
    confidence = sug.get("confidence")
    try:
        conf_f = float(confidence) if confidence is not None else 0.0
    except Exception:
        conf_f = 0.0

    candidates = deps.load_workflow_candidates()
    by_sig = candidates.get("by_signature") if isinstance(candidates.get("by_signature"), dict) else {}
    entry = by_sig.get(signature)
    if not isinstance(entry, dict):
        entry = {}

    try:
        prev_n = int(entry.get("count") or 0)
    except Exception:
        prev_n = 0
    already_counted = signature in wf_sigs_counted_in_run
    if already_counted:
        new_n = prev_n
    else:
        new_n = prev_n + 1
        wf_sigs_counted_in_run.add(signature)
    entry["count"] = new_n
    entry["last_ts"] = deps.now_ts()
    entry["benefit"] = benefit
    entry["confidence"] = conf_f
    if reason_s:
        entry["reason"] = reason_s
    wf_obj = sug.get("workflow") if isinstance(sug.get("workflow"), dict) else {}
    name = str(wf_obj.get("name") or "").strip()
    if name:
        entry["workflow_name"] = name

    by_sig[signature] = entry
    candidates["by_signature"] = by_sig
    deps.write_workflow_candidates(candidates)
    deps.flush_state_warnings()

    existing_wid = str(entry.get("workflow_id") or "").strip()
    if existing_wid:
        return

    threshold = min_occ
    if allow_single_high and benefit == "high":
        threshold = 1
    if new_n < threshold:
        return
    if not isinstance(wf_obj, dict) or not wf_obj:
        return

    wid = deps.new_workflow_id()
    wf_final = dict(wf_obj)
    wf_final["id"] = wid
    wf_final["enabled"] = bool(auto_enable)
    src = wf_final.get("source") if isinstance(wf_final.get("source"), dict) else {}
    ev_refs = src.get("evidence_refs") if isinstance(src.get("evidence_refs"), list) else []
    wf_final["source"] = {
        "kind": "suggested",
        "reason": (reason_s or "suggest_workflow") + f" (signature={signature} benefit={benefit} confidence={conf_f:.2f})",
        "evidence_refs": [str(x) for x in ev_refs if str(x).strip()],
    }
    wf_final["created_ts"] = deps.now_ts()
    wf_final["updated_ts"] = deps.now_ts()
    deps.write_workflow(wf_final)

    entry["workflow_id"] = wid
    entry["solidified_ts"] = deps.now_ts()
    by_sig[signature] = entry
    candidates["by_signature"] = by_sig
    deps.write_workflow_candidates(candidates)

    deps.evidence_append(
        {
            "kind": "workflow_solidified",
            "batch_id": f"{base_batch_id}.workflow_solidified",
            "ts": deps.now_ts(),
            "thread_id": thread_id or "",
            "signature": signature,
            "count": new_n,
            "threshold": threshold,
            "benefit": benefit,
            "confidence": conf_f,
            "workflow_id": wid,
            "workflow_name": str(wf_final.get("name") or ""),
            "enabled": bool(wf_final.get("enabled", False)),
        }
    )

    if auto_sync:
        effective = deps.enabled_effective_workflows()
        sync_obj = deps.sync_hosts(effective)
        deps.evidence_append(
            {
                "kind": "host_sync",
                "batch_id": f"{base_batch_id}.host_sync",
                "ts": deps.now_ts(),
                "thread_id": thread_id or "",
                "source": "workflow_solidified",
                "sync": sync_obj,
            }
        )
        deps.flush_state_warnings()


def mine_preferences_from_segment(
    *,
    enabled: bool,
    executed_batches: int,
    pref_cfg: dict[str, Any],
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
    project_id: str,
    pref_sigs_counted_in_run: set[str],
    deps: PreferenceMiningDeps,
) -> None:
    if not bool(enabled):
        return
    if int(executed_batches) <= 0:
        return

    pref_allow_single_high = bool(pref_cfg.get("allow_single_if_high_benefit", True))
    try:
        pref_min_occ = int(pref_cfg.get("min_occurrences", 2) or 2)
    except Exception:
        pref_min_occ = 2
    if pref_min_occ < 1:
        pref_min_occ = 1
    try:
        pref_min_conf = float(pref_cfg.get("min_confidence", 0.75) or 0.75)
    except Exception:
        pref_min_conf = 0.75
    try:
        pref_max = int(pref_cfg.get("max_suggestions", 3) or 3)
    except Exception:
        pref_max = 3
    if pref_max < 0:
        pref_max = 0
    if pref_max > 10:
        pref_max = 10
    if pref_max == 0:
        return

    mine_notes = f"source={source} status={status} batches={executed_batches} notes={notes}"
    tdb_ctx = deps.build_decide_context(hands_last_message="", recent_evidence=seg_evidence[-8:])
    tdb_ctx_obj = tdb_ctx.to_prompt_obj()
    prompt = deps.mine_preferences_prompt_builder(
        task=task,
        hands_provider=hands_provider,
        mindspec_base=mindspec_base if isinstance(mindspec_base, dict) else {},
        project_overlay=project_overlay if isinstance(project_overlay, dict) else {},
        thought_db_context=tdb_ctx_obj if isinstance(tdb_ctx_obj, dict) else {},
        recent_evidence=seg_evidence,
        notes=mine_notes,
    )
    out, mind_ref, state = deps.mind_call(
        schema_filename="mine_preferences.json",
        prompt=prompt,
        tag=f"mine_preferences:{base_batch_id}",
        batch_id=f"{base_batch_id}.preference_mining",
    )

    deps.evidence_append(
        {
            "kind": "preference_mining",
            "batch_id": f"{base_batch_id}.preference_mining",
            "ts": deps.now_ts(),
            "thread_id": thread_id or "",
            "state": state,
            "mind_transcript_ref": mind_ref,
            "notes": mine_notes,
            "output": out if isinstance(out, dict) else {},
        }
    )

    if not isinstance(out, dict):
        return
    sugs = out.get("suggestions")
    if not isinstance(sugs, list) or not sugs:
        return

    candidates = deps.load_preference_candidates()
    by_sig = candidates.get("by_signature") if isinstance(candidates.get("by_signature"), dict) else {}

    src_eids_pref: list[str] = []
    seen_eids: set[str] = set()
    for r in (seg_evidence or [])[-16:]:
        if not isinstance(r, dict):
            continue
        eid = r.get("event_id")
        if not isinstance(eid, str):
            continue
        e = eid.strip()
        if not e or e in seen_eids:
            continue
        seen_eids.add(e)
        src_eids_pref.append(e)

    existing_sig_to_id = {
        "project": deps.existing_signature_map("project"),
        "global": deps.existing_signature_map("global"),
    }

    for raw in sugs[:pref_max]:
        if not isinstance(raw, dict):
            continue
        scope = str(raw.get("scope") or "project").strip()
        if scope not in ("global", "project"):
            scope = "project"
        text = str(raw.get("text") or "").strip()
        if not text:
            continue

        pid = project_id if scope == "project" else ""
        sig2 = deps.claim_signature_fn(claim_type="preference", scope=scope, project_id=pid, text=text)
        if sig2 in existing_sig_to_id.get(scope, {}):
            continue

        benefit = str(raw.get("benefit") or "medium").strip()
        if benefit not in ("low", "medium", "high"):
            benefit = "medium"
        rationale = str(raw.get("rationale") or "").strip()
        conf = raw.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else 0.0
        except Exception:
            conf_f = 0.0
        if conf_f < pref_min_conf:
            continue

        sig = deps.preference_signature_fn(scope=scope, text=text)
        entry = by_sig.get(sig)
        if not isinstance(entry, dict):
            entry = {}

        try:
            prev_n = int(entry.get("count") or 0)
        except Exception:
            prev_n = 0
        already_counted = sig in pref_sigs_counted_in_run
        if already_counted:
            new_n = prev_n
        else:
            new_n = prev_n + 1
            pref_sigs_counted_in_run.add(sig)
        entry["count"] = new_n
        entry["last_ts"] = deps.now_ts()
        entry["scope"] = scope
        entry["text"] = text
        entry["benefit"] = benefit
        entry["confidence"] = conf_f
        if rationale:
            entry["rationale"] = rationale

        if bool(entry.get("suggestion_emitted", False)) or bool(entry.get("applied_claim_ids")):
            by_sig[sig] = entry
            continue

        threshold = pref_min_occ
        if pref_allow_single_high and benefit == "high":
            threshold = 1
        if new_n < threshold:
            by_sig[sig] = entry
            continue

        applied_ids = deps.handle_learn_suggested(
            learn_suggested=[{"scope": scope, "text": text, "rationale": rationale or "preference_mining", "severity": "medium"}],
            batch_id=f"{base_batch_id}.preference_solidified",
            source="mine_preferences",
            mind_transcript_ref=mind_ref,
            source_event_ids=src_eids_pref,
        )
        entry["suggestion_emitted"] = True
        entry["suggestion_ts"] = deps.now_ts()
        if applied_ids:
            entry["applied_claim_ids"] = list(applied_ids)
            entry["solidified_ts"] = deps.now_ts()

        deps.evidence_append(
            {
                "kind": "preference_solidified",
                "batch_id": f"{base_batch_id}.preference_solidified",
                "ts": deps.now_ts(),
                "thread_id": thread_id or "",
                "signature": sig,
                "count": new_n,
                "threshold": threshold,
                "benefit": benefit,
                "confidence": conf_f,
                "scope": scope,
                "text": text,
                "applied_claim_ids": list(applied_ids),
            }
        )
        by_sig[sig] = entry

    candidates["by_signature"] = by_sig
    deps.write_preference_candidates(candidates)
    deps.flush_state_warnings()
