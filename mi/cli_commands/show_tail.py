from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from ..core.paths import GlobalPaths, ProjectPaths
from ..core.redact import redact_text
from ..runtime.inspect import load_last_batch_bundle, summarize_evidence_record, tail_json_objects, tail_raw_lines
from ..runtime.transcript import last_agent_message_from_transcript, resolve_transcript_path, tail_transcript_lines
from ..thoughtdb import ThoughtDbStore
from ..thoughtdb.app_service import ThoughtDbApplicationService


def _latest_transcript_path(pp: ProjectPaths, *, mind: bool) -> Path:
    subdir = "mind" if mind else "hands"
    tdir = pp.transcripts_dir / subdir
    files = sorted([p for p in tdir.glob("*.jsonl") if p.is_file()])
    return files[-1] if files else Path("")


def _render_transcript(tp: Path, *, lines: int, jsonl: bool, redact: bool) -> int:
    if not tp or not str(tp):
        print("No transcript found.", file=sys.stderr)
        return 2
    if not tp.exists():
        print(f"Transcript not found: {tp}", file=sys.stderr)
        return 2

    real_tp = resolve_transcript_path(tp)
    rows = tail_transcript_lines(tp, lines)
    print(str(tp))
    if real_tp != tp:
        print(f"(archived -> {real_tp})")
    if jsonl:
        for line in rows:
            print(redact_text(line) if redact else line)
        return 0

    for line in rows:
        try:
            rec = json.loads(line)
        except Exception:
            print(redact_text(line) if redact else line)
            continue
        if not isinstance(rec, dict):
            print(redact_text(line) if redact else line)
            continue
        ts = str(rec.get("ts") or "")
        stream = str(rec.get("stream") or "")
        payload = rec.get("line")
        payload_s = str(payload) if payload is not None else ""
        if redact:
            payload_s = redact_text(payload_s)
        print(f"{ts} {stream} {payload_s}".strip())
    return 0


