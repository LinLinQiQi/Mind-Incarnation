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

def handle_host_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int | None:
    if args.cmd == "host":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        overlay2 = load_project_overlay(home_dir=home_dir, project_root=project_root)
        if not isinstance(overlay2, dict):
            overlay2 = {}
        hb = overlay2.get("host_bindings")
        bindings = hb if isinstance(hb, list) else []

        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        wf_store = WorkflowStore(pp)
        wf_global = GlobalWorkflowStore(GlobalPaths(home_dir=home_dir))
        wf_reg = WorkflowRegistry(project_store=wf_store, global_store=wf_global)

        def _sync_hosts() -> dict[str, Any]:
            eff = wf_reg.enabled_workflows_effective(overlay=overlay2)
            eff2 = [{k: v for k, v in w.items() if k != "_mi_scope"} for w in eff if isinstance(w, dict)]
            return sync_hosts_from_overlay(overlay=overlay2, project_id=pp.project_id, workflows=eff2)

        if args.host_cmd == "list":
            parsed = parse_host_bindings(overlay2)
            if not parsed:
                print("(no host bindings)")
                return 0
            for b in parsed:
                print(f"{b.host} enabled={str(bool(b.enabled)).lower()} workspace_root={b.workspace_root} generated_rel_dir={b.generated_rel_dir}")
            return 0

        if args.host_cmd == "bind":
            reg_dirs: list[dict[str, str]] = []
            for item in args.symlink_dir or []:
                s = str(item or "").strip()
                if not s:
                    continue
                if ":" not in s:
                    print(f"invalid --symlink-dir (expected SRC:DST): {s}", file=sys.stderr)
                    return 2
                src, dst = s.split(":", 1)
                src = src.strip()
                dst = dst.strip()
                if not src or not dst:
                    print(f"invalid --symlink-dir (empty SRC or DST): {s}", file=sys.stderr)
                    return 2
                reg_dirs.append({"src": src, "dst": dst})

            new_binding: dict[str, Any] = {
                "host": str(args.host),
                "workspace_root": str(args.workspace),
                "enabled": True,
            }
            if str(args.generated_rel_dir or "").strip():
                new_binding["generated_rel_dir"] = str(args.generated_rel_dir).strip()
            if reg_dirs:
                new_binding["register"] = {"symlink_dirs": reg_dirs}

            out: list[dict[str, Any]] = []
            for b in bindings:
                if isinstance(b, dict) and str(b.get("host") or "").strip() == str(args.host).strip():
                    continue
                if isinstance(b, dict):
                    out.append(b)
            out.append(new_binding)
            overlay2["host_bindings"] = out
            write_project_overlay(home_dir=home_dir, project_root=project_root, overlay=overlay2)

            res = _sync_hosts()
            if bool(args.host_cmd) and not bool(res.get("ok", True)):
                print(json.dumps(res, indent=2, sort_keys=True), file=sys.stderr)

            print(f"bound host={args.host} workspace={args.workspace}")
            return 0

        if args.host_cmd == "unbind":
            host = str(args.host).strip()
            old_overlay = dict(overlay2)
            out: list[dict[str, Any]] = []
            removed_any = False
            for b in bindings:
                if isinstance(b, dict) and str(b.get("host") or "").strip() == host:
                    removed_any = True
                    continue
                if isinstance(b, dict):
                    out.append(b)
            overlay2["host_bindings"] = out
            write_project_overlay(home_dir=home_dir, project_root=project_root, overlay=overlay2)

            # Best-effort cleanup for the removed host binding.
            parsed_old = parse_host_bindings(old_overlay)
            cleanup_results: list[dict[str, Any]] = []
            for b in parsed_old:
                if b.host == host:
                    cleanup_results.append(sync_host_binding(binding=b, project_id=pp.project_id, workflows=[]))
            if cleanup_results and not all(bool(r.get("ok", True)) for r in cleanup_results):
                print(json.dumps({"ok": False, "cleanup_results": cleanup_results}, indent=2, sort_keys=True), file=sys.stderr)

            if not removed_any:
                print(f"(host not bound) {host}")
                return 0
            print(f"unbound host={host}")
            return 0

        if args.host_cmd == "sync":
            res = _sync_hosts()
            if bool(args.json):
                print(json.dumps(res, indent=2, sort_keys=True))
                return 0
            ok = bool(res.get("ok", True))
            print(f"ok={str(ok).lower()}")
            results = res.get("results") if isinstance(res.get("results"), list) else []
            for r in results:
                if not isinstance(r, dict):
                    continue
                host = str(r.get("host") or "").strip()
                ok2 = bool(r.get("ok", False))
                gen = str(r.get("generated_root") or "").strip()
                ws = str(r.get("workspace_root") or "").strip()
                n = r.get("workflows_n")
                print(f"- {host} ok={str(ok2).lower()} workflows_n={n} workspace_root={ws} generated_root={gen}")
            return 0 if ok else 1

        return 2

    return None
