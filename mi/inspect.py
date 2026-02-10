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
    if kind in ("codex_input", "hands_input"):
        detail = _truncate(str(obj.get("input") or "").strip().replace("\n", "\\n"), limit)
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
    elif kind == "loop_guard":
        detail = f"pattern={obj.get('pattern')}"
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
        "codex_input": None,
        "evidence_item": None,
        "check_plan": None,
        "auto_answer": None,
        "risk_event": None,
        "loop_guard": None,
        "decide_next": None,
        # Convenience: mind transcript pointers for this batch cycle.
        # Entries look like: {"kind": "...", "batch_id": "...", "mind_transcript_ref": "...", ...}.
        "mind_transcripts": [],
        "user_inputs": [],
    }
    last_bid = ""

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

                if kind in ("codex_input", "hands_input") and bid:
                    last_bid = bid
                    bundle = {
                        "batch_id": bid,
                        "thread_id": str(tid or ""),
                        "codex_input": obj,
                        "evidence_item": None,
                        "check_plan": None,
                        "auto_answer": None,
                        "risk_event": None,
                        "loop_guard": None,
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
                    add_mind_transcript_ref(obj=obj, kind="auto_answer_to_codex", bid=bid)
                elif kind == "risk_event":
                    if bid == last_bid:
                        bundle["risk_event"] = obj
                    add_mind_transcript_ref(obj=obj, kind="risk_judge", bid=bid)
                elif kind == "loop_guard":
                    if bid == last_bid:
                        bundle["loop_guard"] = obj
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
        return bundle

    return bundle