def _render_last_bundle(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int:
    project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
    pp = ProjectPaths(home_dir=home_dir, project_root=project_root)

    bundle = load_last_batch_bundle(pp.evidence_log_path)
    hands_input = bundle.get("hands_input") if isinstance(bundle.get("hands_input"), dict) else None
    evidence_item = bundle.get("evidence_item") if isinstance(bundle.get("evidence_item"), dict) else None
    decide_next = bundle.get("decide_next") if isinstance(bundle.get("decide_next"), dict) else None

    transcript_path = ""
    if hands_input and isinstance(hands_input.get("transcript_path"), str):
        transcript_path = hands_input["transcript_path"]
    elif evidence_item and isinstance(evidence_item.get("hands_transcript_ref"), str):
        transcript_path = evidence_item["hands_transcript_ref"]

    last_msg = ""
    if transcript_path:
        last_msg = last_agent_message_from_transcript(Path(transcript_path))

    mi_input_text = (hands_input.get("input") if hands_input else "") or ""
    hands_last_text = last_msg or ""

    evidence_item_out = evidence_item or {}
    decide_next_out = decide_next or {}
    state_corrupt_recent_raw = bundle.get("state_corrupt_recent") if isinstance(bundle.get("state_corrupt_recent"), dict) else None
    state_corrupt_recent_out = dict(state_corrupt_recent_raw) if isinstance(state_corrupt_recent_raw, dict) else {}
    why_trace_raw = bundle.get("why_trace") if isinstance(bundle.get("why_trace"), dict) else None
    why_traces_raw = bundle.get("why_traces") if isinstance(bundle.get("why_traces"), list) else []
    why_trace_out = dict(why_trace_raw) if isinstance(why_trace_raw, dict) else {}
    why_traces_out = [dict(x) for x in why_traces_raw if isinstance(x, dict)]
    learn_update_raw = bundle.get("learn_update") if isinstance(bundle.get("learn_update"), dict) else None
    learn_update_out = dict(learn_update_raw) if isinstance(learn_update_raw, dict) else {}
    learn_suggested_out = (bundle.get("learn_suggested") or []) if isinstance(bundle.get("learn_suggested"), list) else []
    learn_applied_out = (bundle.get("learn_applied") or []) if isinstance(bundle.get("learn_applied"), list) else []
    if bool(getattr(args, "redact", False)):
        mi_input_text = redact_text(mi_input_text)
        hands_last_text = redact_text(hands_last_text)
        if isinstance(evidence_item_out, dict):
            for k in ("facts", "results", "unknowns", "risk_signals"):
                v = evidence_item_out.get(k)
                if isinstance(v, list):
                    evidence_item_out[k] = [redact_text(str(x)) for x in v]
        if isinstance(decide_next_out, dict):
            for k in ("notes", "ask_user_question", "next_hands_input"):
                v = decide_next_out.get(k)
                if isinstance(v, str) and v:
                    decide_next_out[k] = redact_text(v)
            inner = decide_next_out.get("decision")
            if isinstance(inner, dict):
                for k in ("notes", "ask_user_question", "next_hands_input"):
                    v = inner.get(k)
                    if isinstance(v, str) and v:
                        inner[k] = redact_text(v)
        if isinstance(learn_update_out, dict) and learn_update_out:
            out0 = learn_update_out.get("output")
            if isinstance(out0, dict):
                patch0 = out0.get("patch")
                if isinstance(patch0, dict):
                    claims = patch0.get("claims") if isinstance(patch0.get("claims"), list) else []
                    for c in claims:
                        if isinstance(c, dict) and isinstance(c.get("text"), str) and c.get("text"):
                            c["text"] = redact_text(str(c.get("text") or ""))
                        if isinstance(c, dict) and isinstance(c.get("notes"), str) and c.get("notes"):
                            c["notes"] = redact_text(str(c.get("notes") or ""))
                    edges = patch0.get("edges") if isinstance(patch0.get("edges"), list) else []
                    for e in edges:
                        if isinstance(e, dict) and isinstance(e.get("notes"), str) and e.get("notes"):
                            e["notes"] = redact_text(str(e.get("notes") or ""))
                retracts = out0.get("retract") if isinstance(out0.get("retract"), list) else []
                for r in retracts:
                    if isinstance(r, dict) and isinstance(r.get("rationale"), str) and r.get("rationale"):
                        r["rationale"] = redact_text(str(r.get("rationale") or ""))
        for rec in learn_suggested_out:
            if not isinstance(rec, dict):
                continue
            chs = rec.get("learn_suggested")
            if not isinstance(chs, list):
                continue
            for ch in chs:
                if not isinstance(ch, dict):
                    continue
                t = ch.get("text")
                if isinstance(t, str) and t:
                    ch["text"] = redact_text(t)
                r = ch.get("rationale")
                if isinstance(r, str) and r:
                    ch["rationale"] = redact_text(r)
        for rec in [why_trace_out, *why_traces_out]:
            if not isinstance(rec, dict) or not rec:
                continue
            q = rec.get("query")
            if isinstance(q, str) and q:
                rec["query"] = redact_text(q)
            out2 = rec.get("output")
            if isinstance(out2, dict):
                for k in ("explanation", "notes"):
                    v = out2.get(k)
                    if isinstance(v, str) and v:
                        out2[k] = redact_text(v)
        items = state_corrupt_recent_out.get("items") if isinstance(state_corrupt_recent_out.get("items"), list) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            for k in ("path", "quarantined_to", "error", "quarantine_error"):
                v = it.get(k)
                if isinstance(v, str) and v:
                    it[k] = redact_text(v)

    out = {
        "project_root": str(project_root),
        "project_dir": str(pp.project_dir),
        "evidence_log": str(pp.evidence_log_path),
        "batch_id": bundle.get("batch_id") or "",
        "thread_id": bundle.get("thread_id") or "",
        "hands_transcript": transcript_path,
        "mi_input": mi_input_text,
        "hands_last_message": hands_last_text,
        "evidence_item": evidence_item_out,
        "check_plan": (bundle.get("check_plan") or {}) if isinstance(bundle.get("check_plan"), dict) else {},
        "auto_answer": (bundle.get("auto_answer") or {}) if isinstance(bundle.get("auto_answer"), dict) else {},
        "risk_event": (bundle.get("risk_event") or {}) if isinstance(bundle.get("risk_event"), dict) else {},
        "state_corrupt_recent": state_corrupt_recent_out,
        "why_trace": why_trace_out,
        "why_traces": why_traces_out,
        "learn_update": learn_update_out,
        "learn_suggested": learn_suggested_out,
        "learn_applied": learn_applied_out,
        "loop_guard": (bundle.get("loop_guard") or {}) if isinstance(bundle.get("loop_guard"), dict) else {},
        "loop_break": (bundle.get("loop_break") or {}) if isinstance(bundle.get("loop_break"), dict) else {},
        "decide_next": decide_next_out,
        "mind_transcripts": (bundle.get("mind_transcripts") or []) if isinstance(bundle.get("mind_transcripts"), list) else [],
    }

    if bool(getattr(args, "json", False)):
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    print(f"thread_id={out['thread_id']} batch_id={out['batch_id']}")
    print(f"project_dir={out['project_dir']}")
    print(f"evidence_log={out['evidence_log']}")
    items2 = state_corrupt_recent_out.get("items") if isinstance(state_corrupt_recent_out.get("items"), list) else []
    if items2:
        labels: list[str] = []
        for it in items2:
            if isinstance(it, dict):
                lab = str(it.get("label") or "").strip()
                if lab:
                    labels.append(lab)
        label_s = ",".join(sorted(set(labels))[:6])
        msg = f"state_corrupt_recent: n={len(items2)}" + (f" labels={label_s}" if label_s else "")
        print(msg)
    if transcript_path:
        print(f"hands_transcript={transcript_path}")
    if out["mi_input"].strip():
        print("\nmi_input:\n" + out["mi_input"].strip())
    if out["hands_last_message"].strip():
        print("\nhands_last_message:\n" + out["hands_last_message"].strip())
    if isinstance(decide_next_out, dict) and decide_next_out:
        st = str(decide_next_out.get("status") or "")
        na = str(decide_next_out.get("next_action") or "")
        cf = decide_next_out.get("confidence")
        try:
            cf_s = f"{float(cf):.2f}" if cf is not None else ""
        except Exception:
            cf_s = str(cf or "")
        hdr = " ".join([x for x in [f"status={st}" if st else "", f"next_action={na}" if na else "", f"confidence={cf_s}" if cf_s else ""] if x])
        if hdr:
            print("\ndecide_next:\n" + hdr)
        notes_s = str(decide_next_out.get("notes") or "").strip()
        if notes_s:
            print("\nnotes:\n" + notes_s)
        if na == "send_to_hands":
            nxt = str(decide_next_out.get("next_hands_input") or "").strip()
            if nxt:
                print("\nnext_hands_input (planned):\n" + nxt)
        if na == "ask_user":
            q = str(decide_next_out.get("ask_user_question") or "").strip()
            if q:
                print("\nask_user_question:\n" + q)
    if isinstance(why_trace_out, dict) and why_trace_out:
        out2 = why_trace_out.get("output") if isinstance(why_trace_out.get("output"), dict) else {}
        st2 = str(why_trace_out.get("state") or "").strip()
        status2 = str(out2.get("status") or "").strip()
        cf2 = out2.get("confidence")
        chosen2 = out2.get("chosen_claim_ids") if isinstance(out2.get("chosen_claim_ids"), list) else []
        edges2 = why_trace_out.get("written_edge_ids") if isinstance(why_trace_out.get("written_edge_ids"), list) else []
        try:
            cf2_s = f"{float(cf2):.2f}" if cf2 is not None else ""
        except Exception:
            cf2_s = str(cf2 or "")
        parts2 = []
        if st2:
            parts2.append(f"state={st2}")
        if status2:
            parts2.append(f"status={status2}")
        if cf2_s:
            parts2.append(f"confidence={cf2_s}")
        parts2.append(f"chosen={len(chosen2)}")
        parts2.append(f"edges_written={len(edges2)}")
        print("\nwhy_trace:\n" + " ".join([x for x in parts2 if x]))
        if chosen2:
            print("chosen_claim_ids:")
            for cid in chosen2[:5]:
                if isinstance(cid, str) and cid.strip():
                    print(f"- {cid.strip()}")
    mts = out.get("mind_transcripts")
    if isinstance(mts, list) and mts:
        print("\nmind_transcripts:")
        for it in mts[:12]:
            if not isinstance(it, dict):
                continue
            k = str(it.get("kind") or "").strip()
            ref = str(it.get("mind_transcript_ref") or "").strip()
            if k and ref:
                print(f"- {k}: {ref}")

    lu = out.get("learn_update")
    if isinstance(lu, dict) and lu:
        print("\nlearn_update:")
        print(f"- {summarize_evidence_record(lu)}")

    ls = out.get("learn_suggested")
    if isinstance(ls, list) and ls:
        print("\nlearn_suggested:")
        for rec in ls[:12]:
            if not isinstance(rec, dict):
                continue
            sid = str(rec.get("id") or "").strip()
            auto = bool(rec.get("auto_learn", True))
            applied_ids = rec.get("applied_claim_ids") if isinstance(rec.get("applied_claim_ids"), list) else []
            summary = summarize_evidence_record(rec)
            if sid and (not auto) and (not applied_ids):
                summary = summary + f" (apply: mi claim apply-suggested {sid} --cd {project_root})"
            print(f"- {summary}")

    la = out.get("learn_applied")
    if isinstance(la, list) and la:
        print("\nlearn_applied:")
        for rec in la[:8]:
            if not isinstance(rec, dict):
                continue
            print(f"- {summarize_evidence_record(rec)}")
    if isinstance(evidence_item_out, dict) and evidence_item_out:
        facts = evidence_item_out.get("facts") if isinstance(evidence_item_out.get("facts"), list) else []
        results = evidence_item_out.get("results") if isinstance(evidence_item_out.get("results"), list) else []
        unknowns = evidence_item_out.get("unknowns") if isinstance(evidence_item_out.get("unknowns"), list) else []
        if facts:
            print("\nfacts:")
            for x in facts[:8]:
                xs = str(x).strip()
                if xs:
                    print(f"- {xs}")
        if results:
            print("\nresults:")
            for x in results[:8]:
                xs = str(x).strip()
                if xs:
                    print(f"- {xs}")
        if unknowns:
            print("\nunknowns:")
            for x in unknowns[:8]:
                xs = str(x).strip()
                if xs:
                    print(f"- {xs}")
    return 0


def _show_evidence_ref(
    *,
    eid: str,
    args: argparse.Namespace,
    home_dir: Path,
    tdb_app: ThoughtDbApplicationService,
    print_json: Callable[[object], None],
) -> int:
    scope, obj = tdb_app.find_evidence_event_prefer_project(
        home_dir=home_dir,
        event_id=eid,
        global_only=bool(getattr(args, "show_global", False)),
    )
    if obj is None:
        print(f"evidence event not found: {eid}", file=sys.stderr)
        return 2
    print_json({"scope": scope, "event": obj})
    return 0


def _show_claim_ref(
    *,
    cid: str,
    tdb_app: ThoughtDbApplicationService,
    print_json: Callable[[object], None],
) -> int:
    found_scope, cobj = tdb_app.find_claim_effective(cid)
    if not cobj:
        print(f"claim not found: {cid}", file=sys.stderr)
        return 2
    print_json({"scope": found_scope, "claim": cobj})
    return 0


def _show_node_ref(
    *,
    nid: str,
    tdb_app: ThoughtDbApplicationService,
    print_json: Callable[[object], None],
) -> int:
    found_scope, nobj = tdb_app.find_node_effective(nid)
    if not nobj:
        print(f"node not found: {nid}", file=sys.stderr)
        return 2
    print_json({"scope": found_scope, "node": nobj})
    return 0


def _show_edge_ref(
    *,
    eid: str,
    tdb: ThoughtDbStore,
    print_json: Callable[[object], None],
) -> int:
    found_scope = ""
    eobj: dict[str, Any] | None = None
    for sc in ("project", "global"):
        v = tdb.load_view(scope=sc)
        for e in v.edges:
            if isinstance(e, dict) and str(e.get("edge_id") or "").strip() == eid:
                found_scope = sc
                eobj = e
                break
        if eobj:
            break
    if not eobj:
        print(f"edge not found: {eid}", file=sys.stderr)
        return 2
    print_json({"scope": found_scope, "edge": eobj})
    return 0


def _show_workflow_ref(
    *,
    wid: str,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    dispatch_fn: Callable[[argparse.Namespace, Path, dict[str, Any]], int],
) -> int:
    args2 = argparse.Namespace(**vars(args))
    args2.cmd = "workflow"
    args2.wf_cmd = "show"
    args2.id = wid
    args2.scope = "effective"
    args2.markdown = not bool(getattr(args, "json", False))
    return dispatch_fn(args2, home_dir, cfg)


def handle_show(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
    dispatch_fn: Callable[[argparse.Namespace, Path, dict[str, Any]], int],
) -> int:
    ref = str(getattr(args, "ref", "") or "").strip()
    if not ref:
        print("missing ref", file=sys.stderr)
        return 2

    token = ref.strip().lower()
    if token in ("last", "@last"):
        return _render_last_bundle(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            resolve_project_root_from_args=resolve_project_root_from_args,
            effective_cd_arg=effective_cd_arg,
        )
    if token in ("project", "overlay"):
        args2 = argparse.Namespace(**vars(args))
        args2.cmd = "project"
        args2.project_cmd = "show"
        return dispatch_fn(args2, home_dir, cfg)
    if token in ("hands", "mind"):
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp2 = ProjectPaths(home_dir=home_dir, project_root=project_root)
        tp = _latest_transcript_path(pp2, mind=token == "mind")
        want_jsonl = bool(getattr(args, "jsonl", False)) or bool(getattr(args, "json", False))
        n = int(getattr(args, "lines", 200) or 200)
        return _render_transcript(tp, lines=n, jsonl=want_jsonl, redact=bool(getattr(args, "redact", False)))

    if ref.endswith(".jsonl") or ref.endswith(".jsonl.gz"):
        tp = Path(ref).expanduser()
        n = int(getattr(args, "lines", 200) or 200)
        return _render_transcript(
            tp,
            lines=n,
            jsonl=bool(getattr(args, "jsonl", False)),
            redact=bool(getattr(args, "redact", False)),
        )

    project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
    pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
    tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)
    tdb_app = ThoughtDbApplicationService(tdb=tdb, project_paths=pp)

    def _print_json(obj: object) -> None:
        s = json.dumps(obj, indent=2, sort_keys=True)
        print(redact_text(s) if bool(getattr(args, "redact", False)) else s)

    if ref.startswith("ev_"):
        return _show_evidence_ref(eid=ref, args=args, home_dir=home_dir, tdb_app=tdb_app, print_json=_print_json)

    if ref.startswith("cl_"):
        return _show_claim_ref(cid=ref, tdb_app=tdb_app, print_json=_print_json)

    if ref.startswith("nd_"):
        return _show_node_ref(nid=ref, tdb_app=tdb_app, print_json=_print_json)

    if ref.startswith("ed_"):
        return _show_edge_ref(eid=ref, tdb=tdb, print_json=_print_json)

    if ref.startswith("wf_"):
        return _show_workflow_ref(wid=ref, args=args, home_dir=home_dir, cfg=cfg, dispatch_fn=dispatch_fn)

    print(
        f"unknown ref: {ref} (expected ev_/cl_/nd_/wf_/ed_, a transcript .jsonl path, or one of: last/project/hands/mind)",
        file=sys.stderr,
    )
    return 2


