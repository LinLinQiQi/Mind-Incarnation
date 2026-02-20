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
from .cli_commands import handle_show, handle_tail
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

    if args.cmd == "claim":
        project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        overlay2 = load_project_overlay(home_dir=home_dir, project_root=project_root)
        if not isinstance(overlay2, dict):
            overlay2 = {}

        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)

        def _view_for_scope(scope: str) -> object:
            sc = str(scope or "project").strip()
            if sc not in ("project", "global"):
                sc = "project"
            return tdb.load_view(scope=sc)

        def _iter_effective_claims(
            *,
            include_inactive: bool,
            include_aliases: bool,
            as_of_ts: str,
            filter_fn: Any,
        ) -> list[dict]:
            proj = tdb.load_view(scope="project")
            glob = tdb.load_view(scope="global")
            out: list[dict] = []
            seen: set[str] = set()

            def sig_for(c: dict) -> str:
                ct = str(c.get("claim_type") or "").strip()
                text = str(c.get("text") or "").strip()
                return claim_signature(claim_type=ct, scope="effective", project_id="", text=text)

            for c in proj.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases, as_of_ts=as_of_ts):
                if not isinstance(c, dict):
                    continue
                if not filter_fn(c):
                    continue
                s = sig_for(c)
                if s:
                    seen.add(s)
                out.append(c)

            for c in glob.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases, as_of_ts=as_of_ts):
                if not isinstance(c, dict):
                    continue
                if not filter_fn(c):
                    continue
                s = sig_for(c)
                if s and s in seen:
                    continue
                out.append(c)

            # Sort newest first when possible.
            out.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)
            return out

        def _find_claim_effective(cid: str) -> tuple[str, dict[str, Any] | None]:
            """Return (scope, claim) searching project then global."""
            c = (cid or "").strip()
            if not c:
                return "", None
            for sc in ("project", "global"):
                v = tdb.load_view(scope=sc)
                if c in v.claims_by_id:
                    obj = dict(v.claims_by_id[c])
                    obj["status"] = v.claim_status(c)
                    obj["canonical_id"] = v.resolve_id(c)
                    return sc, obj
                canon = v.resolve_id(c)
                if canon and canon in v.claims_by_id:
                    obj = dict(v.claims_by_id[canon])
                    obj["status"] = v.claim_status(canon)
                    obj["canonical_id"] = v.resolve_id(canon)
                    obj["requested_id"] = c
                    return sc, obj
            return "", None

        if args.claim_cmd == "list":
            scope = str(getattr(args, "scope", "project") or "project").strip()
            raw_statuses = getattr(args, "status", None) or []
            want_statuses = {str(x).strip() for x in raw_statuses if str(x).strip()}
            include_inactive = bool(getattr(args, "all", False)) or (bool(want_statuses) and want_statuses != {"active"})
            include_aliases = bool(getattr(args, "all", False))

            raw_tags = getattr(args, "tag", None) or []
            want_tags = {str(x).strip().lower() for x in raw_tags if str(x).strip()}
            contains = str(getattr(args, "contains", "") or "").strip().lower()
            raw_types = getattr(args, "claim_type", None) or []
            want_types = {str(x).strip() for x in raw_types if str(x).strip()}
            try:
                limit = int(getattr(args, "limit", 0) or 0)
            except Exception:
                limit = 0
            as_of_ts = str(getattr(args, "as_of", "") or "").strip() or now_rfc3339()

            def _claim_matches(c: dict[str, Any]) -> bool:
                if want_types and str(c.get("claim_type") or "").strip() not in want_types:
                    return False
                if want_statuses and str(c.get("status") or "").strip() not in want_statuses:
                    return False
                if want_tags:
                    tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                    tagset = {str(x).strip().lower() for x in tags if str(x).strip()}
                    if not all(t in tagset for t in want_tags):
                        return False
                if contains:
                    text = str(c.get("text") or "")
                    if contains not in text.lower():
                        return False
                return True

            if scope == "effective":
                items = _iter_effective_claims(
                    include_inactive=include_inactive,
                    include_aliases=include_aliases,
                    as_of_ts=as_of_ts,
                    filter_fn=_claim_matches,
                )
            else:
                v = tdb.load_view(scope=scope)
                items = [
                    x
                    for x in v.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases, as_of_ts=as_of_ts)
                    if isinstance(x, dict) and _claim_matches(x)
                ]
                items.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)

            if limit > 0:
                items = items[:limit]

            if getattr(args, "json", False):
                print(json.dumps(items, indent=2, sort_keys=True))
                return 0

            if not items:
                print("(no claims)")
                return 0
            for c in items:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("claim_id") or "").strip()
                ct = str(c.get("claim_type") or "").strip()
                st = str(c.get("status") or "").strip()
                sc = str(c.get("scope") or scope).strip()
                text = str(c.get("text") or "").strip().replace("\n", " ")
                if len(text) > 140:
                    text = text[:137] + "..."
                print(f"{cid} scope={sc} status={st} type={ct} {text}".strip())
            return 0

        if args.claim_cmd == "show":
            cid = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "effective") or "effective").strip()
            found_scope = ""
            obj: dict[str, Any] | None = None
            edges: list[dict[str, Any]] = []

            want_graph = bool(getattr(args, "graph", False))
            if want_graph and not bool(getattr(args, "json", False)):
                print("--graph requires --json", file=sys.stderr)
                return 2

            if scope == "effective":
                found_scope, obj = _find_claim_effective(cid)
                if found_scope:
                    v = tdb.load_view(scope=found_scope)
                    canon = v.resolve_id(cid)
                    for e in v.edges:
                        if not isinstance(e, dict):
                            continue
                        frm = str(e.get("from_id") or "").strip()
                        to = str(e.get("to_id") or "").strip()
                        if cid in (frm, to) or (canon and canon in (frm, to)):
                            edges.append(e)
            else:
                v = tdb.load_view(scope=scope)
                if cid in v.claims_by_id:
                    obj = dict(v.claims_by_id[cid])
                    obj["status"] = v.claim_status(cid)
                    obj["canonical_id"] = v.resolve_id(cid)
                    found_scope = scope
                else:
                    canon = v.resolve_id(cid)
                    if canon and canon in v.claims_by_id:
                        obj = dict(v.claims_by_id[canon])
                        obj["status"] = v.claim_status(canon)
                        obj["canonical_id"] = v.resolve_id(canon)
                        obj["requested_id"] = cid
                        found_scope = scope
                if found_scope:
                    canon = v.resolve_id(cid)
                    for e in v.edges:
                        if not isinstance(e, dict):
                            continue
                        frm = str(e.get("from_id") or "").strip()
                        to = str(e.get("to_id") or "").strip()
                        if cid in (frm, to) or (canon and canon in (frm, to)):
                            edges.append(e)

            if not obj:
                print(f"claim not found: {cid}", file=sys.stderr)
                return 2

            payload = {"scope": found_scope, "claim": obj, "edges": edges}
            if want_graph:
                edge_types_raw = getattr(args, "edge_types", None) or []
                etypes = {str(x).strip() for x in edge_types_raw if str(x).strip()}
                graph_scope = scope if scope == "effective" else found_scope
                payload["graph"] = build_subgraph_for_id(
                    tdb=tdb,
                    scope=graph_scope,
                    root_id=str(obj.get("claim_id") or cid).strip() or cid,
                    depth=int(getattr(args, "depth", 1) or 1),
                    direction=str(getattr(args, "direction", "both") or "both").strip(),
                    edge_types=etypes,
                    include_inactive=bool(getattr(args, "include_inactive", False)),
                    include_aliases=bool(getattr(args, "include_aliases", False)),
                )
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0

            c = obj
            print(f"claim_id={c.get('claim_id')}")
            if c.get("requested_id") and c.get("requested_id") != c.get("claim_id"):
                print(f"requested_id={c.get('requested_id')}")
            print(f"scope={found_scope}")
            print(f"type={c.get('claim_type')}")
            print(f"status={c.get('status')}")
            canon = c.get("canonical_id")
            if canon and canon != c.get("claim_id"):
                print(f"canonical_id={canon}")
            text = str(c.get("text") or "").strip()
            if text:
                print("text:")
                print(text)
            if edges:
                print(f"edges={len(edges)}")
            return 0

        if args.claim_cmd == "apply-suggested":
            sug_id = str(getattr(args, "suggestion_id", "") or "").strip()
            if not sug_id:
                print("missing suggestion_id", file=sys.stderr)
                return 2

            suggestion: dict[str, object] | None = None
            for obj in iter_jsonl(pp.evidence_log_path):
                if not isinstance(obj, dict):
                    continue
                if obj.get("kind") != "learn_suggested":
                    continue
                if str(obj.get("id") or "") == sug_id:
                    suggestion = obj

            if suggestion is None:
                print(f"suggestion not found: {sug_id}", file=sys.stderr)
                return 2

            # Avoid duplicate application unless forced.
            already_applied = False
            applied_ids0 = suggestion.get("applied_claim_ids")
            if isinstance(applied_ids0, list) and any(str(x).strip() for x in applied_ids0):
                already_applied = True

            if not already_applied:
                for obj in iter_jsonl(pp.evidence_log_path):
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") != "learn_applied":
                        continue
                    if str(obj.get("suggestion_id") or "") == sug_id:
                        already_applied = True
                        break

            if already_applied and not bool(getattr(args, "force", False)):
                print(f"Suggestion already applied: {sug_id}")
                return 0

            changes = suggestion.get("learn_suggested") if isinstance(suggestion.get("learn_suggested"), list) else []
            normalized: list[dict[str, str]] = []
            for ch in changes:
                if not isinstance(ch, dict):
                    continue
                scope = str(ch.get("scope") or "").strip()
                text = str(ch.get("text") or "").strip()
                if scope not in ("global", "project") or not text:
                    continue
                normalized.append(
                    {
                        "scope": scope,
                        "text": text,
                        "rationale": str(ch.get("rationale") or "").strip(),
                        "severity": str(ch.get("severity") or "").strip(),
                    }
                )

            if not normalized:
                print(f"(no applicable learn_suggested items in suggestion {sug_id})")
                return 0

            if bool(getattr(args, "dry_run", False)):
                print(json.dumps({"suggestion_id": sug_id, "changes": normalized}, indent=2, sort_keys=True))
                return 0

            extra = str(getattr(args, "extra_rationale", "") or "").strip()
            sig_to_id = {
                "project": tdb.existing_signature_map(scope="project"),
                "global": tdb.existing_signature_map(scope="global"),
            }
            ev_id = str(suggestion.get("event_id") or "").strip()
            src_eids = [ev_id] if ev_id else []

            applied_claim_ids: list[str] = []
            for item in normalized:
                scope0 = str(item.get("scope") or "").strip()
                sc = "global" if scope0 == "global" else "project"
                pid = pp.project_id if sc == "project" else ""
                text = str(item.get("text") or "").strip()
                if not text:
                    continue

                sig = claim_signature(claim_type="preference", scope=sc, project_id=pid, text=text)
                existing = sig_to_id.get(sc, {}).get(sig)
                if existing:
                    applied_claim_ids.append(str(existing))
                    continue

                base_r = (item.get("rationale") or "").strip() or "manual_apply"
                notes = f"{base_r} (apply_suggestion={sug_id})"
                if extra:
                    notes = f"{notes}; {extra}"
                sev = str(item.get("severity") or "").strip()
                tags = ["mi:learned_apply", f"learn_suggested:{sug_id}"]
                if sev:
                    tags.append(f"severity:{sev}")

                cid = tdb.append_claim_create(
                    claim_type="preference",
                    text=text,
                    scope=sc,
                    visibility=("global" if sc == "global" else "project"),
                    valid_from=None,
                    valid_to=None,
                    tags=tags,
                    source_event_ids=src_eids,
                    confidence=1.0,
                    notes=notes,
                )
                sig_to_id.setdefault(sc, {})[sig] = cid
                applied_claim_ids.append(cid)

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            evw.append(
                {
                    "kind": "learn_applied",
                    "ts": now_rfc3339(),
                    "suggestion_id": sug_id,
                    "batch_id": str(suggestion.get("batch_id") or ""),
                    "thread_id": str(suggestion.get("thread_id") or ""),
                    "applied_claim_ids": applied_claim_ids,
                }
            )
            print(f"Applied suggestion {sug_id}: {len(applied_claim_ids)} preference claims")
            for cid in applied_claim_ids:
                print(cid)
            return 0

        if args.claim_cmd == "retract":
            cid = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "project") or "project").strip()

            # Record a user-driven event in EvidenceLog and cite it in Thought DB.
            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "claim_retract",
                    "batch_id": "cli.claim_retract",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "claim_id": cid,
                    "rationale": str(getattr(args, "rationale", "") or "").strip(),
                }
            )
            try:
                tdb.append_claim_retract(
                    claim_id=cid,
                    scope=scope,
                    rationale=str(getattr(args, "rationale", "") or "").strip(),
                    source_event_ids=[str(ev.get("event_id") or "").strip()],
                )
            except Exception as e:
                print(f"retract failed: {e}", file=sys.stderr)
                return 2
            print(cid)
            return 0

        if args.claim_cmd == "supersede":
            old_id = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "effective") or "effective").strip()

            if scope == "effective":
                found_scope, old = _find_claim_effective(old_id)
                if not old or not found_scope:
                    print(f"old claim not found: {old_id}", file=sys.stderr)
                    return 2
                scope = found_scope
            else:
                v = tdb.load_view(scope=scope)
                old = dict(v.claims_by_id.get(old_id) or {})
                if not old:
                    print(f"old claim not found: {old_id}", file=sys.stderr)
                    return 2

            new_text = str(getattr(args, "text", "") or "").strip()
            if not new_text:
                print("--text is required", file=sys.stderr)
                return 2

            ct = str(getattr(args, "claim_type", "") or "").strip() or str(old.get("claim_type") or "").strip() or "fact"
            vis = str(getattr(args, "visibility", "") or "").strip() or str(old.get("visibility") or "").strip() or ("global" if scope == "global" else "project")
            vf = str(getattr(args, "valid_from", "") or "").strip() or None
            vt = str(getattr(args, "valid_to", "") or "").strip() or None
            tags = [str(x).strip() for x in (getattr(args, "tag", None) or []) if str(x).strip()]

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "claim_supersede",
                    "batch_id": "cli.claim_supersede",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "old_claim_id": old_id,
                    "new_text": new_text,
                    "claim_type": ct,
                    "visibility": vis,
                    "valid_from": vf,
                    "valid_to": vt,
                    "tags": tags,
                }
            )
            ev_id = str(ev.get("event_id") or "").strip()
            try:
                new_id = tdb.append_claim_create(
                    claim_type=ct,
                    text=new_text,
                    scope=scope,
                    visibility=vis,
                    valid_from=vf,
                    valid_to=vt,
                    tags=tags,
                    source_event_ids=[ev_id] if ev_id else [],
                    confidence=1.0,
                    notes="supersede via cli",
                )
                tdb.append_edge(
                    edge_type="supersedes",
                    from_id=old_id,
                    to_id=new_id,
                    scope=scope,
                    visibility=vis,
                    source_event_ids=[ev_id] if ev_id else [],
                    notes="supersede via cli",
                )
            except Exception as e:
                print(f"supersede failed: {e}", file=sys.stderr)
                return 2
            print(new_id)
            return 0

        if args.claim_cmd == "same-as":
            dup_id = str(args.dup_id or "").strip()
            canon_id = str(args.canonical_id or "").strip()
            scope = str(getattr(args, "scope", "project") or "project").strip()

            v = tdb.load_view(scope=scope)
            if dup_id not in v.claims_by_id or canon_id not in v.claims_by_id:
                print("both dup_id and canonical_id must exist in the same scope store", file=sys.stderr)
                return 2

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "claim_same_as",
                    "batch_id": "cli.claim_same_as",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "dup_id": dup_id,
                    "canonical_id": canon_id,
                    "notes": str(getattr(args, "notes", "") or "").strip(),
                }
            )
            ev_id = str(ev.get("event_id") or "").strip()
            try:
                tdb.append_edge(
                    edge_type="same_as",
                    from_id=dup_id,
                    to_id=canon_id,
                    scope=scope,
                    visibility=str(v.claims_by_id.get(dup_id, {}).get("visibility") or ("global" if scope == "global" else "project")),
                    source_event_ids=[ev_id] if ev_id else [],
                    notes=str(getattr(args, "notes", "") or "").strip(),
                )
            except Exception as e:
                print(f"same-as failed: {e}", file=sys.stderr)
                return 2
            print(f"{dup_id} -> {canon_id}")
            return 0

        if args.claim_cmd == "mine":
            # On-demand mining uses the Mind provider (same as other CLI model calls).
            runtime_cfg = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
            tcfg = runtime_cfg.get("thought_db") if isinstance(runtime_cfg.get("thought_db"), dict) else {}
            try:
                min_conf = float(tcfg.get("min_confidence", 0.9) or 0.9)
            except Exception:
                min_conf = 0.9
            try:
                max_claims = int(tcfg.get("max_claims_per_checkpoint", 6) or 6)
            except Exception:
                max_claims = 6
            if getattr(args, "min_confidence", -1.0) is not None and float(getattr(args, "min_confidence")) >= 0:
                min_conf = float(getattr(args, "min_confidence"))
            if getattr(args, "max_claims", -1) is not None and int(getattr(args, "max_claims")) >= 0:
                max_claims = int(getattr(args, "max_claims"))

            # Prefer the current open segment buffer; fall back to EvidenceLog tail.
            seg: dict[str, Any] | None = None
            try:
                seg = json.loads(pp.segment_state_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                seg = None
            except Exception:
                seg = None

            seg_records: list[dict[str, Any]] = []
            if isinstance(seg, dict) and bool(seg.get("open", False)) and isinstance(seg.get("records"), list):
                seg_records = [x for x in seg.get("records") if isinstance(x, dict)]  # type: ignore[arg-type]
            if not seg_records:
                seg_records = tail_json_objects(pp.evidence_log_path, 60)

            allowed: list[str] = []
            seen: set[str] = set()
            for r in seg_records:
                eid = r.get("event_id")
                if isinstance(eid, str) and eid.strip() and eid.strip() not in seen:
                    seen.add(eid.strip())
                    allowed.append(eid.strip())
            allowed_set = set(allowed)

            pp.transcripts_dir.mkdir(parents=True, exist_ok=True)
            mind = make_mind_provider(cfg, project_root=project_root, transcripts_dir=pp.transcripts_dir)
            tdb_ctx = build_decide_next_thoughtdb_context(
                tdb=tdb,
                as_of_ts=now_rfc3339(),
                task=str("(manual claim mine) " + (seg.get("task_hint") if isinstance(seg, dict) else "")).strip(),
                hands_last_message="",
                recent_evidence=seg_records[-8:],
            )
            tdb_ctx_obj = tdb_ctx.to_prompt_obj()
            prompt = mine_claims_prompt(
                task=str("(manual claim mine) " + (seg.get("task_hint") if isinstance(seg, dict) else "")).strip(),
                hands_provider=str(cfg.get("hands", {}).get("provider") or ""),
                mindspec_base=runtime_cfg,
                project_overlay=overlay2,
                thought_db_context=tdb_ctx_obj,
                segment_evidence=seg_records,
                allowed_event_ids=allowed,
                min_confidence=min_conf,
                max_claims=max_claims,
                notes="source=cli",
            )
            try:
                res = mind.call(schema_filename="mine_claims.json", prompt=prompt, tag="mine_claims_cli")
            except Exception as e:
                print(f"mind call failed: {e}", file=sys.stderr)
                return 2

            out = res.obj if hasattr(res, "obj") else {}
            applied = tdb.apply_mined_output(
                output=out if isinstance(out, dict) else {},
                allowed_event_ids=allowed_set,
                min_confidence=min_conf,
                max_claims=max_claims,
            )

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            evw.append(
                {
                    "kind": "claim_mining",
                    "batch_id": "cli.claim_mining",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "segment_id": str(seg.get("segment_id") or "") if isinstance(seg, dict) else "",
                    "mind_transcript_ref": str(getattr(res, "transcript_path", "") or ""),
                    "config": {"min_confidence": min_conf, "max_claims_per_checkpoint": max_claims},
                    "output": out if isinstance(out, dict) else {},
                    "applied": applied,
                }
            )

            if getattr(args, "json", False):
                print(json.dumps({"applied": applied, "output": out}, indent=2, sort_keys=True))
                return 0
            written = applied.get("written") if isinstance(applied, dict) else []
            print(f"written={len(written) if isinstance(written, list) else 0}")
            return 0

        print("unknown claim subcommand", file=sys.stderr)
        return 2

    if args.cmd == "node":
        project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)

        def _iter_effective_nodes(*, include_inactive: bool, include_aliases: bool) -> list[dict[str, Any]]:
            proj = tdb.load_view(scope="project")
            glob = tdb.load_view(scope="global")
            out: list[dict[str, Any]] = []
            seen: set[str] = set()

            for n in proj.iter_nodes(include_inactive=include_inactive, include_aliases=include_aliases):
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("node_id") or "").strip()
                if nid:
                    seen.add(nid)
                out.append(n)

            for n in glob.iter_nodes(include_inactive=include_inactive, include_aliases=include_aliases):
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("node_id") or "").strip()
                if nid and nid in seen:
                    continue
                out.append(n)

            out.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)
            return out

        def _find_node_effective(nid: str) -> tuple[str, dict[str, Any] | None]:
            """Return (scope, node) searching project then global."""
            n = (nid or "").strip()
            if not n:
                return "", None
            for sc in ("project", "global"):
                v = tdb.load_view(scope=sc)
                if n in v.nodes_by_id:
                    obj = dict(v.nodes_by_id[n])
                    obj["status"] = v.node_status(n)
                    obj["canonical_id"] = v.resolve_id(n)
                    return sc, obj
                canon = v.resolve_id(n)
                if canon and canon in v.nodes_by_id:
                    obj = dict(v.nodes_by_id[canon])
                    obj["status"] = v.node_status(canon)
                    obj["canonical_id"] = v.resolve_id(canon)
                    obj["requested_id"] = n
                    return sc, obj
            return "", None

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
                    v = tdb.load_view(scope=found_scope)
                    canon = v.resolve_id(nid)
                    for e in v.edges:
                        if not isinstance(e, dict):
                            continue
                        frm = str(e.get("from_id") or "").strip()
                        to = str(e.get("to_id") or "").strip()
                        if nid in (frm, to) or (canon and canon in (frm, to)):
                            edges.append(e)
            else:
                v = tdb.load_view(scope=scope)
                if nid in v.nodes_by_id:
                    obj = dict(v.nodes_by_id[nid])
                    obj["status"] = v.node_status(nid)
                    obj["canonical_id"] = v.resolve_id(nid)
                    found_scope = scope
                else:
                    canon = v.resolve_id(nid)
                    if canon and canon in v.nodes_by_id:
                        obj = dict(v.nodes_by_id[canon])
                        obj["status"] = v.node_status(canon)
                        obj["canonical_id"] = v.resolve_id(canon)
                        obj["requested_id"] = nid
                        found_scope = scope
                if found_scope:
                    canon = v.resolve_id(nid)
                    for e in v.edges:
                        if not isinstance(e, dict):
                            continue
                        frm = str(e.get("from_id") or "").strip()
                        to = str(e.get("to_id") or "").strip()
                        if nid in (frm, to) or (canon and canon in (frm, to)):
                            edges.append(e)

            if not obj:
                print(f"node not found: {nid}", file=sys.stderr)
                return 2

            payload = {"scope": found_scope, "node": obj, "edges": edges}
            if want_graph:
                edge_types_raw = getattr(args, "edge_types", None) or []
                etypes = {str(x).strip() for x in edge_types_raw if str(x).strip()}
                graph_scope = scope if scope == "effective" else found_scope
                payload["graph"] = build_subgraph_for_id(
                    tdb=tdb,
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

    if args.cmd == "edge":
        project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
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

    if args.cmd == "why":
        project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
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

    if args.cmd == "workflow":
        project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
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
                diff = _unified_diff(before, after, fromfile="before", tofile="after")
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
                req0 = _read_user_line("Edit request (blank to cancel):")
            rc = _run_once(str(req0 or ""))
            if rc != 0:
                return rc
            if not bool(args.loop):
                return 0
            while True:
                nxt = _read_user_line("Next edit request (blank to stop):")
                if not nxt.strip():
                    return 0
                rc2 = _run_once(nxt)
                if rc2 != 0:
                    return rc2

        return 2

    if args.cmd == "host":
        project_root = _resolve_project_root_from_args(home_dir, _effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
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

    return 2
