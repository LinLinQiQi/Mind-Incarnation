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

def handle_edge_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int | None:
    if args.cmd == "edge":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)

        def _iter_edges_for_scope(scope: str) -> list[dict[str, Any]]:
            v = tdb.load_view(scope=scope)
            return [e for e in v.edges if isinstance(e, dict) and str(e.get("kind") or "").strip() == "edge"]

        if args.edge_cmd == "create":
            scope = str(getattr(args, "scope", "project") or "project").strip()
            et = str(getattr(args, "edge_type", "") or "").strip()
            frm = str(getattr(args, "from_id", "") or "").strip()
            to = str(getattr(args, "to_id", "") or "").strip()
            vis = str(getattr(args, "visibility", "") or "").strip() or ("global" if scope == "global" else "project")
            notes = str(getattr(args, "notes", "") or "").strip()

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "edge_create",
                    "batch_id": "cli.edge_create",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "edge_type": et,
                    "from_id": frm,
                    "to_id": to,
                    "visibility": vis,
                    "notes": notes,
                }
            )
            ev_id = str(ev.get("event_id") or "").strip()
            try:
                eid = tdb.append_edge(
                    edge_type=et,
                    from_id=frm,
                    to_id=to,
                    scope=scope,
                    visibility=vis,
                    source_event_ids=[ev_id] if ev_id else [],
                    notes=notes,
                )
            except Exception as e:
                print(f"edge create failed: {e}", file=sys.stderr)
                return 2

            payload = {"edge_id": eid, "scope": scope, "edge_type": et, "from_id": frm, "to_id": to}
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            print(eid)
            return 0

        if args.edge_cmd == "list":
            scope = str(getattr(args, "scope", "project") or "project").strip()
            edge_type = str(getattr(args, "edge_type", "") or "").strip()
            from_id = str(getattr(args, "from_id", "") or "").strip()
            to_id = str(getattr(args, "to_id", "") or "").strip()
            try:
                limit = int(getattr(args, "limit", 50) or 50)
            except Exception:
                limit = 50
            limit = max(1, min(500, limit))

            items: list[dict[str, Any]] = []
            seen_keys: set[str] = set()

            scopes = [scope] if scope in ("project", "global") else ["project", "global"]
            for sc in scopes:
                for e in _iter_edges_for_scope(sc):
                    et = str(e.get("edge_type") or "").strip()
                    frm = str(e.get("from_id") or "").strip()
                    to = str(e.get("to_id") or "").strip()
                    if edge_type and et != edge_type:
                        continue
                    if from_id and frm != from_id:
                        continue
                    if to_id and to != to_id:
                        continue

                    key = f"{et}|{frm}|{to}"
                    if scope == "effective":
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                    items.append(e)
                    if len(items) >= limit:
                        break
                if len(items) >= limit:
                    break

            # Newest first when possible.
            items.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)

            if getattr(args, "json", False):
                print(json.dumps(items, indent=2, sort_keys=True))
                return 0

            if not items:
                print("(no edges)")
                return 0
            for e in items:
                eid = str(e.get("edge_id") or "").strip()
                et = str(e.get("edge_type") or "").strip()
                frm = str(e.get("from_id") or "").strip()
                to = str(e.get("to_id") or "").strip()
                sc = str(e.get("scope") or "").strip()
                print(f"{eid} scope={sc} type={et} {frm} -> {to}".strip())
            return 0

        if args.edge_cmd == "show":
            eid = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "effective") or "effective").strip()

            found_scope = ""
            obj: dict[str, Any] | None = None

            scopes = [scope] if scope in ("project", "global") else ["project", "global"]
            for sc in scopes:
                for e in _iter_edges_for_scope(sc):
                    if str(e.get("edge_id") or "").strip() == eid:
                        found_scope = sc
                        obj = e
                        break
                if obj:
                    break

            if not obj:
                print(f"edge not found: {eid}", file=sys.stderr)
                return 2

            payload = {"scope": found_scope, "edge": obj}
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        print("unknown edge subcommand", file=sys.stderr)
        return 2


    return None
