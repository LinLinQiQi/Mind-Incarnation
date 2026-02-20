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

def handle_workflow_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
    read_user_line: Callable[[str], str],
    unified_diff: Callable[..., str],
) -> int | None:
    if args.cmd == "workflow":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        overlay2 = load_project_overlay(home_dir=home_dir, project_root=project_root)
        if not isinstance(overlay2, dict):
            overlay2 = {}

        wf_store = WorkflowStore(pp)
        wf_global = GlobalWorkflowStore(GlobalPaths(home_dir=home_dir))
        wf_reg = WorkflowRegistry(project_store=wf_store, global_store=wf_global)
        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)

        runtime_cfg = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
        wf_cfg = runtime_cfg.get("workflows") if isinstance(runtime_cfg.get("workflows"), dict) else {}

        def _effective_enabled_workflows() -> list[dict[str, Any]]:
            eff = wf_reg.enabled_workflows_effective(overlay=overlay2)
            # Internal markers should not leak into derived artifacts.
            return [{k: v for k, v in w.items() if k != "_mi_scope"} for w in eff if isinstance(w, dict)]

        def _auto_sync_hosts() -> None:
            if not bool(wf_cfg.get("auto_sync_on_change", True)):
                return
            res = sync_hosts_from_overlay(overlay=overlay2, project_id=pp.project_id, workflows=_effective_enabled_workflows())
            if not bool(res.get("ok", True)):
                print(json.dumps(res, indent=2, sort_keys=True), file=sys.stderr)

        if args.wf_cmd == "list":
            scope = str(getattr(args, "scope", "project") or "project").strip()
            if scope == "global":
                ids = wf_global.list_ids()
                if not ids:
                    print("(no workflows)")
                    return 0
                for wid in ids:
                    try:
                        w = wf_global.load(wid)
                    except Exception:
                        print(f"{wid} (failed to load)")
                        continue
                    name = str(w.get("name") or "").strip()
                    enabled = bool(w.get("enabled", False))
                    print(f"{wid} enabled={str(enabled).lower()} {name}".strip())
                return 0

            if scope == "effective":
                items = wf_reg.workflows_effective(overlay=overlay2, enabled_only=False)
                if not items:
                    print("(no workflows)")
                    return 0
                for w in items:
                    if not isinstance(w, dict):
                        continue
                    wid = str(w.get("id") or "").strip()
                    name = str(w.get("name") or "").strip()
                    enabled = bool(w.get("enabled", False))
                    sc = str(w.get("_mi_scope") or "").strip() or "?"
                    print(f"{wid} scope={sc} enabled={str(enabled).lower()} {name}".strip())
                return 0

            # project (default)
            ids = wf_store.list_ids()
            if not ids:
                print("(no workflows)")
                return 0
            for wid in ids:
                try:
                    w = wf_store.load(wid)
                except Exception:
                    print(f"{wid} (failed to load)")
                    continue
                name = str(w.get("name") or "").strip()
                enabled = bool(w.get("enabled", False))
                print(f"{wid} enabled={str(enabled).lower()} {name}".strip())
            return 0

        if args.wf_cmd == "show":
            wid = str(args.id)
            scope = str(getattr(args, "scope", "project") or "project").strip()
            if scope == "global":
                w = wf_global.load(wid)
            elif scope == "effective":
                # Apply overlay overrides when the workflow is global.
                items = wf_reg.workflows_effective(overlay=overlay2, enabled_only=False)
                by_id = {str(x.get("id") or "").strip(): x for x in items if isinstance(x, dict)}
                if wid not in by_id:
                    raise FileNotFoundError(f"workflow not found: {wid}")
                w = by_id[wid]
            else:
                w = wf_store.load(wid)
            if args.json:
                print(json.dumps(w, indent=2, sort_keys=True))
                return 0
            if args.markdown or (not args.json):
                print(render_workflow_markdown(w), end="")
                return 0

        if args.wf_cmd == "create":
            wid = new_workflow_id()
            w = {
                "version": "v1",
                "id": wid,
                "name": str(args.name),
                "enabled": not bool(args.disabled),
                "trigger": {"mode": str(args.trigger_mode), "pattern": str(args.pattern or "")},
                "mermaid": "",
                "steps": [],
                "source": {"kind": "manual", "reason": "created via mi workflow create", "evidence_refs": []},
                "created_ts": now_rfc3339(),
                "updated_ts": now_rfc3339(),
            }
            scope = str(getattr(args, "scope", "project") or "project").strip()
            if scope == "global":
                wf_global.write(w)
            else:
                wf_store.write(w)
            _auto_sync_hosts()
            print(wid)
            return 0

        if args.wf_cmd in ("enable", "disable"):
            wid = str(args.id)
            enabled_target = True if args.wf_cmd == "enable" else False
            scope = str(getattr(args, "scope", "project") or "project").strip()

            if scope == "global" and bool(getattr(args, "project_override", False)):
                overlay2.setdefault("global_workflow_overrides", {})
                ov = overlay2.get("global_workflow_overrides")
                if not isinstance(ov, dict):
                    ov = {}
                    overlay2["global_workflow_overrides"] = ov
                ov[wid] = {"enabled": bool(enabled_target)}
                write_project_overlay(home_dir=home_dir, project_root=project_root, overlay=overlay2)
                _auto_sync_hosts()
                print(f"{wid} project_override_enabled={str(bool(enabled_target)).lower()}")
                return 0

            # Mutate the workflow source of truth (project/global/effective resolution).
            if scope == "global":
                w0 = wf_global.load(wid)
                w1 = dict(w0)
                w1["enabled"] = bool(enabled_target)
                wf_global.write(w1)
            elif scope == "effective":
                try:
                    w0 = wf_store.load(wid)
                    w1 = dict(w0)
                    w1["enabled"] = bool(enabled_target)
                    wf_store.write(w1)
                except Exception:
                    w0 = wf_global.load(wid)
                    w1 = dict(w0)
                    w1["enabled"] = bool(enabled_target)
                    wf_global.write(w1)
            else:
                w0 = wf_store.load(wid)
                w1 = dict(w0)
                w1["enabled"] = bool(enabled_target)
                wf_store.write(w1)
            _auto_sync_hosts()
            print(f"{wid} enabled={str(bool(w1['enabled'])).lower()}")
            return 0

        if args.wf_cmd == "delete":
            wid = str(args.id)
            scope = str(getattr(args, "scope", "project") or "project").strip()
            if scope == "global" and bool(getattr(args, "project_override", False)):
                ov = overlay2.get("global_workflow_overrides")
                if not isinstance(ov, dict):
                    ov = {}
                    overlay2["global_workflow_overrides"] = ov
                if wid in ov:
                    del ov[wid]
                    write_project_overlay(home_dir=home_dir, project_root=project_root, overlay=overlay2)
                _auto_sync_hosts()
                print(f"cleared override for {wid}")
                return 0
            if scope == "global":
                wf_global.delete(wid)
            else:
                wf_store.delete(wid)
            _auto_sync_hosts()
            print(f"deleted {wid} (scope={scope})")
            return 0

        if args.wf_cmd == "edit":
            wid = str(args.id)
            scope = str(getattr(args, "scope", "project") or "project").strip()
            project_override = bool(getattr(args, "project_override", False))
            if scope == "effective":
                # Resolve once for the edit loop.
                try:
                    wf_store.load(wid)
                    scope = "project"
                except Exception:
                    scope = "global"

            def _run_once(req: str) -> int:
                req = (req or "").strip()
                if not req:
                    return 0
                w_global0 = wf_global.load(wid) if scope == "global" else {}
                if scope == "global" and project_override:
                    # Edit the effective global workflow (global + current project override), then persist a patch to overlay.
                    w0 = apply_global_overrides(w_global0, overlay=overlay2)
                else:
                    w0 = wf_global.load(wid) if scope == "global" else wf_store.load(wid)
                llm = make_mind_provider(cfg, project_root=project_root, transcripts_dir=pp.transcripts_dir)
                tdb_ctx = build_decide_next_thoughtdb_context(
                    tdb=tdb,
                    as_of_ts=now_rfc3339(),
                    task=req,
                    hands_last_message="",
                    recent_evidence=[],
                )
                tdb_ctx_obj = tdb_ctx.to_prompt_obj()
                prompt = edit_workflow_prompt(
                    mindspec_base=runtime_cfg,
                    project_overlay=overlay2,
                    thought_db_context=tdb_ctx_obj,
                    workflow=w0,
                    user_request=req,
                )
                try:
                    out = llm.call(schema_filename="edit_workflow.json", prompt=prompt, tag=f"edit_workflow:{wid}").obj
                except Exception as e:
                    print(f"edit_workflow failed: {e}", file=sys.stderr)
                    return 2

                if not isinstance(out, dict) or not isinstance(out.get("workflow"), dict):
                    print("edit_workflow returned invalid output", file=sys.stderr)
                    return 2

                w1 = dict(out["workflow"])
                # Enforce invariants regardless of model output.
                base_for_invariants = w_global0 if (scope == "global") else w0
                w1["id"] = base_for_invariants.get("id")
                w1["version"] = base_for_invariants.get("version")
                w1["created_ts"] = base_for_invariants.get("created_ts")

                w1n = normalize_workflow(w1)
                w0n = normalize_workflow(w0)

                before = json.dumps(w0n, indent=2, sort_keys=True) + "\n"
                after = json.dumps(w1n, indent=2, sort_keys=True) + "\n"
                diff = unified_diff(before, after, fromfile="before", tofile="after")
                if diff:
                    print(diff, end="")

                change_summary = out.get("change_summary") if isinstance(out.get("change_summary"), list) else []
                conflicts = out.get("conflicts") if isinstance(out.get("conflicts"), list) else []
                notes = str(out.get("notes") or "").strip()
                if change_summary:
                    print("\nchange_summary:")
                    for x in change_summary[:20]:
                        xs = str(x).strip()
                        if xs:
                            print(f"- {xs}")
                if conflicts:
                    print("\nconflicts:")
                    for x in conflicts[:20]:
                        xs = str(x).strip()
                        if xs:
                            print(f"- {xs}")
                if notes:
                    print("\nnotes:\n" + notes)

                if bool(args.dry_run):
                    return 0

                if scope == "global" and project_override:
                    base = normalize_workflow(w_global0)
                    desired = w1n

                    # Compute an override patch relative to the global source of truth.
                    patch: dict[str, Any] = {}
                    if bool(desired.get("enabled", False)) != bool(base.get("enabled", False)):
                        patch["enabled"] = bool(desired.get("enabled", False))
                    name1 = str(desired.get("name") or "").strip()
                    name0 = str(base.get("name") or "").strip()
                    if name1 and name1 != name0:
                        patch["name"] = name1
                    if str(desired.get("mermaid") or "") != str(base.get("mermaid") or ""):
                        patch["mermaid"] = str(desired.get("mermaid") or "")

                    trig0 = base.get("trigger") if isinstance(base.get("trigger"), dict) else {}
                    trig1 = desired.get("trigger") if isinstance(desired.get("trigger"), dict) else {}
                    if trig1 != trig0:
                        patch["trigger"] = trig1

                    steps0 = base.get("steps") if isinstance(base.get("steps"), list) else []
                    steps1 = desired.get("steps") if isinstance(desired.get("steps"), list) else []
                    ids0 = [str(s.get("id") or "") for s in steps0 if isinstance(s, dict) and str(s.get("id") or "").strip()]
                    ids1 = [str(s.get("id") or "") for s in steps1 if isinstance(s, dict) and str(s.get("id") or "").strip()]
                    if ids0 != ids1:
                        patch["steps_replace"] = [s for s in steps1 if isinstance(s, dict)]
                    else:
                        allowed = ("kind", "title", "hands_input", "check_input", "risk_category", "policy", "notes")
                        patches: dict[str, Any] = {}
                        for s0, s1 in zip(steps0, steps1):
                            if not (isinstance(s0, dict) and isinstance(s1, dict)):
                                continue
                            sid = str(s0.get("id") or "").strip()
                            if not sid:
                                continue
                            one: dict[str, Any] = {}
                            for k in allowed:
                                if s1.get(k) != s0.get(k):
                                    one[k] = s1.get(k)
                            if one:
                                patches[sid] = one
                        if patches:
                            patch["step_patches"] = patches

                    overlay2.setdefault("global_workflow_overrides", {})
                    ov = overlay2.get("global_workflow_overrides")
                    if not isinstance(ov, dict):
                        ov = {}
                        overlay2["global_workflow_overrides"] = ov
                    if patch:
                        ov[wid] = patch
                    else:
                        # If there is no diff against global, clear any prior override.
                        if wid in ov:
                            del ov[wid]
                    write_project_overlay(home_dir=home_dir, project_root=project_root, overlay=overlay2)
                    _auto_sync_hosts()
                    return 0

                if scope == "global":
                    wf_global.write(w1n)
                else:
                    wf_store.write(w1n)
                _auto_sync_hosts()
                return 0

            req0 = args.request
            if req0 == "-" or req0 is None:
                req0 = read_user_line("Edit request (blank to cancel):")
            rc = _run_once(str(req0 or ""))
            if rc != 0:
                return rc
            if not bool(args.loop):
                return 0
            while True:
                nxt = read_user_line("Next edit request (blank to stop):")
                if not nxt.strip():
                    return 0
                rc2 = _run_once(nxt)
                if rc2 != 0:
                    return rc2

        return 2


    return None
