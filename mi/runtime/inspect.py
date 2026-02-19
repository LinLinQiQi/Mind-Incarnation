from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def tail_raw_lines(path: Path, n: int) -> list[str]:
    if n <= 0:
        return []
    dq: deque[str] = deque(maxlen=n)
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                dq.append(line.rstrip("\n"))
    except FileNotFoundError:
        return []
    return list(dq)


def tail_json_objects(path: Path, n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in tail_raw_lines(path, n):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def classify_evidence_record(obj: dict[str, Any]) -> str:
    kind = obj.get("kind")
    if isinstance(kind, str) and kind.strip():
        return kind.strip()
    # EvidenceItem has no "kind" field in V1.
    if "facts" in obj and "results" in obj and "unknowns" in obj:
        return "evidence"
    return "unknown"


def summarize_evidence_record(obj: dict[str, Any], *, limit: int = 160) -> str:
    kind = classify_evidence_record(obj)
    ts = str(obj.get("ts") or "")
    bid = str(obj.get("batch_id") or "")

    detail = ""
    if kind == "hands_input":
        detail = _truncate(str(obj.get("input") or "").strip().replace("\n", "\\n"), limit)
    elif kind == "state_corrupt":
        items = obj.get("items") if isinstance(obj.get("items"), list) else []
        labels: list[str] = []
        for it in items:
            if isinstance(it, dict):
                lab = str(it.get("label") or "").strip()
                if lab:
                    labels.append(lab)
        label_s = ",".join(sorted(set(labels))[:6])
        detail = f"n={len(items)}" + (f" labels={label_s}" if label_s else "")
    elif kind == "decide_next":
        st = str(obj.get("status") or "")
        na = str(obj.get("next_action") or "")
        cf = obj.get("confidence")
        phase = str(obj.get("phase") or "")
        cf_s = ""
        try:
            if cf is not None:
                cf_s = f"{float(cf):.2f}"
        except Exception:
            cf_s = str(cf)
        parts = [x for x in [phase, f"{st}:{na}".strip(":"), (f"conf={cf_s}" if cf_s else "")] if x]
        detail = " ".join(parts).strip()
    elif kind == "check_plan":
        checks = obj.get("checks") if isinstance(obj.get("checks"), dict) else {}
        sr = bool(checks.get("should_run_checks", False))
        detail = f"should_run_checks={sr}"
    elif kind == "auto_answer":
        aa = obj.get("auto_answer") if isinstance(obj.get("auto_answer"), dict) else {}
        sa = bool(aa.get("should_answer", False))
        nui = bool(aa.get("needs_user_input", False))
        detail = f"should_answer={sa} needs_user_input={nui}"
    elif kind == "risk_event":
        risk = obj.get("risk") if isinstance(obj.get("risk"), dict) else {}
        cat = str(risk.get("category") or "")
        sev = str(risk.get("severity") or "")
        detail = f"{cat}:{sev}".strip(":")
    elif kind == "mind_error":
        schema = str(obj.get("schema_filename") or "")
        tag = str(obj.get("tag") or "")
        err = _truncate(str(obj.get("error") or "").strip().replace("\n", " "), 90)
        detail = " ".join([x for x in [schema, tag, err] if x]).strip()
    elif kind == "mind_circuit":
        state = str(obj.get("state") or "")
        fc = obj.get("failures_consecutive")
        ft = obj.get("failures_total")
        try:
            fc_s = str(int(fc)) if fc is not None else ""
        except Exception:
            fc_s = str(fc or "")
        try:
            ft_s = str(int(ft)) if ft is not None else ""
        except Exception:
            ft_s = str(ft or "")
        detail = " ".join([x for x in [state, (f"consec={fc_s}" if fc_s else ""), (f"total={ft_s}" if ft_s else "")] if x]).strip()
    elif kind == "learn_suggested":
        sid = str(obj.get("id") or "")
        auto = obj.get("auto_learn")
        ch = obj.get("learned_changes") if isinstance(obj.get("learned_changes"), list) else []
        applied = obj.get("applied_entry_ids") if isinstance(obj.get("applied_entry_ids"), list) else []
        detail = f"id={sid} n={len(ch)} auto_learn={bool(auto)} applied={len(applied)}"
    elif kind == "learn_applied":
        sid = str(obj.get("suggestion_id") or "")
        applied = obj.get("applied_entry_ids") if isinstance(obj.get("applied_entry_ids"), list) else []
        detail = f"suggestion_id={sid} applied={len(applied)}"
    elif kind == "claim_retract":
        cid = str(obj.get("claim_id") or "").strip()
        scope = str(obj.get("scope") or "").strip()
        detail = f"claim_id={cid} scope={scope}".strip()
    elif kind == "claim_supersede":
        old_id = str(obj.get("old_claim_id") or "").strip()
        scope = str(obj.get("scope") or "").strip()
        detail = f"old_claim_id={old_id} scope={scope}".strip()
    elif kind == "claim_same_as":
        dup_id = str(obj.get("dup_id") or "").strip()
        canon_id = str(obj.get("canonical_id") or "").strip()
        scope = str(obj.get("scope") or "").strip()
        detail = f"{dup_id}->{canon_id} scope={scope}".strip()
    elif kind == "node_create":
        nt = str(obj.get("node_type") or "").strip()
        title = _truncate(str(obj.get("title") or "").strip().replace("\n", " "), 90)
        scope = str(obj.get("scope") or "").strip()
        detail = " ".join([x for x in [f"type={nt}" if nt else "", f"scope={scope}" if scope else "", title] if x]).strip()
    elif kind == "node_retract":
        nid = str(obj.get("node_id") or "").strip()
        scope = str(obj.get("scope") or "").strip()
        detail = f"node_id={nid} scope={scope}".strip()
    elif kind == "edge_create":
        et = str(obj.get("edge_type") or "").strip()
        frm = str(obj.get("from_id") or "").strip()
        to = str(obj.get("to_id") or "").strip()
        scope = str(obj.get("scope") or "").strip()
        detail = f"type={et} {frm}->{to} scope={scope}".strip()
    elif kind == "node_materialized":
        ok = bool(obj.get("ok", True))
        ck = str(obj.get("checkpoint_kind") or "").strip()
        wn = obj.get("written_nodes") if isinstance(obj.get("written_nodes"), list) else []
        we = obj.get("written_edges") if isinstance(obj.get("written_edges"), list) else []
        parts = [f"ok={str(ok).lower()}", (f"checkpoint_kind={ck}" if ck else ""), f"nodes={len(wn)}", f"edges={len(we)}"]
        detail = " ".join([p for p in parts if p]).strip()
    elif kind == "claim_mining":
        applied = obj.get("applied") if isinstance(obj.get("applied"), dict) else {}
        w = applied.get("written") if isinstance(applied.get("written"), list) else []
        le = applied.get("linked_existing") if isinstance(applied.get("linked_existing"), list) else []
        we = applied.get("written_edges") if isinstance(applied.get("written_edges"), list) else []
        sk = applied.get("skipped") if isinstance(applied.get("skipped"), list) else []
        detail = f"written={len(w)} linked_existing={len(le)} edges={len(we)} skipped={len(sk)}"
    elif kind == "why_trace":
        out = obj.get("output") if isinstance(obj.get("output"), dict) else {}
        st = str(out.get("status") or "").strip()
        cf = out.get("confidence")
        chosen = out.get("chosen_claim_ids") if isinstance(out.get("chosen_claim_ids"), list) else []
        try:
            cf_s = f"{float(cf):.2f}" if cf is not None else ""
        except Exception:
            cf_s = str(cf or "")
        detail = " ".join([x for x in [f"status={st}" if st else "", (f"conf={cf_s}" if cf_s else ""), f"chosen={len(chosen)}"] if x]).strip()
    elif kind == "loop_guard":
        detail = f"pattern={obj.get('pattern')}"
    elif kind == "loop_break":
        pat = str(obj.get("pattern") or "").strip()
        st = str(obj.get("state") or "").strip()
        out = obj.get("output") if isinstance(obj.get("output"), dict) else {}
        act = ""
        if isinstance(out, dict):
            act = str(out.get("action") or "").strip()
        if not act:
            act = str(obj.get("action") or "").strip()
        parts = [f"pattern={pat}" if pat else "", f"action={act}" if act else "", f"state={st}" if st else ""]
        detail = " ".join([p for p in parts if p]).strip()
    elif kind == "user_input":
        q = _truncate(str(obj.get("question") or "").strip().replace("\n", " "), 80)
        a = _truncate(str(obj.get("answer") or "").strip().replace("\n", " "), 60)
        detail = f"q={q} a={a}"
    elif kind == "evidence":
        facts = obj.get("facts") if isinstance(obj.get("facts"), list) else []
        unknowns = obj.get("unknowns") if isinstance(obj.get("unknowns"), list) else []
        f0 = _truncate(str(facts[0]) if facts else "", 80)
        detail = f"facts={len(facts)} unknowns={len(unknowns)} {f0}".strip()

    base = " ".join([x for x in [ts, bid, kind] if x])
    if detail:
        return _truncate(base + " " + detail, limit)
    return _truncate(base, limit)


def load_last_batch_bundle(evidence_log_path: Path) -> dict[str, Any]:
    """Load a compact view of the most recent batch from EvidenceLog."""

    bundle: dict[str, Any] = {
        "batch_id": "",
        "thread_id": "",
        "hands_input": None,
        "evidence_item": None,
        "check_plan": None,
        "auto_answer": None,
        "risk_event": None,
        # Convenience: most recent state recovery record (may not match the latest batch_id prefix).
        "state_corrupt_recent": None,
        # Optional: WhyTrace record(s) related to the last batch cycle.
        "why_trace": None,
        "why_traces": [],
        "learn_suggested": [],
        "learn_applied": [],
        "loop_guard": None,
        "loop_break": None,
        "decide_next": None,
        # Convenience: mind transcript pointers for this batch cycle.
        # Entries look like: {"kind": "...", "batch_id": "...", "mind_transcript_ref": "...", ...}.
        "mind_transcripts": [],
        "user_inputs": [],
    }
    last_bid = ""
    last_state_corrupt: dict[str, Any] | None = None

    def is_related_batch_id(bid: str) -> bool:
        if not last_bid or not bid:
            return False
        return bid == last_bid or bid.startswith(last_bid + ".")

    def add_mind_transcript_ref(*, obj: dict[str, Any], kind: str, bid: str) -> None:
        ref = obj.get("mind_transcript_ref")
        if not isinstance(ref, str) or not ref.strip():
            return
        mts = bundle.get("mind_transcripts")
        if not isinstance(mts, list):
            bundle["mind_transcripts"] = []
            mts = bundle["mind_transcripts"]
        item: dict[str, Any] = {"kind": kind, "batch_id": bid, "mind_transcript_ref": ref.strip()}
        if kind == "decide_next":
            phase = obj.get("phase")
            if isinstance(phase, str) and phase.strip():
                item["phase"] = phase.strip()
        if item not in mts:
            mts.append(item)

    try:
        with evidence_log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue

                kind = classify_evidence_record(obj)
                bid = str(obj.get("batch_id") or "")
                tid = obj.get("thread_id")

                # This record may use a fixed batch id like "b0.state_recovery", so it won't
                # reliably match the latest batch prefix. Keep the most recent one as a pointer.
                if kind == "state_corrupt":
                    last_state_corrupt = obj
                    continue

                if kind == "hands_input" and bid:
                    last_bid = bid
                    bundle = {
                        "batch_id": bid,
                        "thread_id": str(tid or ""),
                        "hands_input": obj,
                        "evidence_item": None,
                        "check_plan": None,
                        "auto_answer": None,
                        "risk_event": None,
                        "state_corrupt_recent": None,
                        "why_trace": None,
                        "why_traces": [],
                        "learn_suggested": [],
                        "learn_applied": [],
                        "loop_guard": None,
                        "loop_break": None,
                        "decide_next": None,
                        "mind_transcripts": [],
                        "user_inputs": [],
                    }
                    continue

                if not is_related_batch_id(bid):
                    continue

                # Records for the current last batch.
                if kind == "evidence":
                    if bid == last_bid:
                        bundle["evidence_item"] = obj
                    add_mind_transcript_ref(obj=obj, kind="extract_evidence", bid=bid)
                elif kind == "check_plan":
                    if bid == last_bid:
                        bundle["check_plan"] = obj
                    add_mind_transcript_ref(obj=obj, kind="plan_min_checks", bid=bid)
                elif kind == "auto_answer":
                    if bid == last_bid:
                        bundle["auto_answer"] = obj
                    add_mind_transcript_ref(obj=obj, kind="auto_answer_to_hands", bid=bid)
                elif kind == "risk_event":
                    if bid == last_bid:
                        bundle["risk_event"] = obj
                    add_mind_transcript_ref(obj=obj, kind="risk_judge", bid=bid)
                elif kind == "why_trace":
                    items = bundle.get("why_traces")
                    if isinstance(items, list):
                        items.append(obj)
                    else:
                        bundle["why_traces"] = [obj]
                    bundle["why_trace"] = obj
                    add_mind_transcript_ref(obj=obj, kind="why_trace", bid=bid)
                elif kind == "learn_suggested":
                    items = bundle.get("learn_suggested")
                    if isinstance(items, list):
                        items.append(obj)
                    else:
                        bundle["learn_suggested"] = [obj]
                elif kind == "learn_applied":
                    items = bundle.get("learn_applied")
                    if isinstance(items, list):
                        items.append(obj)
                    else:
                        bundle["learn_applied"] = [obj]
                elif kind == "loop_guard":
                    if bid == last_bid:
                        bundle["loop_guard"] = obj
                elif kind == "loop_break":
                    if bid == last_bid:
                        bundle["loop_break"] = obj
                    add_mind_transcript_ref(obj=obj, kind="loop_break", bid=bid)
                elif kind == "decide_next":
                    if bid == last_bid:
                        bundle["decide_next"] = obj
                    add_mind_transcript_ref(obj=obj, kind="decide_next", bid=bid)
                elif kind == "user_input":
                    uis = bundle.get("user_inputs")
                    if isinstance(uis, list):
                        uis.append(obj)
                    else:
                        bundle["user_inputs"] = [obj]

    except FileNotFoundError:
        bundle["state_corrupt_recent"] = last_state_corrupt
        return bundle

    bundle["state_corrupt_recent"] = last_state_corrupt
    return bundle
