from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from ..core.paths import GlobalPaths, ProjectPaths
from ..core.storage import append_jsonl, iter_jsonl, now_rfc3339
from ..providers.provider_factory import make_mind_provider
from ..runtime.evidence import EvidenceWriter, new_run_id
from ..thoughtdb import ThoughtDbStore, claim_signature
from ..thoughtdb.context import build_decide_next_thoughtdb_context
from ..thoughtdb.graph import build_subgraph_for_id
from ..thoughtdb.why import (
    collect_candidate_claims,
    collect_candidate_claims_for_target,
    default_as_of_ts,
    find_evidence_event,
    query_from_evidence_event,
    run_why_trace,
)
from ..project.overlay_store import load_project_overlay, write_project_overlay
from ..workflows import (
    WorkflowStore,
    GlobalWorkflowStore,
    WorkflowRegistry,
    apply_global_overrides,
    new_workflow_id,
    normalize_workflow,
    render_workflow_markdown,
)
from ..workflows.hosts import parse_host_bindings, sync_host_binding, sync_hosts_from_overlay

def handle_why_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int | None:
    if args.cmd == "why":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)

        # Providers/stores.
        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)
        mem = MemoryService(home_dir)
        mind = make_mind_provider(cfg, project_root=project_root, transcripts_dir=pp.transcripts_dir)

        top_k = int(getattr(args, "top_k", 12) or 12)
        as_of_ts = str(getattr(args, "as_of", "") or "").strip() or default_as_of_ts()

        def _write_why_evidence(*, payload: dict[str, Any]) -> dict[str, Any]:
            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            return evw.append(payload)

        if args.why_cmd in ("event", "last"):
            if args.why_cmd == "last":
                bundle = load_last_batch_bundle(pp.evidence_log_path)
                target_obj = None
                for key in ("decide_next", "evidence_item", "hands_input"):
                    v = bundle.get(key)
                    if isinstance(v, dict) and str(v.get("event_id") or "").strip():
                        target_obj = v
                        break
                if not isinstance(target_obj, dict):
                    print("no recent event found for why last (need decide_next/evidence/hands_input with event_id)", file=sys.stderr)
                    return 2
                event_id = str(target_obj.get("event_id") or "").strip()
            else:
                event_id = str(getattr(args, "event_id", "") or "").strip()
                target_obj = find_evidence_event(evidence_log_path=pp.evidence_log_path, event_id=event_id)
                if not isinstance(target_obj, dict):
                    print(f"event_id not found in EvidenceLog: {event_id}", file=sys.stderr)
                    return 2

            query = query_from_evidence_event(target_obj)
            candidates = collect_candidate_claims_for_target(
                tdb=tdb,
                mem=mem,
                project_paths=pp,
                target_obj=target_obj,
                query=query,
                top_k=top_k,
                as_of_ts=as_of_ts,
                target_event_id=event_id,
            )
            if not candidates:
                payload = _write_why_evidence(
                    payload={
                        "kind": "why_trace",
                        "batch_id": "cli.why_trace",
                        "ts": now_rfc3339(),
                        "thread_id": "",
                        "target": {"target_type": "evidence_event", "event_id": event_id, "evidence_kind": str(target_obj.get("kind") or "")},
                        "as_of_ts": as_of_ts,
                        "query": query,
                        "candidate_claim_ids": [],
                        "state": "ok",
                        "mind_transcript_ref": "",
                        "output": {"status": "insufficient", "confidence": 0.0, "chosen_claim_ids": [], "explanation": "", "notes": "no candidate claims"},
                        "written_edge_ids": [],
                    }
                )
                if getattr(args, "json", False):
                    print(json.dumps(payload, indent=2, sort_keys=True))
                    return 0
                print("insufficient (no candidate claims)")
                return 0

            target = {
                "target_type": "evidence_event",
                "event_id": event_id,
                "evidence_kind": str(target_obj.get("kind") or "").strip(),
                "batch_id": str(target_obj.get("batch_id") or "").strip(),
            }
            outcome = run_why_trace(
                mind=mind,
                tdb=tdb,
                mem=mem,
                project_paths=pp,
                target=target,
                candidate_claims=candidates,
                as_of_ts=as_of_ts,
                write_edges_from_event_id=event_id,
            )

            payload = _write_why_evidence(
                payload={
                    "kind": "why_trace",
                    "batch_id": "cli.why_trace",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "target": target,
                    "as_of_ts": as_of_ts,
                    "query": query,
                    "candidate_claim_ids": [str(c.get("claim_id") or "") for c in candidates if isinstance(c, dict) and str(c.get("claim_id") or "").strip()],
                    "state": "ok",
                    "mind_transcript_ref": outcome.mind_transcript_ref,
                    "output": outcome.obj,
                    "written_edge_ids": list(outcome.written_edge_ids),
                }
            )
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0

            out = outcome.obj if isinstance(outcome.obj, dict) else {}
            print(f"status={out.get('status')} confidence={out.get('confidence')}")
            chosen = out.get("chosen_claim_ids") if isinstance(out.get("chosen_claim_ids"), list) else []
            if chosen:
                print("chosen_claim_ids:")
                for cid in chosen:
                    print(f"- {cid}")
            expl = str(out.get("explanation") or "").strip()
            if expl:
                print("explanation:")
                print(expl)
            return 0

        if args.why_cmd == "claim":
            claim_id = str(getattr(args, "claim_id", "") or "").strip()
            scope = str(getattr(args, "scope", "effective") or "effective").strip()

            found_scope = ""
            claim_obj: dict[str, Any] | None = None

            if scope == "effective":
                for sc in ("project", "global"):
                    v = tdb.load_view(scope=sc)
                    if claim_id in v.claims_by_id:
                        claim_obj = dict(v.claims_by_id[claim_id])
                        claim_obj["status"] = v.claim_status(claim_id)
                        claim_obj["canonical_id"] = v.resolve_id(claim_id)
                        found_scope = sc
                        break
                    canon = v.resolve_id(claim_id)
                    if canon and canon in v.claims_by_id:
                        claim_obj = dict(v.claims_by_id[canon])
                        claim_obj["status"] = v.claim_status(canon)
                        claim_obj["canonical_id"] = v.resolve_id(canon)
                        claim_obj["requested_id"] = claim_id
                        found_scope = sc
                        break
            else:
                v = tdb.load_view(scope=scope)
                if claim_id in v.claims_by_id:
                    claim_obj = dict(v.claims_by_id[claim_id])
                    claim_obj["status"] = v.claim_status(claim_id)
                    claim_obj["canonical_id"] = v.resolve_id(claim_id)
                    found_scope = scope

            if not claim_obj:
                print(f"claim not found: {claim_id}", file=sys.stderr)
                return 2

            query = str(claim_obj.get("text") or "").strip()
            candidates = collect_candidate_claims(
                tdb=tdb,
                mem=mem,
                project_paths=pp,
                query=query,
                top_k=top_k,
                target_event_id="",
            )

            target = {
                "target_type": "claim",
                "claim_id": str(claim_obj.get("claim_id") or "").strip(),
                "scope": found_scope or str(claim_obj.get("scope") or "").strip(),
                "claim_type": str(claim_obj.get("claim_type") or "").strip(),
                "status": str(claim_obj.get("status") or "").strip(),
                "text": str(claim_obj.get("text") or "").strip(),
            }
            outcome = run_why_trace(
                mind=mind,
                tdb=tdb,
                mem=mem,
                project_paths=pp,
                target=target,
                candidate_claims=candidates,
                as_of_ts=as_of_ts,
                write_edges_from_event_id="",
            )

            payload = _write_why_evidence(
                payload={
                    "kind": "why_trace",
                    "batch_id": "cli.why_trace",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "target": target,
                    "as_of_ts": as_of_ts,
                    "query": query,
                    "candidate_claim_ids": [str(c.get("claim_id") or "") for c in candidates if isinstance(c, dict) and str(c.get("claim_id") or "").strip()],
                    "state": "ok",
                    "mind_transcript_ref": outcome.mind_transcript_ref,
                    "output": outcome.obj,
                    "written_edge_ids": list(outcome.written_edge_ids),
                }
            )
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0

            out = outcome.obj if isinstance(outcome.obj, dict) else {}
            print(f"status={out.get('status')} confidence={out.get('confidence')}")
            chosen = out.get("chosen_claim_ids") if isinstance(out.get("chosen_claim_ids"), list) else []
            if chosen:
                print("chosen_claim_ids:")
                for cid in chosen:
                    print(f"- {cid}")
            expl = str(out.get("explanation") or "").strip()
            if expl:
                print("explanation:")
                print(expl)
            return 0

        print("unknown why subcommand", file=sys.stderr)
        return 2


    return None
