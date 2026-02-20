from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .core.config import (
    config_for_display,
    init_config,
    load_config,
    config_path,
    validate_config,
    list_config_templates,
    get_config_template,
    apply_config_template,
    rollback_config,
)
from .runtime.prompts import compile_values_prompt, edit_workflow_prompt, mine_claims_prompt, values_claim_patch_prompt
from .runtime.runner import run_autopilot
from .cli_commands import handle_show, handle_tail, handle_knowledge_workflow_host_commands
from .core.paths import (
    GlobalPaths,
    ProjectPaths,
    resolve_cli_project_root,
    record_last_project_selection,
    set_pinned_project_selection,
    clear_pinned_project_selection,
    set_project_alias,
    remove_project_alias,
    list_project_aliases,
    load_project_selection,
    project_selection_path,
)
from .runtime.inspect import load_last_batch_bundle, tail_json_objects
from .runtime.transcript import last_agent_message_from_transcript
from .core.redact import redact_text
from .providers.provider_factory import make_hands_functions, make_mind_provider
from .runtime.gc import archive_project_transcripts
from .core.storage import append_jsonl, iter_jsonl, now_rfc3339
from .thoughtdb import ThoughtDbStore, claim_signature
from .thoughtdb.compact import compact_thoughtdb_dir
from .workflows import (
    WorkflowStore,
    GlobalWorkflowStore,
    WorkflowRegistry,
    new_workflow_id,
    render_workflow_markdown,
    normalize_workflow,
    apply_global_overrides,
)
from .workflows.hosts import parse_host_bindings, sync_host_binding, sync_hosts_from_overlay
from .memory.ingest import thoughtdb_node_item
from .memory.service import MemoryService
from .runtime.evidence import EvidenceWriter, new_run_id
from .thoughtdb.values import write_values_set_event, existing_values_claims, apply_values_claim_patch
from .thoughtdb.values import (
    VALUES_BASE_TAG,
    VALUES_RAW_TAG,
    VALUES_SUMMARY_TAG,
    upsert_raw_values_claim,
    upsert_values_summary_node,
)
from .thoughtdb.context import build_decide_next_thoughtdb_context
from .thoughtdb.why import (
    find_evidence_event,
    query_from_evidence_event,
    collect_candidate_claims,
    collect_candidate_claims_for_target,
    run_why_trace,
    default_as_of_ts,
)
from .thoughtdb.operational_defaults import (
    ensure_operational_defaults_claims_current,
    resolve_operational_defaults,
    ask_when_uncertain_claim_text,
    refactor_intent_claim_text,
)
from .thoughtdb.graph import build_subgraph_for_id
from .thoughtdb.pins import ASK_WHEN_UNCERTAIN_TAG, REFACTOR_INTENT_TAG
from .project.overlay_store import load_project_overlay, write_project_overlay


def _read_stdin_text() -> str:
    data = sys.stdin.read()
    return data.strip("\n")