def handle_tail(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int:
    target = str(getattr(args, "target", "evidence") or "evidence").strip().lower() or "evidence"
    redact = bool(getattr(args, "redact", False))
    n_arg = getattr(args, "lines", None)

    if target == "evidence":
        n = int(n_arg) if n_arg is not None else 20
        use_global = bool(getattr(args, "tail_global", False))
        if use_global:
            path = GlobalPaths(home_dir=home_dir).global_evidence_log_path
        else:
            project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
            pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
            path = pp.evidence_log_path

        if bool(getattr(args, "raw", False)):
            for line in tail_raw_lines(path, n):
                print(redact_text(line) if redact else line)
            return 0

        objs = tail_json_objects(path, n)
        if bool(getattr(args, "json", False)):
            s = json.dumps(objs, indent=2, sort_keys=True)
            print(redact_text(s) if redact else s)
            return 0
        for obj in objs:
            s = summarize_evidence_record(obj)
            print(redact_text(s) if redact else s)
        return 0

    if target in ("hands", "mind"):
        n = int(n_arg) if n_arg is not None else 200
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        tp = _latest_transcript_path(pp, mind=target == "mind")
        want_jsonl = bool(getattr(args, "jsonl", False)) or bool(getattr(args, "raw", False))
        return _render_transcript(tp, lines=n, jsonl=want_jsonl, redact=redact)

    print(f"unknown tail target: {target!r} (expected evidence/hands/mind)", file=sys.stderr)
    return 2
