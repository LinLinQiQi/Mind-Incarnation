import argparse
import difflib
import json
import os
import sys
from pathlib import Path

from . import __version__
from .config import (
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
from .mindspec import MindSpecStore
from .prompts import compile_mindspec_prompt, edit_workflow_prompt
from .runner import run_autopilot
from .paths import GlobalPaths, ProjectPaths, default_home_dir, project_index_path, resolve_cli_project_root
from .inspect import load_last_batch_bundle, tail_raw_lines, tail_json_objects, summarize_evidence_record
from .transcript import last_agent_message_from_transcript, tail_transcript_lines, resolve_transcript_path
from .redact import redact_text
from .provider_factory import make_hands_functions, make_mind_provider
from .gc import archive_project_transcripts
from .storage import append_jsonl, iter_jsonl, now_rfc3339
from .workflows import (
    WorkflowStore,
    GlobalWorkflowStore,
    WorkflowRegistry,
    new_workflow_id,
    render_workflow_markdown,
    normalize_workflow,
    apply_global_overrides,
)
from .hosts import parse_host_bindings, sync_host_binding, sync_hosts_from_overlay
from .memory import MemoryIndex, rebuild_memory_index
from .evidence import EvidenceWriter, new_run_id


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


def _resolve_project_root_from_args(store: MindSpecStore, cd_arg: str) -> Path:
    """Resolve an effective project root for CLI handlers.

    - If `--cd` is omitted, MI may infer git toplevel (see `resolve_cli_project_root`).
    - Print a short stderr note when inference changes the root away from cwd.
    """

    root, reason = resolve_cli_project_root(store.home_dir, cd_arg, cwd=Path.cwd())
    cwd = Path.cwd().resolve()
    if reason != "arg" and root != cwd:
        print(f"[mi] using inferred project_root={root} (reason={reason}, cwd={cwd})", file=sys.stderr)
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mi",
        description="Mind Incarnation (MI) V1: a values-driven mind layer above execution agents (default Hands: Codex CLI).",
    )
    parser.add_argument(
        "--home",
        default=os.environ.get("MI_HOME"),
        help="MI home directory (defaults to $MI_HOME or ~/.mind-incarnation).",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="Print MI version.")

    p_cfg = sub.add_parser("config", help="Manage MI config (Mind/Hands providers).")
    cfg_sub = p_cfg.add_subparsers(dest="config_cmd", required=True)
    p_ci = cfg_sub.add_parser("init", help="Write a default config.json to MI home.")
    p_ci.add_argument("--force", action="store_true", help="Overwrite existing config.json.")
    cfg_sub.add_parser("show", help="Show the current config (redacted).")
    cfg_sub.add_parser("validate", help="Validate the current config.json (errors + warnings).")
    cfg_sub.add_parser("doctor", help="Alias for validate (for discoverability).")
    cfg_sub.add_parser("examples", help="List config template names.")
    p_ct = cfg_sub.add_parser("template", help="Print a config template as JSON (merge into config.json).")
    p_ct.add_argument("name", help="Template name (see `mi config examples`).")
    p_cat = cfg_sub.add_parser("apply-template", help="Deep-merge a template into config.json (writes a rollback backup).")
    p_cat.add_argument("name", help="Template name (see `mi config examples`).")
    cfg_sub.add_parser("rollback", help="Rollback config.json to the last apply-template backup.")
    cfg_sub.add_parser("path", help="Print the config.json path.")

    p_init = sub.add_parser("init", help="Initialize global values/preferences (MindSpec base).")
    p_init.add_argument(
        "--values",
        help="Values/preferences prompt text. If omitted or '-', read from stdin.",
        default="-",
    )
    p_init.add_argument(
        "--no-compile",
        action="store_true",
        help="Do not call the model; write defaults + values_text only.",
    )
    p_init.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the compiled MindSpec but do not write it.",
    )
    p_init.add_argument(
        "--show",
        action="store_true",
        help="Print the compiled values summary and decision procedure.",
    )

    p_run = sub.add_parser("run", help="Run MI batch autopilot (Hands configured via mi config).")
    p_run.add_argument("task", help="User task for Hands to execute.")
    p_run.add_argument(
        "--cd",
        default="",
        help="Project root for the Hands run (default: infer from cwd; git toplevel when available).",
    )
    p_run.add_argument(
        "--max-batches",
        type=int,
        default=8,
        help="Maximum number of Hands batches before stopping.",
    )
    p_run.add_argument(
        "--continue-hands",
        action="store_true",
        help="Try to resume the last stored Hands thread/session id across separate `mi run` invocations (best-effort).",
    )
    p_run.add_argument(
        "--reset-hands",
        action="store_true",
        help="Clear the stored Hands thread/session id for this project before running (forces a fresh Hands session).",
    )
    p_run.add_argument(
        "--show",
        action="store_true",
        help="Print MI summaries plus pointers to raw transcript and evidence log.",
    )

    p_learned = sub.add_parser("learned", help="Inspect or rollback learned preferences.")
    learned_sub = p_learned.add_subparsers(dest="learned_cmd", required=True)

    p_ll = learned_sub.add_parser("list", help="List learned entries (global + project).")
    p_ll.add_argument("--cd", default="", help="Project root used for project-scoped learned entries.")

    p_ld = learned_sub.add_parser("disable", help="Disable a learned entry by id (append-only).")
    p_ld.add_argument("id", help="Learned change id to disable.")
    p_ld.add_argument(
        "--scope",
        choices=["global", "project"],
        default="project",
        help="Where to record the disable action. 'project' disables only for this project; 'global' disables everywhere.",
    )
    p_ld.add_argument("--cd", default="", help="Project root used for project-scoped disable.")
    p_ld.add_argument("--rationale", default="user rollback", help="Reason to record for the rollback.")

    p_las = learned_sub.add_parser(
        "apply-suggested",
        help="Apply a previously suggested learned change from EvidenceLog (append-only).",
    )
    p_las.add_argument("suggestion_id", help="Suggestion id from EvidenceLog record kind=learn_suggested.")
    p_las.add_argument("--cd", default="", help="Project root used to locate EvidenceLog and learned storage.")
    p_las.add_argument("--dry-run", action="store_true", help="Show what would be applied without writing.")
    p_las.add_argument("--force", action="store_true", help="Apply even if the suggestion looks already applied.")
    p_las.add_argument(
        "--extra-rationale",
        default="",
        help="Optional extra rationale to append to the learned entry (for audit).",
    )

    p_wf = sub.add_parser("workflow", help="Manage workflows (project or global; MI IR).")
    wf_sub = p_wf.add_subparsers(dest="wf_cmd", required=True)

    p_wfl = wf_sub.add_parser("list", help="List workflows for the project.")
    p_wfl.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wfl.add_argument(
        "--scope",
        choices=["project", "global", "effective"],
        default="project",
        help="Which store to list (effective merges project+global with project precedence).",
    )

    p_wfs = wf_sub.add_parser("show", help="Show a workflow by id.")
    p_wfs.add_argument("id", help="Workflow id (wf_...).")
    p_wfs.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wfs.add_argument(
        "--scope",
        choices=["project", "global", "effective"],
        default="project",
        help="Which store to load from (effective tries project first, then global).",
    )
    p_wfs.add_argument("--json", action="store_true", help="Print workflow JSON.")
    p_wfs.add_argument("--markdown", action="store_true", help="Print workflow as Markdown.")

    p_wfc = wf_sub.add_parser("create", help="Create a new workflow (minimal stub).")
    p_wfc.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wfc.add_argument(
        "--scope",
        choices=["project", "global"],
        default="project",
        help="Where to create the workflow (project is default).",
    )
    p_wfc.add_argument("--name", required=True, help="Workflow name.")
    p_wfc.add_argument("--disabled", action="store_true", help="Create as disabled (default is enabled).")
    p_wfc.add_argument("--trigger-mode", default="manual", choices=["manual", "task_contains"], help="Trigger mode.")
    p_wfc.add_argument("--pattern", default="", help="Trigger pattern (used when trigger-mode=task_contains).")

    p_wfe = wf_sub.add_parser("enable", help="Enable a workflow.")
    p_wfe.add_argument("id", help="Workflow id (wf_...).")
    p_wfe.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wfe.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to modify.")
    p_wfe.add_argument(
        "--project-override",
        action="store_true",
        help="When scope=global, write a per-project override instead of editing the global workflow file.",
    )

    p_wfd = wf_sub.add_parser("disable", help="Disable a workflow.")
    p_wfd.add_argument("id", help="Workflow id (wf_...).")
    p_wfd.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wfd.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to modify.")
    p_wfd.add_argument(
        "--project-override",
        action="store_true",
        help="When scope=global, write a per-project override instead of editing the global workflow file.",
    )

    p_wfx = wf_sub.add_parser("delete", help="Delete a workflow (source of truth).")
    p_wfx.add_argument("id", help="Workflow id (wf_...).")
    p_wfx.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wfx.add_argument("--scope", choices=["project", "global"], default="project", help="Which store to delete from.")
    p_wfx.add_argument(
        "--project-override",
        action="store_true",
        help="When scope=global, remove this project's override entry instead of deleting the global workflow file.",
    )

    p_wfedit = wf_sub.add_parser("edit", help="Edit a workflow via natural language (uses Mind provider).")
    p_wfedit.add_argument("id", help="Workflow id (wf_...).")
    p_wfedit.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wfedit.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to edit.")
    p_wfedit.add_argument(
        "--project-override",
        action="store_true",
        help="When scope=global, write a per-project override patch instead of editing the global workflow file.",
    )
    p_wfedit.add_argument(
        "--request",
        default="-",
        help="Edit request text. If omitted or '-', read a single line from stdin.",
    )
    p_wfedit.add_argument("--loop", action="store_true", help="After applying, prompt for more edits until blank.")
    p_wfedit.add_argument("--dry-run", action="store_true", help="Show proposed edits but do not write.")

    p_host = sub.add_parser("host", help="Configure/sync derived artifacts into host workspaces.")
    host_sub = p_host.add_subparsers(dest="host_cmd", required=True)

    p_hl = host_sub.add_parser("list", help="List host bindings for the project.")
    p_hl.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")

    p_hb = host_sub.add_parser("bind", help="Bind a host workspace to this project (writes ProjectOverlay).")
    p_hb.add_argument("host", help="Host name (e.g., openclaw).")
    p_hb.add_argument("--workspace", required=True, help="Host workspace root path.")
    p_hb.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_hb.add_argument(
        "--generated-rel-dir",
        default="",
        help="Relative path under workspace_root for MI derived artifacts (default: .mi/generated/<host>).",
    )
    p_hb.add_argument(
        "--symlink-dir",
        action="append",
        default=[],
        help="Register by symlinking a generated subdir into workspace. Format: SRC:DST (both relative). Repeatable.",
    )

    p_hu = host_sub.add_parser("unbind", help="Remove a host binding from this project.")
    p_hu.add_argument("host", help="Host name.")
    p_hu.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")

    p_hs = host_sub.add_parser("sync", help="Sync enabled workflows into all bound host workspaces (derived artifacts).")
    p_hs.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_hs.add_argument("--json", action="store_true", help="Print sync result as JSON.")

    p_last = sub.add_parser("last", help="Show the latest MI batch bundle (input/output/evidence pointers).")
    p_last.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_last.add_argument("--json", action="store_true", help="Print as JSON.")
    p_last.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_evidence = sub.add_parser("evidence", help="Inspect EvidenceLog (JSONL).")
    ev_sub = p_evidence.add_subparsers(dest="evidence_cmd", required=True)
    p_ev_tail = ev_sub.add_parser("tail", help="Tail EvidenceLog records.")
    p_ev_tail.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ev_tail.add_argument("-n", "--lines", type=int, default=20, help="Number of records to show.")
    p_ev_tail.add_argument("--raw", action="store_true", help="Print raw JSONL lines.")
    p_ev_tail.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_tr = sub.add_parser("transcript", help="Inspect raw transcripts (Hands or Mind).")
    tr_sub = p_tr.add_subparsers(dest="tr_cmd", required=True)
    p_tr_show = tr_sub.add_parser("show", help="Show a transcript (defaults to the latest Hands transcript).")
    p_tr_show.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_tr_show.add_argument("--mind", action="store_true", help="Show Mind transcript instead of Hands.")
    p_tr_show.add_argument("--path", default="", help="Explicit transcript path to show (overrides --mind/--cd selection).")
    p_tr_show.add_argument("-n", "--lines", type=int, default=200, help="Number of transcript lines to show (tail).")
    p_tr_show.add_argument("--jsonl", action="store_true", help="Print stored JSONL lines (no pretty formatting).")
    p_tr_show.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_mem = sub.add_parser("memory", help="Manage MI memory index (materialized view).")
    mem_sub = p_mem.add_subparsers(dest="mem_cmd", required=True)
    p_mi = mem_sub.add_parser("index", help="Manage the memory text index (rebuildable).")
    mi_sub = p_mi.add_subparsers(dest="mi_cmd", required=True)
    p_mis = mi_sub.add_parser("status", help="Show memory index status.")
    p_mis.add_argument("--json", action="store_true", help="Print status as JSON.")
    p_mir = mi_sub.add_parser("rebuild", help="Rebuild memory index from MI stores (learned/workflows + snapshots).")
    p_mir.add_argument("--no-snapshots", action="store_true", help="Skip indexing snapshot records from EvidenceLog.")
    p_mir.add_argument("--json", action="store_true", help="Print rebuild result as JSON.")

    p_proj = sub.add_parser("project", help="Inspect per-project MI state (overlay + resolved paths).")
    proj_sub = p_proj.add_subparsers(dest="project_cmd", required=True)
    p_ps = proj_sub.add_parser("show", help="Show the project overlay and resolved storage paths.")
    p_ps.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ps.add_argument("--json", action="store_true", help="Print as JSON.")
    p_ps.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_gc = sub.add_parser("gc", help="Garbage collect / archive MI artifacts (optional).")
    gc_sub = p_gc.add_subparsers(dest="gc_cmd", required=True)
    p_gct = gc_sub.add_parser("transcripts", help="Archive older transcripts to reduce disk usage (safe, reversible).")
    p_gct.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_gct.add_argument("--keep-hands", type=int, default=50, help="Keep N most recent raw Hands transcripts.")
    p_gct.add_argument("--keep-mind", type=int, default=200, help="Keep N most recent raw Mind transcripts.")
    p_gct.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run).")
    p_gct.add_argument("--json", action="store_true", help="Print result as JSON.")

    args = parser.parse_args(argv)

    store = MindSpecStore(home_dir=args.home)
    cfg = load_config(store.home_dir)

    if args.cmd == "version":
        print(__version__)
        return 0

    if args.cmd == "config":
        if args.config_cmd == "path":
            print(str(config_path(store.home_dir)))
            return 0
        if args.config_cmd == "init":
            path = init_config(store.home_dir, force=bool(args.force))
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
                res = apply_config_template(store.home_dir, name=str(args.name))
            except Exception as e:
                print(f"apply-template failed: {e}", file=sys.stderr)
                return 2
            print(f"Applied template: {args.name}")
            print(f"Backup: {res.get('backup_path')}")
            print(f"Config: {res.get('config_path')}")
            return 0
        if args.config_cmd == "rollback":
            try:
                res = rollback_config(store.home_dir)
            except Exception as e:
                print(f"rollback failed: {e}", file=sys.stderr)
                return 2
            print(f"Rolled back config to: {res.get('backup_path')}")
            print(f"Config: {res.get('config_path')}")
            return 0
        if args.config_cmd in ("validate", "doctor"):
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
        values = args.values
        if values == "-" or values is None:
            values = _read_stdin_text()
        if not values.strip():
            print("Values text is empty. Provide --values or pipe text to stdin.", file=sys.stderr)
            return 2

        compiled = None
        if not args.no_compile:
            # Run compile in an isolated directory to avoid accidental project context bleed.
            scratch = store.home_dir / "tmp" / "compile_mindspec"
            scratch.mkdir(parents=True, exist_ok=True)
            transcripts_dir = store.home_dir / "mindspec" / "transcripts"

            base_template = store.load_base()
            base_template["values_text"] = values

            llm = make_mind_provider(cfg, project_root=scratch, transcripts_dir=transcripts_dir)
            prompt = compile_mindspec_prompt(values_text=values, base_template=base_template)
            try:
                compiled = llm.call(schema_filename="compile_mindspec.json", prompt=prompt, tag="compile_mindspec").obj
            except Exception as e:
                print(f"compile_mindspec failed; falling back to defaults. error={e}", file=sys.stderr)

        if compiled is None:
            store.write_base_values(values_text=values)
            compiled = store.load_base()
        else:
            if not args.dry_run:
                store.write_base(compiled)

        if args.show or args.dry_run:
            vs = compiled.get("values_summary") or []
            if isinstance(vs, list) and any(str(x).strip() for x in vs):
                print("values_summary:")
                for x in vs:
                    x = str(x).strip()
                    if x:
                        print(f"- {x}")
            dp = compiled.get("decision_procedure") or {}
            if isinstance(dp, dict):
                summary = str(dp.get("summary") or "").strip()
                mermaid = str(dp.get("mermaid") or "").strip()
                if summary:
                    print("\ndecision_procedure.summary:\n" + summary)
                if mermaid:
                    print("\ndecision_procedure.mermaid:\n" + mermaid)

        if args.dry_run:
            print("(dry-run) did not write base MindSpec.")
            return 0

        print(f"Wrote base MindSpec to {store.base_path}")
        return 0

    if args.cmd == "run":
        hands_exec, hands_resume = make_hands_functions(cfg)
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        project_paths = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        llm = make_mind_provider(cfg, project_root=project_root, transcripts_dir=project_paths.transcripts_dir)
        hands_provider = ""
        hands_cfg = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
        if isinstance(hands_cfg, dict):
            hands_provider = str(hands_cfg.get("provider") or "").strip()
        continue_default = bool(hands_cfg.get("continue_across_runs", False)) if isinstance(hands_cfg, dict) else False
        continue_hands = bool(args.continue_hands or continue_default)
        result = run_autopilot(
            task=args.task,
            project_root=str(project_root),
            home_dir=args.home,
            max_batches=args.max_batches,
            hands_exec=hands_exec,
            hands_resume=hands_resume,
            llm=llm,
            hands_provider=hands_provider,
            continue_hands=continue_hands,
            reset_hands=bool(args.reset_hands),
        )
        if args.show:
            print(result.render_text())
        return 0 if result.status == "done" else 1

    if args.cmd == "last":
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)

        bundle = load_last_batch_bundle(pp.evidence_log_path)
        codex_input = bundle.get("codex_input") if isinstance(bundle.get("codex_input"), dict) else None
        evidence_item = bundle.get("evidence_item") if isinstance(bundle.get("evidence_item"), dict) else None
        decide_next = bundle.get("decide_next") if isinstance(bundle.get("decide_next"), dict) else None

        transcript_path = ""
        if codex_input and isinstance(codex_input.get("transcript_path"), str):
            transcript_path = codex_input["transcript_path"]
        elif evidence_item and isinstance(evidence_item.get("codex_transcript_ref"), str):
            transcript_path = evidence_item["codex_transcript_ref"]

        last_msg = ""
        if transcript_path:
            last_msg = last_agent_message_from_transcript(Path(transcript_path))

        mi_input_text = (codex_input.get("input") if codex_input else "") or ""
        codex_last_text = last_msg or ""

        evidence_item_out = evidence_item or {}
        decide_next_out = decide_next or {}
        learn_suggested_out = (bundle.get("learn_suggested") or []) if isinstance(bundle.get("learn_suggested"), list) else []
        learn_applied_out = (bundle.get("learn_applied") or []) if isinstance(bundle.get("learn_applied"), list) else []
        if args.redact:
            mi_input_text = redact_text(mi_input_text)
            codex_last_text = redact_text(codex_last_text)
            if isinstance(evidence_item_out, dict):
                for k in ("facts", "results", "unknowns", "risk_signals"):
                    v = evidence_item_out.get(k)
                    if isinstance(v, list):
                        evidence_item_out[k] = [redact_text(str(x)) for x in v]
            if isinstance(decide_next_out, dict):
                for k in ("notes", "ask_user_question", "next_codex_input"):
                    v = decide_next_out.get(k)
                    if isinstance(v, str) and v:
                        decide_next_out[k] = redact_text(v)
                inner = decide_next_out.get("decision")
                if isinstance(inner, dict):
                    for k in ("notes", "ask_user_question", "next_codex_input"):
                        v = inner.get(k)
                        if isinstance(v, str) and v:
                            inner[k] = redact_text(v)
            # Redact learned suggestion texts/rationales (they may contain tokens/URLs).
            for rec in learn_suggested_out:
                if not isinstance(rec, dict):
                    continue
                chs = rec.get("learned_changes")
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

        out = {
            "project_root": str(project_root),
            "project_dir": str(pp.project_dir),
            "evidence_log": str(pp.evidence_log_path),
            "batch_id": bundle.get("batch_id") or "",
            "thread_id": bundle.get("thread_id") or "",
            "hands_transcript": transcript_path,
            "mi_input": mi_input_text,
            "codex_last_message": codex_last_text,
            "evidence_item": evidence_item_out,
            "check_plan": (bundle.get("check_plan") or {}) if isinstance(bundle.get("check_plan"), dict) else {},
            "auto_answer": (bundle.get("auto_answer") or {}) if isinstance(bundle.get("auto_answer"), dict) else {},
            "risk_event": (bundle.get("risk_event") or {}) if isinstance(bundle.get("risk_event"), dict) else {},
            "learn_suggested": learn_suggested_out,
            "learn_applied": learn_applied_out,
            "loop_guard": (bundle.get("loop_guard") or {}) if isinstance(bundle.get("loop_guard"), dict) else {},
            "decide_next": decide_next_out,
            "mind_transcripts": (bundle.get("mind_transcripts") or []) if isinstance(bundle.get("mind_transcripts"), list) else [],
        }

        if args.json:
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0

        print(f"thread_id={out['thread_id']} batch_id={out['batch_id']}")
        print(f"project_dir={out['project_dir']}")
        print(f"evidence_log={out['evidence_log']}")
        if transcript_path:
            print(f"hands_transcript={transcript_path}")
        if out["mi_input"].strip():
            print("\nmi_input:\n" + out["mi_input"].strip())
        if out["codex_last_message"].strip():
            # Legacy key name in JSON output: "codex_last_message" means Hands last message.
            print("\nhands_last_message:\n" + out["codex_last_message"].strip())
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
            if na == "send_to_codex":
                nxt = str(decide_next_out.get("next_codex_input") or "").strip()
                if nxt:
                    print("\nnext_hands_input (planned):\n" + nxt)
            if na == "ask_user":
                q = str(decide_next_out.get("ask_user_question") or "").strip()
                if q:
                    print("\nask_user_question:\n" + q)
        mts = out.get("mind_transcripts")
        if isinstance(mts, list) and mts:
            # Keep this short; `mi last --json` is the main interface for pointers.
            print("\nmind_transcripts:")
            for it in mts[:12]:
                if not isinstance(it, dict):
                    continue
                k = str(it.get("kind") or "").strip()
                ref = str(it.get("mind_transcript_ref") or "").strip()
                if k and ref:
                    print(f"- {k}: {ref}")

        ls = out.get("learn_suggested")
        if isinstance(ls, list) and ls:
            print("\nlearn_suggested:")
            for rec in ls[:12]:
                if not isinstance(rec, dict):
                    continue
                sid = str(rec.get("id") or "").strip()
                auto = bool(rec.get("auto_learn", True))
                applied_ids = rec.get("applied_entry_ids") if isinstance(rec.get("applied_entry_ids"), list) else []
                summary = summarize_evidence_record(rec)
                if sid and (not auto) and (not applied_ids):
                    summary = summary + f" (apply: mi learned apply-suggested {sid} --cd {project_root})"
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

    if args.cmd == "evidence":
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        if args.evidence_cmd == "tail":
            if args.raw:
                for line in tail_raw_lines(pp.evidence_log_path, args.lines):
                    print(redact_text(line) if args.redact else line)
                return 0
            for obj in tail_json_objects(pp.evidence_log_path, args.lines):
                s = summarize_evidence_record(obj)
                print(redact_text(s) if args.redact else s)
            return 0

    if args.cmd == "transcript":
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        if args.tr_cmd == "show":
            if args.path:
                tp = Path(args.path).expanduser()
            else:
                subdir = "mind" if args.mind else "hands"
                tdir = pp.transcripts_dir / subdir
                files = sorted([p for p in tdir.glob("*.jsonl") if p.is_file()])
                tp = files[-1] if files else Path("")
            if not tp or not str(tp):
                print("No transcript found.", file=sys.stderr)
                return 2
            if not tp.exists():
                print(f"Transcript not found: {tp}", file=sys.stderr)
                return 2

            real_tp = resolve_transcript_path(tp)
            lines = tail_transcript_lines(tp, args.lines)
            print(str(tp))
            if real_tp != tp:
                print(f"(archived -> {real_tp})")
            if args.jsonl:
                for line in lines:
                    print(redact_text(line) if args.redact else line)
                return 0

            for line in lines:
                try:
                    rec = json.loads(line)
                except Exception:
                    print(line)
                    continue
                if not isinstance(rec, dict):
                    print(line)
                    continue
                ts = str(rec.get("ts") or "")
                stream = str(rec.get("stream") or "")
                payload = rec.get("line")
                payload_s = str(payload) if payload is not None else ""
                if args.redact:
                    payload_s = redact_text(payload_s)
                print(f"{ts} {stream} {payload_s}")
            return 0

    if args.cmd == "memory":
        if args.mem_cmd == "index":
            index = MemoryIndex(store.home_dir)
            if args.mi_cmd == "status":
                st = index.status()
                if args.json:
                    print(json.dumps(st, indent=2, sort_keys=True))
                    return 0
                if not bool(st.get("exists", False)):
                    print(f"memory index: (missing) {st.get('db_path')}")
                    return 0
                print(f"memory index: {st.get('db_path')}")
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
                res = rebuild_memory_index(home_dir=store.home_dir, include_snapshots=not bool(args.no_snapshots))
                if args.json:
                    print(json.dumps(res, indent=2, sort_keys=True))
                    return 0
                print(f"rebuilt: {bool(res.get('rebuilt', False))}")
                print(f"db_path: {res.get('db_path')}")
                print(f"fts_version: {res.get('fts_version')}")
                print(f"total_items: {res.get('total_items')}")
                if "indexed_snapshots" in res:
                    print(f"indexed_snapshots: {res.get('indexed_snapshots')}")
                return 0

    if args.cmd == "project":
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        overlay = store.load_project_overlay(project_root)

        identity_key = str(overlay.get("identity_key") or "").strip()
        idx_path = project_index_path(store.home_dir)
        idx_mapped = ""
        if identity_key:
            try:
                idx_obj = json.loads(idx_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                idx_obj = None
            except Exception:
                idx_obj = None
            if isinstance(idx_obj, dict):
                by_id = idx_obj.get("by_identity")
                if isinstance(by_id, dict):
                    idx_mapped = str(by_id.get(identity_key) or "").strip()
                else:
                    idx_mapped = str(idx_obj.get(identity_key) or "").strip()

        out = {
            "project_root": str(project_root),
            "project_id": pp.project_id,
            "project_dir": str(pp.project_dir),
            "overlay_path": str(pp.overlay_path),
            "identity_key": identity_key,
            "project_index_path": str(idx_path),
            "project_index_mapped_id": idx_mapped,
            "evidence_log": str(pp.evidence_log_path),
            "learned_path": str(pp.learned_path),
            "transcripts_dir": str(pp.transcripts_dir),
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
        if idx_mapped:
            print(f"index_mapped_project_id={idx_mapped}")
        print(f"evidence_log={out['evidence_log']}")
        print(f"learned_path={out['learned_path']}")
        print(f"transcripts_dir={out['transcripts_dir']}")
        return 0

    if args.cmd == "gc":
        if args.gc_cmd == "transcripts":
            project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
            pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
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

    if args.cmd == "workflow":
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        loaded2 = store.load(project_root)
        overlay2 = loaded2.project_overlay if isinstance(loaded2.project_overlay, dict) else {}
        if not isinstance(overlay2, dict):
            overlay2 = {}

        wf_store = WorkflowStore(pp)
        wf_global = GlobalWorkflowStore(GlobalPaths(home_dir=store.home_dir))
        wf_reg = WorkflowRegistry(project_store=wf_store, global_store=wf_global)

        def _effective_enabled_workflows() -> list[dict[str, Any]]:
            eff = wf_reg.enabled_workflows_effective(overlay=overlay2)
            # Internal markers should not leak into derived artifacts.
            return [{k: v for k, v in w.items() if k != "_mi_scope"} for w in eff if isinstance(w, dict)]

        def _auto_sync_hosts() -> None:
            wf_cfg = loaded2.base.get("workflows") if isinstance(loaded2.base.get("workflows"), dict) else {}
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
                store.write_project_overlay(project_root, overlay2)
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
                    store.write_project_overlay(project_root, overlay2)
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
                prompt = edit_workflow_prompt(
                    mindspec_base=loaded2.base,
                    learned_text=loaded2.learned_text,
                    project_overlay=overlay2,
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
                    store.write_project_overlay(project_root, overlay2)
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
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        loaded2 = store.load(project_root)
        overlay2 = loaded2.project_overlay if isinstance(loaded2.project_overlay, dict) else {}
        if not isinstance(overlay2, dict):
            overlay2 = {}
        hb = overlay2.get("host_bindings")
        bindings = hb if isinstance(hb, list) else []

        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        wf_store = WorkflowStore(pp)
        wf_global = GlobalWorkflowStore(GlobalPaths(home_dir=store.home_dir))
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
            store.write_project_overlay(project_root, overlay2)

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
            store.write_project_overlay(project_root, overlay2)

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

    if args.cmd == "learned":
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        if args.learned_cmd == "list":
            entries = store.list_learned_entries(project_root)
            if not entries:
                print("(no learned entries)")
                return 0
            for e in entries:
                src = e.get("_source")
                entry_id = e.get("id")
                action = e.get("action") or "add"
                text = e.get("text") or ""
                print(f"{src} {entry_id} {action} {text}".strip())
            return 0
        if args.learned_cmd == "disable":
            store.disable_learned(project_root=project_root, scope=args.scope, target_id=args.id, rationale=args.rationale)
            print(f"Disabled {args.id} (scope={args.scope}) for project={project_root}")
            return 0
        if args.learned_cmd == "apply-suggested":
            sug_id = str(args.suggestion_id or "").strip()
            if not sug_id:
                print("missing suggestion_id", file=sys.stderr)
                return 2

            pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)

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
            applied_ids0 = suggestion.get("applied_entry_ids")
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

            if already_applied and not bool(args.force):
                print(f"Suggestion already applied: {sug_id}")
                return 0

            changes = suggestion.get("learned_changes") if isinstance(suggestion.get("learned_changes"), list) else []
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
                    }
                )

            if not normalized:
                print(f"(no applicable learned changes in suggestion {sug_id})")
                return 0

            if bool(args.dry_run):
                print(json.dumps({"suggestion_id": sug_id, "changes": normalized}, indent=2, sort_keys=True))
                return 0

            extra = str(args.extra_rationale or "").strip()
            applied_entry_ids: list[str] = []
            for item in normalized:
                base_r = (item.get("rationale") or "").strip() or "manual_apply"
                r = f"{base_r} (apply_suggestion={sug_id})"
                if extra:
                    r = f"{r}; {extra}"
                applied_entry_ids.append(
                    store.append_learned(
                        project_root=project_root,
                        scope=item["scope"],
                        text=item["text"],
                        rationale=r,
                    )
                )

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            evw.append(
                {
                    "kind": "learn_applied",
                    "ts": now_rfc3339(),
                    "suggestion_id": sug_id,
                    "batch_id": str(suggestion.get("batch_id") or ""),
                    "thread_id": str(suggestion.get("thread_id") or ""),
                    "applied_entry_ids": applied_entry_ids,
                }
            )
            print(f"Applied suggestion {sug_id}: {len(applied_entry_ids)} learned entries")
            for entry_id in applied_entry_ids:
                print(entry_id)
            return 0

    return 2
