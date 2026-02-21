from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from ..core.paths import GlobalPaths, ProjectPaths
from ..core.storage import now_rfc3339
from ..memory.ingest import thoughtdb_node_item
from ..memory.service import MemoryService
from ..runtime.evidence import EvidenceWriter, new_run_id
from ..thoughtdb import ThoughtDbStore
from ..thoughtdb.app_service import ThoughtDbApplicationService


def _read_stdin_text() -> str:
    try:
        return sys.stdin.read()
    except Exception:
        return ""

def handle_node_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int | None:
    if args.cmd == "node":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)
        tdb_app = ThoughtDbApplicationService(tdb=tdb, project_paths=pp)

        def _iter_effective_nodes(*, include_inactive: bool, include_aliases: bool) -> list[dict[str, Any]]:
            return tdb_app.list_effective_nodes(
                include_inactive=include_inactive,
                include_aliases=include_aliases,
            )

        def _find_node_effective(nid: str) -> tuple[str, dict[str, Any] | None]:
            """Return (scope, node) searching project then global."""
            return tdb_app.find_node_effective(nid)

        if args.node_cmd == "list":
            scope = str(getattr(args, "scope", "project") or "project").strip()
            raw_statuses = getattr(args, "status", None) or []
            want_statuses = {str(x).strip() for x in raw_statuses if str(x).strip()}
            include_inactive = bool(getattr(args, "all", False)) or (bool(want_statuses) and want_statuses != {"active"})
            include_aliases = bool(getattr(args, "all", False))

            raw_tags = getattr(args, "tag", None) or []
            want_tags = {str(x).strip().lower() for x in raw_tags if str(x).strip()}
            contains = str(getattr(args, "contains", "") or "").strip().lower()
            raw_types = getattr(args, "node_type", None) or []
            want_types = {str(x).strip() for x in raw_types if str(x).strip()}
            try:
                limit = int(getattr(args, "limit", 0) or 0)
            except Exception:
                limit = 0

            def _node_matches(n: dict[str, Any]) -> bool:
                if want_types and str(n.get("node_type") or "").strip() not in want_types:
                    return False
                if want_statuses and str(n.get("status") or "").strip() not in want_statuses:
                    return False
                if want_tags:
                    tags = n.get("tags") if isinstance(n.get("tags"), list) else []
                    tagset = {str(x).strip().lower() for x in tags if str(x).strip()}
                    if not all(t in tagset for t in want_tags):
                        return False
                if contains:
                    title = str(n.get("title") or "")
                    text = str(n.get("text") or "")
                    blob = (title + "\n" + text).lower()
                    if contains not in blob:
                        return False
                return True

            if scope == "effective":
                items = _iter_effective_nodes(include_inactive=include_inactive, include_aliases=include_aliases)
            else:
                v = tdb.load_view(scope=scope)
                items = list(v.iter_nodes(include_inactive=include_inactive, include_aliases=include_aliases))
                items.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)

            items = [x for x in items if isinstance(x, dict) and _node_matches(x)]
            if limit > 0:
                items = items[:limit]

            if getattr(args, "json", False):
                print(json.dumps(items, indent=2, sort_keys=True))
                return 0

            if not items:
                print("(no nodes)")
                return 0
            for n in items:
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("node_id") or "").strip()
                nt = str(n.get("node_type") or "").strip()
                st = str(n.get("status") or "").strip()
                sc = str(n.get("scope") or scope).strip()
                title = str(n.get("title") or "").strip().replace("\n", " ")
                if len(title) > 140:
                    title = title[:137] + "..."
                print(f"{nid} scope={sc} status={st} type={nt} {title}".strip())
            return 0

        if args.node_cmd == "show":
            nid = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "effective") or "effective").strip()
            found_scope = ""
            obj: dict[str, Any] | None = None
            edges: list[dict[str, Any]] = []

            want_graph = bool(getattr(args, "graph", False))
            if want_graph and not bool(getattr(args, "json", False)):
                print("--graph requires --json", file=sys.stderr)
                return 2

            if scope == "effective":
                found_scope, obj = _find_node_effective(nid)
                if found_scope:
                    edges = tdb_app.related_edges_for_id(scope=found_scope, item_id=nid)
            else:
                found_scope, obj = tdb_app.find_node(scope=scope, node_id=nid)
                if found_scope:
                    edges = tdb_app.related_edges_for_id(scope=found_scope, item_id=nid)

            if not obj:
                print(f"node not found: {nid}", file=sys.stderr)
                return 2

            payload = {"scope": found_scope, "node": obj, "edges": edges}
            if want_graph:
                edge_types_raw = getattr(args, "edge_types", None) or []
                etypes = {str(x).strip() for x in edge_types_raw if str(x).strip()}
                graph_scope = scope if scope == "effective" else found_scope
                payload["graph"] = tdb_app.build_subgraph(
                    scope=graph_scope,
                    root_id=str(obj.get("node_id") or nid).strip() or nid,
                    depth=int(getattr(args, "depth", 1) or 1),
                    direction=str(getattr(args, "direction", "both") or "both").strip(),
                    edge_types=etypes,
                    include_inactive=bool(getattr(args, "include_inactive", False)),
                    include_aliases=bool(getattr(args, "include_aliases", False)),
                )
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            n = obj
            print(f"node_id={n.get('node_id')}")
            if n.get("requested_id") and n.get("requested_id") != n.get("node_id"):
                print(f"requested_id={n.get('requested_id')}")
            print(f"scope={found_scope}")
            print(f"type={n.get('node_type')}")
            print(f"status={n.get('status')}")
            canon = n.get("canonical_id")
            if canon and canon != n.get("node_id"):
                print(f"canonical_id={canon}")
            title = str(n.get("title") or "").strip()
            if title:
                print(f"title={title}")
            text = str(n.get("text") or "").strip()
            if text:
                print("text:")
                print(text)
            if edges:
                print(f"edges={len(edges)}")
            return 0

        if args.node_cmd == "create":
            scope = str(getattr(args, "scope", "project") or "project").strip()
            nt = str(getattr(args, "node_type", "") or "").strip()
            title = str(getattr(args, "title", "") or "").strip()
            raw_text = str(getattr(args, "text", "-") or "-").strip()
            text = _read_stdin_text() if (not raw_text or raw_text == "-") else raw_text
            if not text.strip():
                print("node text is empty", file=sys.stderr)
                return 2

            vis = str(getattr(args, "visibility", "") or "").strip() or ("global" if scope == "global" else "project")
            tags = [str(x).strip() for x in (getattr(args, "tag", None) or []) if str(x).strip()]
            cite_raw = getattr(args, "cite", None) or []
            cite = [str(x).strip() for x in cite_raw if str(x).strip()]
            notes = str(getattr(args, "notes", "") or "").strip()
            try:
                conf = float(getattr(args, "confidence", 1.0) or 1.0)
            except Exception:
                conf = 1.0

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "node_create",
                    "batch_id": "cli.node_create",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "node_type": nt,
                    "title": title,
                    "text": text,
                    "visibility": vis,
                    "tags": tags,
                    "confidence": conf,
                    "notes": notes,
                    "cite_event_ids": cite,
                }
            )
            ev_id = str(ev.get("event_id") or "").strip()
            source_event_ids = [x for x in [ev_id, *cite] if x]
            try:
                nid = tdb.append_node_create(
                    node_type=nt,
                    title=title,
                    text=text,
                    scope=scope,
                    visibility=vis,
                    tags=tags,
                    source_event_ids=source_event_ids,
                    confidence=conf,
                    notes=notes,
                )
            except Exception as e:
                print(f"node create failed: {e}", file=sys.stderr)
                return 2

            # Derived: index the node for text recall (best-effort; no hard dependency).
            try:
                nodes_path = (
                    GlobalPaths(home_dir=home_dir).thoughtdb_global_nodes_path
                    if scope == "global"
                    else pp.thoughtdb_nodes_path
                )
                refs = [{"kind": "evidence_event", "event_id": x} for x in source_event_ids[:12] if x]
                it = thoughtdb_node_item(
                    node_id=nid,
                    node_type=nt,
                    title=title,
                    text=text,
                    scope=scope,
                    project_id="" if scope == "global" else pp.project_id,
                    ts=now_rfc3339(),
                    visibility=vis,
                    tags=tags,
                    nodes_path=nodes_path,
                    source_refs=refs,
                )
                MemoryService(home_dir).upsert_items([it])
            except Exception:
                pass

            payload = {"node_id": nid, "scope": scope}
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            print(nid)
            return 0

        if args.node_cmd == "retract":
            nid = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "project") or "project").strip()
            rationale = str(getattr(args, "rationale", "") or "").strip()

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "node_retract",
                    "batch_id": "cli.node_retract",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "node_id": nid,
                    "rationale": rationale,
                }
            )
            ev_id = str(ev.get("event_id") or "").strip()
            try:
                tdb.append_node_retract(
                    node_id=nid,
                    scope=scope,
                    rationale=rationale,
                    source_event_ids=[ev_id] if ev_id else [],
                )
            except Exception as e:
                print(f"node retract failed: {e}", file=sys.stderr)
                return 2
            print(nid)
            return 0

        print("unknown node subcommand", file=sys.stderr)
        return 2


    return None