def _read_user_line(question: str) -> str:
    print(question.strip(), file=sys.stderr)
    print("> ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def _unified_diff(a: str, b: str, *, fromfile: str, tofile: str, limit_lines: int = 400) -> str:
    diff = list(
        difflib.unified_diff(
            a.splitlines(True),
            b.splitlines(True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )
    if len(diff) > limit_lines:
        diff = diff[:limit_lines] + ["... (diff truncated)\n"]
    return "".join(diff).rstrip() + "\n" if diff else ""


def _effective_cd_arg(args: argparse.Namespace) -> str:
    """Return the effective project selection argument for project-scoped commands.

    Precedence: subcommand --cd (if any) overrides global -C/--cd.
    """

    cd = str(getattr(args, "cd", "") or "").strip()
    if cd:
        return cd
    return str(getattr(args, "global_cd", "") or "").strip()


def _resolve_project_root_from_args(home_dir: Path, cd_arg: str, *, cfg: dict[str, Any] | None = None, here: bool = False) -> Path:
    """Resolve an effective project root for CLI handlers.

    - If `--cd` is omitted, MI may infer git toplevel (see `resolve_cli_project_root`).
    - Print a short stderr note when inference changes the root away from cwd.
    """

    root, reason = resolve_cli_project_root(home_dir, cd_arg, cwd=Path.cwd(), here=bool(here))
    if str(reason or "").startswith("error:alias_missing:"):
        token = str(reason).split("error:alias_missing:", 1)[-1].strip() or str(cd_arg or "").strip()
        print(f"[mi] unknown project token: {token}", file=sys.stderr)
        print("[mi] tip: run `mi project alias list` or set `mi project use --cd <path>` to set @last.", file=sys.stderr)
        raise SystemExit(2)
    cwd = Path.cwd().resolve()
    if not str(reason or "").startswith("arg") and root != cwd:
        print(f"[mi] using inferred project_root={root} (reason={reason}, cwd={cwd})", file=sys.stderr)

    # Auto-update the last-used project (non-canonical convenience) to reduce `--cd` burden.
    runtime_cfg = cfg.get("runtime") if isinstance(cfg, dict) and isinstance(cfg.get("runtime"), dict) else {}
    ps_cfg = runtime_cfg.get("project_selection") if isinstance(runtime_cfg.get("project_selection"), dict) else {}
    auto_update = bool(ps_cfg.get("auto_update_last", True))
    if auto_update:
        try:
            record_last_project_selection(home_dir, root)
        except Exception:
            pass
    return root


def dispatch(*, args: argparse.Namespace, home_dir: Path, cfg: dict[str, Any]) -> int:
    def _make_global_tdb() -> ThoughtDbStore:
        # Use a dummy ProjectPaths id to avoid accidentally creating a project mapping during global operations.
        dummy_pp = ProjectPaths(home_dir=home_dir, project_root=Path("."), _project_id="__global__")
        return ThoughtDbStore(home_dir=home_dir, project_paths=dummy_pp)

    def _do_values_set(
        *,
        values_text: str,
        no_compile: bool,
        no_values_claims: bool,
        show: bool,
        dry_run: bool,
        notes: str,
    ) -> dict[str, Any]:
        """Set canonical values in Thought DB (values event + raw claim; optional derived claims)."""

        values = str(values_text or "")
        if not values.strip():
            return {"ok": False, "error": "values text is empty"}

        llm = None
        compiled: dict[str, Any] | None = None
        compiled_from_model = False

        if not no_compile:
            # Run compile in an isolated directory to avoid accidental project context bleed.
            scratch = home_dir / "tmp" / "compile_values"
            scratch.mkdir(parents=True, exist_ok=True)
            gp = GlobalPaths(home_dir=home_dir)
            transcripts_dir = gp.global_dir / "transcripts"

            llm = make_mind_provider(cfg, project_root=scratch, transcripts_dir=transcripts_dir)
            prompt = compile_values_prompt(values_text=values)
            try:
                out = llm.call(schema_filename="compile_values.json", prompt=prompt, tag="compile_values").obj
                compiled = out if isinstance(out, dict) else None
                compiled_from_model = bool(compiled)
            except Exception as e:
                compiled = None
                print(f"compile_values failed; falling back. error={e}", file=sys.stderr)

        compiled_values = compiled if isinstance(compiled, dict) else {}

        if show or dry_run:
            vs = compiled_values.get("values_summary") or []
            if isinstance(vs, list) and any(str(x).strip() for x in vs):
                print("values_summary:")
                for x in vs:
                    xs = str(x).strip()
                    if xs:
                        print(f"- {xs}")
            dp = compiled_values.get("decision_procedure") or {}
            if isinstance(dp, dict):
                summary = str(dp.get("summary") or "").strip()
                mermaid = str(dp.get("mermaid") or "").strip()
                if summary:
                    print("\ndecision_procedure.summary:\n" + summary)
                if mermaid:
                    print("\ndecision_procedure.mermaid:\n" + mermaid)

        if dry_run:
            return {"ok": True, "dry_run": True, "compiled": compiled_values, "compiled_from_model": compiled_from_model}

        # Record values changes into a global EvidenceLog so Claims can cite stable event_id provenance.
        values_ev = write_values_set_event(
            home_dir=home_dir,
            values_text=values,
            compiled_values=compiled_values,
            notes=str(notes or "").strip(),
        )
        values_event_id = str(values_ev.get("event_id") or "").strip()
        if not values_event_id:
            return {"ok": False, "error": "failed to record global values_set event_id"}

        tdb = _make_global_tdb()
        raw_id = upsert_raw_values_claim(
            tdb=tdb,
            values_text=values,
            values_event_id=values_event_id,
            visibility="global",
            notes="values_text (raw)",
        )
        summary_id = ""
        if compiled_from_model:
            summary_id = upsert_values_summary_node(tdb=tdb, compiled_values=compiled_values, values_event_id=values_event_id)
        try:
            defaults_seed = ensure_operational_defaults_claims_current(
                home_dir=home_dir,
                tdb=tdb,
                desired_defaults=None,
                mode="seed_missing",
                event_notes=str(notes or "").strip() or "values_set",
                claim_notes_prefix="seed_on_values_set",
            )
        except Exception as e:
            defaults_seed = {"ok": False, "changed": False, "mode": "seed_missing", "event_id": "", "error": f"{type(e).__name__}: {e}"}

        # Derive values into canonical global preference/goal claims (best-effort).
        values_claims_applied: dict[str, Any] = {}
        values_claims_retracted: list[str] = []
        if no_compile:
            return {
                "ok": True,
                "values_event_id": values_event_id,
                "raw_claim_id": raw_id,
                "summary_node_id": summary_id,
                "defaults_seed": defaults_seed,
                "values_claims": {"skipped": "--no-compile"},
            }
        if no_values_claims:
            return {
                "ok": True,
                "values_event_id": values_event_id,
                "raw_claim_id": raw_id,
                "summary_node_id": summary_id,
                "defaults_seed": defaults_seed,
                "values_claims": {"skipped": "--no-values-claims"},
            }
        if llm is None:
            return {
                "ok": True,
                "values_event_id": values_event_id,
                "raw_claim_id": raw_id,
                "summary_node_id": summary_id,
                "defaults_seed": defaults_seed,
                "values_claims": {"skipped": "mind provider unavailable"},
            }

        try:
            existing = existing_values_claims(tdb=tdb, limit=120)
            retractable_ids = [
                str(c.get("claim_id") or "").strip()
                for c in existing
                if isinstance(c, dict) and str(c.get("claim_id") or "").strip()
            ]

            prompt2 = values_claim_patch_prompt(
                values_text=values,
                compiled_values=compiled_values,
                existing_values_claims=existing,
                allowed_event_ids=[values_event_id],
                allowed_retract_claim_ids=retractable_ids,
                notes=str(notes or "").strip() or "values -> Thought DB claims",
            )
            patch_obj = llm.call(schema_filename="values_claim_patch.json", prompt=prompt2, tag="values_claim_patch").obj

            runtime_cfg = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
            tcfg = runtime_cfg.get("thought_db") if isinstance(runtime_cfg.get("thought_db"), dict) else {}
            try:
                min_conf = float(tcfg.get("min_confidence", 0.9) or 0.9)
            except Exception:
                min_conf = 0.9
            try:
                base_max = int(tcfg.get("max_claims_per_checkpoint", 6) or 6)
            except Exception:
                base_max = 6
            max_claims = max(8, min(20, base_max * 2))

            applied = apply_values_claim_patch(
                tdb=tdb,
                patch_obj=patch_obj if isinstance(patch_obj, dict) else {},
                values_event_id=values_event_id,
                min_confidence=min_conf,
                max_claims=max_claims,
            )
            if applied.ok:
                values_claims_applied = applied.applied if isinstance(applied.applied, dict) else {}
                values_claims_retracted = list(applied.retracted or [])
        except Exception as e:
            return {
                "ok": True,
                "values_event_id": values_event_id,
                "raw_claim_id": raw_id,
                "summary_node_id": summary_id,
                "defaults_seed": defaults_seed,
                "values_claims": {"error": f"{type(e).__name__}: {e}"},
            }

        return {
            "ok": True,
            "values_event_id": values_event_id,
            "raw_claim_id": raw_id,
            "summary_node_id": summary_id,
            "defaults_seed": defaults_seed,
            "values_claims": {"applied": values_claims_applied, "retracted": values_claims_retracted},
        }

    if args.cmd == "version":
        print(__version__)
        return 0

    if args.cmd == "status":
        # Read-only: resolve project root without updating @last.
        cd_arg = _effective_cd_arg(args)
        cwd = Path.cwd().resolve()
        root, reason = resolve_cli_project_root(home_dir, cd_arg, cwd=cwd, here=bool(getattr(args, "here", False)))
        if str(reason or "").startswith("error:alias_missing:"):
            token = str(reason).split("error:alias_missing:", 1)[-1].strip() or str(cd_arg or "").strip()
            print(f"[mi] unknown project token: {token}", file=sys.stderr)
            print("[mi] tip: run `mi project alias list` or set `mi project use --cd <path>` to set @last.", file=sys.stderr)
            return 2

        # Config/provider health.
        vcfg = validate_config(cfg)
        hands_cfg = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
        mind_cfg = cfg.get("mind") if isinstance(cfg.get("mind"), dict) else {}
        hands_provider = str(hands_cfg.get("provider") or "codex").strip()
        mind_provider = str(mind_cfg.get("provider") or "codex_schema").strip()

        # Values readiness (canonical: Thought DB global).
        tdb_g = _make_global_tdb()
        vals = existing_values_claims(tdb=tdb_g, limit=3)
        values_base_present = bool(vals)

        # Best-effort: detect presence of a values summary node.
        values_summary_present = False
        try:
            v_glob = tdb_g.load_view(scope="global")
            for n in v_glob.iter_nodes(include_inactive=False, include_aliases=False):
                if not isinstance(n, dict):
                    continue
                tags = n.get("tags") if isinstance(n.get("tags"), list) else []
                if VALUES_SUMMARY_TAG in {str(x).strip() for x in tags if str(x).strip()}:
                    values_summary_present = True
                    break
        except Exception:
            values_summary_present = False

        # Project-scoped state (best-effort).
        pp = ProjectPaths(home_dir=home_dir, project_root=root)
        overlay = load_project_overlay(home_dir=home_dir, project_root=root)
        if not isinstance(overlay, dict):
            overlay = {}
        bindings = parse_host_bindings(overlay)

        bundle = load_last_batch_bundle(pp.evidence_log_path)
        hands_input = bundle.get("hands_input") if isinstance(bundle.get("hands_input"), dict) else None
        evidence_item = bundle.get("evidence_item") if isinstance(bundle.get("evidence_item"), dict) else None
        decide_next = bundle.get("decide_next") if isinstance(bundle.get("decide_next"), dict) else None

        transcript_path = ""
        if hands_input and isinstance(hands_input.get("transcript_path"), str):
            transcript_path = hands_input["transcript_path"]
        elif evidence_item and isinstance(evidence_item.get("hands_transcript_ref"), str):
            transcript_path = evidence_item["hands_transcript_ref"]
        last_msg = last_agent_message_from_transcript(Path(transcript_path)) if transcript_path else ""

        # Pending learn suggestions (reduce user burden): show only items that require manual apply.
        pending_suggestions: list[str] = []
        for rec in (bundle.get("learn_suggested") or []) if isinstance(bundle.get("learn_suggested"), list) else []:
            if not isinstance(rec, dict):
                continue
            sid = str(rec.get("id") or "").strip()
            auto = bool(rec.get("auto_learn", True))
            applied_ids = rec.get("applied_claim_ids") if isinstance(rec.get("applied_claim_ids"), list) else []
            if sid and (not auto) and (not applied_ids):
                pending_suggestions.append(sid)
        pending_suggestions = pending_suggestions[:6]

        # Best-effort: detect the latest host_sync record and surface failures.
        host_sync_recent: dict[str, Any] | None = None
        try:
            for obj in reversed(tail_json_objects(pp.evidence_log_path, 200)):
                if isinstance(obj, dict) and str(obj.get("kind") or "").strip() == "host_sync":
                    host_sync_recent = obj
                    break
        except Exception:
            host_sync_recent = None
        host_sync_ok = True
        if isinstance(host_sync_recent, dict):
            sync_obj = host_sync_recent.get("sync") if isinstance(host_sync_recent.get("sync"), dict) else {}
            host_sync_ok = bool(sync_obj.get("ok", True)) if isinstance(sync_obj, dict) else True

        # Deterministic next-step suggestions (no model calls).
        next_steps: list[str] = []
        if not bool(vcfg.get("ok", False)):
            next_steps.append("mi config validate")
        if not values_base_present:
            next_steps.append('mi values set --text "..."')
        if pending_suggestions:
            next_steps.append(f"mi claim apply-suggested {pending_suggestions[0]} --dry-run")
        st = str(decide_next.get("status") or "") if isinstance(decide_next, dict) else ""
        na = str(decide_next.get("next_action") or "") if isinstance(decide_next, dict) else ""
        if st in ("blocked", "not_done") or na in ("ask_user", "continue", "run_checks"):
            next_steps.append("mi show last --redact")
        if bindings and (not host_sync_ok):
            next_steps.append("mi host sync --json")
        if not next_steps:
            next_steps.append('mi run "..."')
        next_steps = next_steps[:3]

        payload = {
            "cwd": str(cwd),
            "effective_cd": str(cd_arg or ""),
            "here": bool(getattr(args, "here", False)),
            "project_root": str(root),
            "reason": str(reason or ""),
            "config_validate": vcfg,
            "providers": {"hands": hands_provider, "mind": mind_provider},
            "values": {
                "values_base_present": values_base_present,
                "values_summary_present": values_summary_present,
                "values_base_claims_sample": vals,
            },
            "project": {
                "project_id": pp.project_id,
                "project_dir": str(pp.project_dir),
                "evidence_log": str(pp.evidence_log_path),
                "transcripts_dir": str(pp.transcripts_dir),
                "host_bindings": [b.host for b in bindings] if bindings else [],
            },
            "last": {
                "bundle": bundle,
                "hands_last_message": last_msg,
            },
            "pending": {
                "learn_suggested": pending_suggestions,
                "host_sync_ok": host_sync_ok,
                "host_sync_recent": host_sync_recent or {},
            },
            "next_steps": next_steps,
        }

        if bool(getattr(args, "redact", False)):
            if last_msg:
                last_msg = redact_text(last_msg)
            s = json.dumps(payload, indent=2, sort_keys=True)
            payload_s = redact_text(s)
            if bool(getattr(args, "json", False)):
                print(payload_s)
                return 0
            # Keep text rendering compact in redacted mode too.
            payload2 = json.loads(payload_s)
            payload = payload2 if isinstance(payload2, dict) else payload

        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        print(f"project_root={payload['project_root']} (reason={payload['reason']})")
        print(f"providers: hands={hands_provider} mind={mind_provider}")
        errs = vcfg.get("errors") if isinstance(vcfg.get("errors"), list) else []
        warns = vcfg.get("warnings") if isinstance(vcfg.get("warnings"), list) else []
        print(f"config_ok={str(bool(vcfg.get('ok', False))).lower()} errors={len(errs)} warnings={len(warns)}")
        print(f"values_base_present={str(values_base_present).lower()} values_summary_present={str(values_summary_present).lower()}")
        if last_msg.strip():
            print("")
            print("hands_last_message:")
            print(last_msg.strip())
        if pending_suggestions:
            print("")
            print("pending_learn_suggested:")
            for sid in pending_suggestions[:3]:
                print(f"- {sid}")
        if bindings:
            print("")
            print(f"host_bindings={len(bindings)} host_sync_ok={str(bool(host_sync_ok)).lower()}")
        if next_steps:
            print("")
            print("next_steps:")
            for cmd in next_steps:
                print(f"- {cmd}")
        return 0

    if args.cmd == "show":
        return handle_show(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            resolve_project_root_from_args=_resolve_project_root_from_args,
            effective_cd_arg=_effective_cd_arg,
            dispatch_fn=lambda args2, home2, cfg2: dispatch(args=args2, home_dir=home2, cfg=cfg2),
        )

    if args.cmd == "tail":
        return handle_tail(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            resolve_project_root_from_args=_resolve_project_root_from_args,
            effective_cd_arg=_effective_cd_arg,
        )

    if args.cmd == "config":
        if args.config_cmd == "path":
            print(str(config_path(home_dir)))
            return 0
        if args.config_cmd == "init":
            path = init_config(home_dir, force=bool(args.force))
            print(f"Wrote config to {path}")
            return 0
        if args.config_cmd == "show":
            disp = config_for_display(cfg)
            print(json.dumps(disp, indent=2, sort_keys=True))
            return 0
        if args.config_cmd == "examples":
            for name in list_config_templates():
                print(name)
            return 0
        if args.config_cmd == "template":
            try:
                tmpl = get_config_template(str(args.name))
            except Exception as e:
                print(f"unknown template: {args.name}", file=sys.stderr)
                print("available:", file=sys.stderr)
                for name in list_config_templates():
                    print(f"- {name}", file=sys.stderr)
                return 2
            print(json.dumps(tmpl, indent=2, sort_keys=True))
            return 0
        if args.config_cmd == "apply-template":
            try:
                res = apply_config_template(home_dir, name=str(args.name))
            except Exception as e:
                print(f"apply-template failed: {e}", file=sys.stderr)
                return 2
            print(f"Applied template: {args.name}")
            print(f"Backup: {res.get('backup_path')}")
            print(f"Config: {res.get('config_path')}")
            return 0
        if args.config_cmd == "rollback":
            try:
                res = rollback_config(home_dir)
            except Exception as e:
                print(f"rollback failed: {e}", file=sys.stderr)
                return 2
            print(f"Rolled back config to: {res.get('backup_path')}")
            print(f"Config: {res.get('config_path')}")
            return 0
        if args.config_cmd == "validate":
            report = validate_config(cfg)
            ok = bool(report.get("ok", False))
            errs = report.get("errors") if isinstance(report.get("errors"), list) else []
            warns = report.get("warnings") if isinstance(report.get("warnings"), list) else []
            if ok and not warns:
                print("ok")
                return 0
            if errs:
                print("errors:")
                for e in errs:
                    es = str(e).strip()
                    if es:
                        print(f"- {es}")
            if warns:
                print("warnings:")
                for w in warns:
                    ws = str(w).strip()
                    if ws:
                        print(f"- {ws}")
            return 0 if ok else 1
        return 2

    if args.cmd == "init":
        values = str(args.values or "")
        if values == "-":
            values = _read_stdin_text()
        if not values.strip():
            print("Values text is empty. Provide --values or pipe text to stdin.", file=sys.stderr)
            return 2

        out = _do_values_set(
            values_text=values,
            no_compile=bool(args.no_compile),
            no_values_claims=bool(args.no_values_claims),
            show=bool(args.show),
            dry_run=bool(args.dry_run),
            notes="mi init",
        )
        if not bool(out.get("ok", False)):
            print(f"init failed: {out.get('error')}", file=sys.stderr)
            return 2
        if bool(out.get("dry_run", False)):
            print("(dry-run) did not write Thought DB.")
            return 0
        ve = str(out.get("values_event_id") or "").strip()
        if ve:
            print(f"[mi] recorded global values_set event_id={ve}", file=sys.stderr)
        raw_id = str(out.get("raw_claim_id") or "").strip()
        if raw_id:
            print(f"[mi] raw values claim_id={raw_id} (tag={VALUES_RAW_TAG})", file=sys.stderr)
        sum_id = str(out.get("summary_node_id") or "").strip()
        if sum_id:
            print(f"[mi] values summary node_id={sum_id} (tag={VALUES_SUMMARY_TAG})", file=sys.stderr)

        vc = out.get("values_claims") if isinstance(out.get("values_claims"), dict) else {}
        if isinstance(vc, dict) and vc.get("skipped"):
            print(f"[mi] values->claims skipped: {vc.get('skipped')}", file=sys.stderr)
        elif isinstance(vc, dict) and isinstance(vc.get("applied"), dict):
            a = vc.get("applied") if isinstance(vc.get("applied"), dict) else {}
            written = a.get("written") if isinstance(a.get("written"), list) else []
            linked = a.get("linked_existing") if isinstance(a.get("linked_existing"), list) else []
            edges = a.get("written_edges") if isinstance(a.get("written_edges"), list) else []
            retracted = vc.get("retracted") if isinstance(vc.get("retracted"), list) else []
            print(
                f"[mi] values->claims ok: written={len(written)} linked_existing={len(linked)} edges={len(edges)} retracted={len(retracted)}",
                file=sys.stderr,
            )
        elif isinstance(vc, dict) and vc.get("error"):
            print(f"[mi] values->claims error: {vc.get('error')}", file=sys.stderr)

        return 0

    if args.cmd == "values":
        if args.values_cmd == "set":
            text = str(getattr(args, "text", "-") or "-")
            if text == "-":
                text = _read_stdin_text()
            if not text.strip():
                print("Values text is empty. Provide --text or pipe text to stdin.", file=sys.stderr)
                return 2
            out = _do_values_set(
                values_text=text,
                no_compile=bool(getattr(args, "no_compile", False)),
                no_values_claims=bool(getattr(args, "no_values_claims", False)),
                show=bool(getattr(args, "show", False)),
                dry_run=False,
                notes="mi values set",
            )
            if not bool(out.get("ok", False)):
                print(f"values set failed: {out.get('error')}", file=sys.stderr)
                return 2
            ve = str(out.get("values_event_id") or "").strip()
            if ve:
                print(f"values_event_id={ve}")
            raw_id = str(out.get("raw_claim_id") or "").strip()
            if raw_id:
                print(f"raw_claim_id={raw_id}")
            sum_id = str(out.get("summary_node_id") or "").strip()
            if sum_id:
                print(f"summary_node_id={sum_id}")
            return 0

        if args.values_cmd == "show":
            tdb = _make_global_tdb()
            v = tdb.load_view(scope="global")

            raw: dict[str, Any] | None = None
            raw_ts = ""
            for c in v.iter_claims(include_inactive=False, include_aliases=False):
                if not isinstance(c, dict):
                    continue
                tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                tagset = {str(x).strip() for x in tags if str(x).strip()}
                if VALUES_RAW_TAG not in tagset:
                    continue
                ts = str(c.get("asserted_ts") or "").strip()
                if ts >= raw_ts:
                    raw = c
                    raw_ts = ts

            summary: dict[str, Any] | None = None
            sum_ts = ""
            for n in v.iter_nodes(include_inactive=False, include_aliases=False):
                if not isinstance(n, dict):
                    continue
                tags = n.get("tags") if isinstance(n.get("tags"), list) else []
                tagset = {str(x).strip() for x in tags if str(x).strip()}
                if VALUES_SUMMARY_TAG not in tagset:
                    continue
                ts = str(n.get("asserted_ts") or "").strip()
                if ts >= sum_ts:
                    summary = n
                    sum_ts = ts

            derived = existing_values_claims(tdb=tdb, limit=80)
            out = {
                "raw_values_claim": raw or {},
                "values_summary_node": summary or {},
                "derived_values_claims": derived,
            }
            if bool(getattr(args, "json", False)):
                print(json.dumps(out, indent=2, sort_keys=True))
                return 0

            if raw:
                print(f"raw_values_claim_id={raw.get('claim_id')}")
                txt = str(raw.get("text") or "").strip()
                if txt:
                    print("\nvalues_text:\n" + txt)
            if summary:
                print(f"\nvalues_summary_node_id={summary.get('node_id')}")
                st = str(summary.get("text") or "").strip()
                if st:
                    print("\nsummary:\n" + st)
            if derived:
                print("\nderived_values_claims:")
                for c in derived[:24]:
                    if not isinstance(c, dict):
                        continue
                    cid = str(c.get("claim_id") or "").strip()
                    text = str(c.get("text") or "").strip()
                    ct = str(c.get("claim_type") or "").strip()
                    if text:
                        print(f"- [{ct or 'claim'}] {text} ({cid})" if cid else f"- [{ct or 'claim'}] {text}")
            return 0

    if args.cmd == "settings":
        if args.settings_cmd == "show":
            cd = _effective_cd_arg(args)
            if cd:
                project_root = _resolve_project_root_from_args(home_dir, cd, cfg=cfg, here=bool(getattr(args, "here", False)))
                pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
            else:
                pp = ProjectPaths(home_dir=home_dir, project_root=Path("."), _project_id="__global__")
            tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)
            op = resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339())
            out = op.to_dict()
            if bool(getattr(args, "json", False)):
                print(json.dumps(out, indent=2, sort_keys=True))
                return 0
            print(f"ask_when_uncertain={out.get('ask_when_uncertain')}")
            print(f"ask_when_uncertain_source={out.get('ask_when_uncertain_source')}")
            print(f"refactor_intent={out.get('refactor_intent')}")
            print(f"refactor_intent_source={out.get('refactor_intent_source')}")
            return 0

        if args.settings_cmd == "set":
            scope = str(getattr(args, "scope", "global") or "global").strip()
            ask_s = str(getattr(args, "ask_when_uncertain", "") or "").strip()
            ref_s = str(getattr(args, "refactor_intent", "") or "").strip()
            if not ask_s and not ref_s:
                print("nothing to set: provide --ask-when-uncertain and/or --refactor-intent", file=sys.stderr)
                return 2

            if scope == "global":
                tdb = _make_global_tdb()
                cur = resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339())
                desired_ask = cur.ask_when_uncertain
                desired_ref = cur.refactor_intent or "behavior_preserving"
                if ask_s:
                    desired_ask = True if ask_s == "ask" else False
                if ref_s:
                    desired_ref = ref_s

                desired = {"ask_when_uncertain": desired_ask, "refactor_intent": desired_ref}
                if bool(getattr(args, "dry_run", False)):
                    print(json.dumps({"scope": "global", "desired": desired}, indent=2, sort_keys=True))
                    return 0

                res = ensure_operational_defaults_claims_current(
                    home_dir=home_dir,
                    tdb=tdb,
                    desired_defaults=desired,
                    mode="sync",
                    event_notes="mi settings set",
                    claim_notes_prefix="user_set",
                )
                print(json.dumps(res, indent=2, sort_keys=True))
                return 0

            # Project-scoped overrides: write setting claims into the project store (append-only).
            project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
            pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)
            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "settings_set",
                    "batch_id": "cli.settings_set",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": "project",
                    "project_id": pp.project_id,
                    "ask_when_uncertain": ask_s,
                    "refactor_intent": ref_s,
                }
            )
            ev_id = str(ev.get("event_id") or "").strip()

            v = tdb.load_view(scope="project")
            sig_map = tdb.existing_signature_map(scope="project")

            def _find_latest_tagged(tag: str) -> dict[str, Any] | None:
                best: dict[str, Any] | None = None
                best_ts = ""
                for c in v.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=now_rfc3339()):
                    if not isinstance(c, dict):
                        continue
                    ct = str(c.get("claim_type") or "").strip()
                    if ct not in ("preference", "goal"):
                        continue
                    tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                    tagset = {str(x).strip() for x in tags if str(x).strip()}
                    if tag not in tagset:
                        continue
                    ts = str(c.get("asserted_ts") or "").strip()
                    if ts >= best_ts:
                        best = c
                        best_ts = ts
                return best

            changes: list[dict[str, str]] = []
            for tag, text in (
                ("ask", ask_when_uncertain_claim_text(True) if ask_s == "ask" else ask_when_uncertain_claim_text(False) if ask_s else ""),
                ("ref", refactor_intent_claim_text(ref_s) if ref_s else ""),
            ):
                if not text:
                    continue
                tag_id = ASK_WHEN_UNCERTAIN_TAG if tag == "ask" else REFACTOR_INTENT_TAG
                existing = _find_latest_tagged(tag_id)
                existing_id = str(existing.get("claim_id") or "").strip() if isinstance(existing, dict) else ""
                existing_text = str(existing.get("text") or "").strip() if isinstance(existing, dict) else ""
                if existing_id and existing_text == text:
                    continue

                sig = claim_signature(claim_type="preference", scope="project", project_id=pp.project_id, text=text)
                reuse_id = ""
                if sig and sig in sig_map:
                    cand = v.claims_by_id.get(str(sig_map[sig]) or "")
                    cand_tags = cand.get("tags") if isinstance(cand, dict) and isinstance(cand.get("tags"), list) else []
                    cand_tagset = {str(x).strip() for x in cand_tags if str(x).strip()}
                    if tag_id in cand_tagset:
                        reuse_id = str(sig_map[sig])

                if reuse_id:
                    new_id = reuse_id
                else:
                    new_id = tdb.append_claim_create(
                        claim_type="preference",
                        text=text,
                        scope="project",
                        visibility="project",
                        valid_from=None,
                        valid_to=None,
                        tags=[tag_id, "mi:setting", "mi:defaults", "mi:source:cli.settings_set"],
                        source_event_ids=[ev_id] if ev_id else [],
                        confidence=1.0,
                        notes="user_set project override",
                    )

                if existing_id and ev_id:
                    try:
                        tdb.append_edge(
                            edge_type="supersedes",
                            from_id=existing_id,
                            to_id=new_id,
                            scope="project",
                            visibility="project",
                            source_event_ids=[ev_id],
                            notes="project settings override update",
                        )
                    except Exception:
                        pass
                changes.append({"tag": tag_id, "claim_id": new_id})

            print(json.dumps({"ok": True, "scope": "project", "project_id": pp.project_id, "changes": changes}, indent=2, sort_keys=True))
            return 0

    if args.cmd == "run":
        quiet = bool(getattr(args, "quiet", False))
        live = not quiet
        hands_raw = bool(getattr(args, "hands_raw", False))
        no_mi_prompt = bool(getattr(args, "no_mi_prompt", False))
        run_redact = bool(getattr(args, "redact", False))

        task_obj = getattr(args, "task", "")
        if isinstance(task_obj, list):
            task = " ".join(str(x) for x in task_obj).strip()
        else:
            task = str(task_obj or "").strip()

        hands_exec, hands_resume = make_hands_functions(cfg, live=live, hands_raw=hands_raw, redact=run_redact)
        project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        project_paths = ProjectPaths(home_dir=home_dir, project_root=project_root)
        llm = make_mind_provider(cfg, project_root=project_root, transcripts_dir=project_paths.transcripts_dir)
        hands_provider = ""
        hands_cfg = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
        if isinstance(hands_cfg, dict):
            hands_provider = str(hands_cfg.get("provider") or "").strip()
        continue_default = bool(hands_cfg.get("continue_across_runs", False)) if isinstance(hands_cfg, dict) else False
        continue_hands = bool(args.continue_hands or continue_default)
        result = run_autopilot(
            task=task,
            project_root=str(project_root),
            home_dir=str(home_dir),
            max_batches=args.max_batches,
            hands_exec=hands_exec,
            hands_resume=hands_resume,
            llm=llm,
            hands_provider=hands_provider,
            continue_hands=continue_hands,
            reset_hands=bool(args.reset_hands),
            why_trace_on_run_end=bool(getattr(args, "why", False)),
            live=live,
            quiet=quiet,
            no_mi_prompt=no_mi_prompt,
            redact=run_redact,
        )
        # Always print an end summary unless suppressed.
        if not quiet:
            print(result.render_text())
        return 0 if result.status == "done" else 1

    if args.cmd == "memory":
        if args.mem_cmd == "index":
            mem = MemoryService(home_dir)
            if args.mi_cmd == "status":
                st = mem.status()
                if args.json:
                    print(json.dumps(st, indent=2, sort_keys=True))
                    return 0
                backend = str(st.get("backend") or "?").strip() or "?"
                exists = bool(st.get("exists", True))
                if not exists:
                    db_path = str(st.get("db_path") or "").strip()
                    extra = f" {db_path}" if db_path else ""
                    print(f"memory backend: {backend} (missing){extra}")
                    return 0

                print(f"memory backend: {backend}")
                if str(st.get("db_path") or "").strip():
                    print(f"db_path: {st.get('db_path')}")
                if str(st.get("fts_version") or "").strip():
                    print(f"fts_version: {st.get('fts_version')}")
                print(f"total_items: {st.get('total_items')}")
                groups = st.get("groups") if isinstance(st.get("groups"), list) else []
                if groups:
                    print("groups:")
                    for g in groups:
                        if not isinstance(g, dict):
                            continue
                        proj = str(g.get("project_id") or "").strip() or "global"
                        kind = str(g.get("kind") or "").strip() or "?"
                        scope = str(g.get("scope") or "").strip() or "?"
                        try:
                            n = int(g.get("count") or 0)
                        except Exception:
                            n = 0
                        print(f"- {kind}/{scope}/{proj}: {n}")
                return 0

            if args.mi_cmd == "rebuild":
                res = mem.rebuild(include_snapshots=not bool(args.no_snapshots))
                if args.json:
                    print(json.dumps(res, indent=2, sort_keys=True))
                    return 0
                print(f"rebuilt: {bool(res.get('rebuilt', False))}")
                backend = str(res.get("backend") or "?").strip() or "?"
                print(f"backend: {backend}")
                if str(res.get("db_path") or "").strip():
                    print(f"db_path: {res.get('db_path')}")
                if str(res.get("fts_version") or "").strip():
                    print(f"fts_version: {res.get('fts_version')}")
                print(f"total_items: {res.get('total_items')}")
                if "indexed_snapshots" in res:
                    print(f"indexed_snapshots: {res.get('indexed_snapshots')}")
                return 0

    if args.cmd == "project":
        subcmd = str(getattr(args, "project_cmd", "") or "").strip()

        if subcmd == "status":
            cd_arg = _effective_cd_arg(args)
            cwd = Path.cwd().resolve()
            root, reason = resolve_cli_project_root(home_dir, cd_arg, cwd=cwd, here=bool(getattr(args, "here", False)))
            if str(reason or "").startswith("error:alias_missing:"):
                token = str(reason).split("error:alias_missing:", 1)[-1].strip() or str(cd_arg or "").strip()
                print(f"[mi] unknown project token: {token}", file=sys.stderr)
                print("[mi] tip: run `mi project alias list` or set `mi project use --cd <path>` to set @last.", file=sys.stderr)
                return 2

            sel = load_project_selection(home_dir)
            payload = {
                "cwd": str(cwd),
                "effective_cd": str(cd_arg or ""),
                "here": bool(getattr(args, "here", False)),
                "project_root": str(root),
                "reason": str(reason or ""),
                "selection_path": str(project_selection_path(home_dir)),
                "selection": sel if isinstance(sel, dict) else {},
            }
            if bool(getattr(args, "json", False)):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0

            print(f"project_root={payload['project_root']}")
            print(f"reason={payload['reason']}")
            print(f"cwd={payload['cwd']}")
            if payload["effective_cd"]:
                print(f"cd_arg={payload['effective_cd']}")
            print(f"selection_path={payload['selection_path']}")

            last = sel.get("last") if isinstance(sel, dict) else {}
            pinned = sel.get("pinned") if isinstance(sel, dict) else {}
            aliases = sel.get("aliases") if isinstance(sel, dict) else {}
            last_rp = str(last.get("root_path") or "").strip() if isinstance(last, dict) else ""
            pinned_rp = str(pinned.get("root_path") or "").strip() if isinstance(pinned, dict) else ""
            if pinned_rp:
                print(f"@pinned={pinned_rp}")
            if last_rp:
                print(f"@last={last_rp}")
            if isinstance(aliases, dict) and aliases:
                print(f"aliases={len(aliases)}")
            return 0

        if subcmd == "alias":
            alias_cmd = str(getattr(args, "alias_cmd", "") or "").strip()
            if alias_cmd == "list":
                aliases = list_project_aliases(home_dir)
                payload = {
                    "selection_path": str(project_selection_path(home_dir)),
                    "aliases": aliases,
                }
                if bool(getattr(args, "json", False)):
                    print(json.dumps(payload, indent=2, sort_keys=True))
                    return 0
                if not aliases:
                    print("(no aliases)")
                    return 0
                for name in sorted(aliases.keys()):
                    entry = aliases.get(name) if isinstance(aliases.get(name), dict) else {}
                    rp = str(entry.get("root_path") or "").strip()
                    pid = str(entry.get("project_id") or "").strip()
                    print(f"- @{name}: {rp} (project_id={pid})" if pid else f"- @{name}: {rp}")
                return 0

            if alias_cmd == "add":
                project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
                name = str(getattr(args, "name", "") or "").strip()
                try:
                    entry = set_project_alias(home_dir, name=name, project_root=project_root)
                except Exception as e:
                    print(f"alias add failed: {e}", file=sys.stderr)
                    return 2
                payload = {"ok": True, "name": name, "entry": entry}
                if bool(getattr(args, "json", False)):
                    print(json.dumps(payload, indent=2, sort_keys=True))
                    return 0
                print(f"added @{name} -> {entry.get('root_path')}")
                return 0

            if alias_cmd == "rm":
                name = str(getattr(args, "name", "") or "").strip()
                ok = False
                try:
                    ok = remove_project_alias(home_dir, name=name)
                except Exception:
                    ok = False
                payload = {"ok": bool(ok), "name": name}
                if bool(getattr(args, "json", False)):
                    print(json.dumps(payload, indent=2, sort_keys=True))
                    return 0 if ok else 2
                if ok:
                    print(f"removed @{name}")
                    return 0
                print(f"alias not found: @{name}", file=sys.stderr)
                return 2

            print("unknown project alias subcommand", file=sys.stderr)
            return 2

        if subcmd == "unpin":
            clear_pinned_project_selection(home_dir)
            payload = {"ok": True, "pinned": {}}
            if bool(getattr(args, "json", False)):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            print("cleared @pinned")
            return 0

        if subcmd == "pin":
            project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
            entry = set_pinned_project_selection(home_dir, project_root)
            payload = {"ok": True, "pinned": entry}
            if bool(getattr(args, "json", False)):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            print(f"pinned @pinned -> {entry.get('root_path')}")
            return 0

        if subcmd == "use":
            project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
            entry = record_last_project_selection(home_dir, project_root)
            payload = {"ok": True, "last": entry}
            if bool(getattr(args, "json", False)):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            print(f"set @last -> {entry.get('root_path')}")
            return 0

        if subcmd == "show":
            project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
            pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
            overlay = load_project_overlay(home_dir=home_dir, project_root=project_root)

            identity_key = str(overlay.get("identity_key") or "").strip()

            out = {
                "project_root": str(project_root),
                "project_id": pp.project_id,
                "project_dir": str(pp.project_dir),
                "overlay_path": str(pp.overlay_path),
                "identity_key": identity_key,
                "evidence_log": str(pp.evidence_log_path),
                "transcripts_dir": str(pp.transcripts_dir),
                "thoughtdb_dir": str(pp.thoughtdb_dir),
                "thoughtdb_claims": str(pp.thoughtdb_claims_path),
                "thoughtdb_edges": str(pp.thoughtdb_edges_path),
                "overlay": overlay if isinstance(overlay, dict) else {},
            }

            if args.redact:
                # Redact all string leaf values for display (keeps JSON valid).
                def _redact_any(x: object) -> object:
                    if isinstance(x, str):
                        return redact_text(x)
                    if isinstance(x, list):
                        return [_redact_any(v) for v in x]
                    if isinstance(x, dict):
                        return {k: _redact_any(v) for k, v in x.items()}
                    return x

                out = _redact_any(out)  # type: ignore[assignment]

            if args.json:
                print(json.dumps(out, indent=2, sort_keys=True))
                return 0

            print(f"project_id={out['project_id']}")
            print(f"project_dir={out['project_dir']}")
            print(f"overlay_path={out['overlay_path']}")
            if identity_key:
                print(f"identity_key={identity_key}")
            print(f"evidence_log={out['evidence_log']}")
            print(f"transcripts_dir={out['transcripts_dir']}")
            print(f"thoughtdb_dir={out['thoughtdb_dir']}")
            return 0

        print("unknown project subcommand", file=sys.stderr)
        return 2

    if args.cmd == "gc":
        if args.gc_cmd == "transcripts":
            project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
            pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
            res = archive_project_transcripts(
                transcripts_dir=pp.transcripts_dir,
                keep_hands=int(args.keep_hands),
                keep_mind=int(args.keep_mind),
                dry_run=not bool(args.apply),
            )
            if args.json:
                print(json.dumps(res, indent=2, sort_keys=True))
                return 0

            mode = "dry-run" if res.get("dry_run") else "applied"
            hands = res.get("hands") if isinstance(res.get("hands"), dict) else {}
            mind = res.get("mind") if isinstance(res.get("mind"), dict) else {}
            print(f"{mode} project_dir={pp.project_dir}")
            print(f"hands: keep={hands.get('keep')} planned={hands.get('planned')}")
            print(f"mind: keep={mind.get('keep')} planned={mind.get('planned')}")
            if not bool(args.apply):
                print("Re-run with --apply to archive.")
            return 0

        if args.gc_cmd == "thoughtdb":
            dry_run = not bool(getattr(args, "apply", False))
            is_global = bool(getattr(args, "gc_global", False))

            if is_global:
                gp = GlobalPaths(home_dir=home_dir)
                snap = gp.thoughtdb_global_dir / "view.snapshot.json"
                res = compact_thoughtdb_dir(thoughtdb_dir=gp.thoughtdb_global_dir, snapshot_path=snap, dry_run=dry_run)
                res["scope"] = "global"
            else:
                project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
                pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
                snap = pp.thoughtdb_dir / "view.snapshot.json"
                res = compact_thoughtdb_dir(thoughtdb_dir=pp.thoughtdb_dir, snapshot_path=snap, dry_run=dry_run)
                res["scope"] = "project"
                res["project_id"] = pp.project_id
                res["project_dir"] = str(pp.project_dir)

            # Rebuild snapshot after applying compaction (best-effort).
            res["snapshot"] = res.get("snapshot") if isinstance(res.get("snapshot"), dict) else {"path": str(snap)}
            if not dry_run:
                try:
                    if is_global:
                        dummy_pp = ProjectPaths(home_dir=home_dir, project_root=Path("."), _project_id="__global__")
                        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=dummy_pp)
                        tdb.load_view(scope="global")
                    else:
                        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)  # type: ignore[arg-type]
                        tdb.load_view(scope="project")
                    res["snapshot"]["rebuilt"] = True
                except Exception as e:
                    res["snapshot"]["rebuilt"] = False
                    res["snapshot"]["rebuild_error"] = f"{type(e).__name__}: {e}"

            if args.json:
                print(json.dumps(res, indent=2, sort_keys=True))
                return 0

            mode = "dry-run" if res.get("dry_run") else "applied"
            scope = str(res.get("scope") or "").strip() or ("global" if is_global else "project")
            print(f"{mode} scope={scope} thoughtdb_dir={res.get('thoughtdb_dir')}")
            files = res.get("files") if isinstance(res.get("files"), dict) else {}
            for name in ("claims", "edges", "nodes"):
                item = files.get(name) if isinstance(files.get(name), dict) else {}
                w = item.get("write") if isinstance(item.get("write"), dict) else {}
                cs = item.get("compact_stats") if isinstance(item.get("compact_stats"), dict) else {}
                planned = w.get("lines") if isinstance(w.get("lines"), int) else cs.get("output_lines")
                inp = cs.get("input_lines")
                print(f"{name}: input_lines={inp} output_lines={planned}")
            if dry_run:
                print("Re-run with --apply to compact and archive.")
            return 0

    rc = handle_knowledge_workflow_host_commands(
        args=args,
        home_dir=home_dir,
        cfg=cfg,
        resolve_project_root_from_args=_resolve_project_root_from_args,
        effective_cd_arg=_effective_cd_arg,
        read_user_line=_read_user_line,
        unified_diff=_unified_diff,
    )
    if rc is not None:
        return rc

    return 2
