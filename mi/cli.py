import argparse
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any

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
from .mindspec_runtime import sanitize_mindspec_base_for_runtime
from .prompts import compile_mindspec_prompt, edit_workflow_prompt, mine_claims_prompt, values_claim_patch_prompt
from .runner import run_autopilot
from .paths import GlobalPaths, ProjectPaths, default_home_dir, project_index_path, resolve_cli_project_root
from .inspect import load_last_batch_bundle, tail_raw_lines, tail_json_objects, summarize_evidence_record
from .transcript import last_agent_message_from_transcript, tail_transcript_lines, resolve_transcript_path
from .redact import redact_text
from .provider_factory import make_hands_functions, make_mind_provider
from .gc import archive_project_transcripts
from .storage import append_jsonl, iter_jsonl, now_rfc3339
from .thoughtdb import ThoughtDbStore, claim_signature
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
from .memory_ingest import thoughtdb_node_item
from .memory_service import MemoryService
from .evidence import EvidenceWriter, new_run_id
from .values import write_values_set_event, existing_values_claims, apply_values_claim_patch
from .thought_context import build_decide_next_thoughtdb_context
from .why import (
    find_evidence_event,
    query_from_evidence_event,
    collect_candidate_claims,
    run_why_trace,
    default_as_of_ts,
)


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
        "--no-values-claims",
        action="store_true",
        help="Skip migrating values/preferences into global Thought DB preference/goal Claims.",
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

    p_claim = sub.add_parser("claim", help="Manage Thought DB claims (atomic reusable arguments).")
    claim_sub = p_claim.add_subparsers(dest="claim_cmd", required=True)

    p_cll = claim_sub.add_parser("list", help="List claims (default: active + canonical).")
    p_cll.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_cll.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to list.")
    p_cll.add_argument("--all", action="store_true", help="Include superseded/retracted and alias claims.")
    p_cll.add_argument("--json", action="store_true", help="Print as JSON.")

    p_cls = claim_sub.add_parser("show", help="Show a claim by id.")
    p_cls.add_argument("id", help="Claim id (cl_...).")
    p_cls.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_cls.add_argument("--scope", choices=["project", "global", "effective"], default="effective", help="Where to resolve the id.")
    p_cls.add_argument("--json", action="store_true", help="Print as JSON.")

    p_clm = claim_sub.add_parser("mine", help="On-demand mine claims from the current segment buffer (best-effort).")
    p_clm.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_clm.add_argument("--min-confidence", type=float, default=-1.0, help="Override MindSpec.thought_db.min_confidence.")
    p_clm.add_argument("--max-claims", type=int, default=-1, help="Override MindSpec.thought_db.max_claims_per_checkpoint.")
    p_clm.add_argument("--json", action="store_true", help="Print result as JSON.")

    p_clr = claim_sub.add_parser("retract", help="Retract a claim (append-only).")
    p_clr.add_argument("id", help="Claim id to retract.")
    p_clr.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_clr.add_argument("--scope", choices=["project", "global"], default="project", help="Which store to write to.")
    p_clr.add_argument("--rationale", default="user retract", help="Reason recorded for audit.")

    p_clsup = claim_sub.add_parser("supersede", help="Supersede a claim by creating a replacement and linking supersedes(old->new).")
    p_clsup.add_argument("id", help="Old claim id to supersede.")
    p_clsup.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_clsup.add_argument("--scope", choices=["project", "global", "effective"], default="effective", help="Where to resolve the old id.")
    p_clsup.add_argument("--text", required=True, help="New claim text.")
    p_clsup.add_argument("--claim-type", choices=["fact", "preference", "assumption", "goal"], default="", help="New claim type (defaults to old).")
    p_clsup.add_argument("--visibility", choices=["private", "project", "global"], default="", help="New claim visibility (defaults to old).")
    p_clsup.add_argument("--valid-from", default="", help="Optional RFC3339 valid_from.")
    p_clsup.add_argument("--valid-to", default="", help="Optional RFC3339 valid_to.")
    p_clsup.add_argument("--tag", action="append", default=[], help="Tag to attach (repeatable).")

    p_clsa = claim_sub.add_parser("same-as", help="Mark two claims equivalent via same_as(dup->canonical) (append-only).")
    p_clsa.add_argument("dup_id", help="Duplicate claim id.")
    p_clsa.add_argument("canonical_id", help="Canonical claim id.")
    p_clsa.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_clsa.add_argument("--scope", choices=["project", "global"], default="project", help="Which store to write to.")
    p_clsa.add_argument("--notes", default="", help="Optional notes for audit.")

    p_node = sub.add_parser("node", help="Manage Thought DB nodes (Decision/Action/Summary).")
    node_sub = p_node.add_subparsers(dest="node_cmd", required=True)

    p_nl = node_sub.add_parser("list", help="List nodes (default: active + canonical).")
    p_nl.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_nl.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to list.")
    p_nl.add_argument("--all", action="store_true", help="Include superseded/retracted and alias nodes.")
    p_nl.add_argument("--json", action="store_true", help="Print as JSON.")

    p_ns = node_sub.add_parser("show", help="Show a node by id.")
    p_ns.add_argument("id", help="Node id (nd_...).")
    p_ns.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ns.add_argument("--scope", choices=["project", "global", "effective"], default="effective", help="Where to resolve the id.")
    p_ns.add_argument("--json", action="store_true", help="Print as JSON.")

    p_nc = node_sub.add_parser("create", help="Create a node (append-only).")
    p_nc.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_nc.add_argument("--scope", choices=["project", "global"], default="project", help="Which store to write to.")
    p_nc.add_argument("--type", dest="node_type", choices=["decision", "action", "summary"], required=True, help="Node type.")
    p_nc.add_argument("--title", default="", help="Optional title (defaults to first line of text).")
    p_nc.add_argument("--text", default="-", help="Node text. If omitted or '-', read from stdin.")
    p_nc.add_argument("--visibility", choices=["private", "project", "global"], default="", help="Visibility label (defaults to scope).")
    p_nc.add_argument("--tag", action="append", default=[], help="Tag to attach (repeatable).")
    p_nc.add_argument("--cite", action="append", default=[], help="Extra EvidenceLog event_id to cite (repeatable).")
    p_nc.add_argument("--confidence", type=float, default=1.0, help="Confidence 0..1 (best-effort).")
    p_nc.add_argument("--notes", default="", help="Optional notes for audit.")
    p_nc.add_argument("--json", action="store_true", help="Print as JSON.")

    p_nr = node_sub.add_parser("retract", help="Retract a node (append-only).")
    p_nr.add_argument("id", help="Node id to retract.")
    p_nr.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_nr.add_argument("--scope", choices=["project", "global"], default="project", help="Which store to write to.")
    p_nr.add_argument("--rationale", default="user retract", help="Reason recorded for audit.")

    p_edge = sub.add_parser("edge", help="Manage Thought DB edges (dependencies + evolution).")
    edge_sub = p_edge.add_subparsers(dest="edge_cmd", required=True)

    p_ec = edge_sub.add_parser("create", help="Create an edge (append-only).")
    p_ec.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ec.add_argument("--scope", choices=["project", "global"], default="project", help="Which store to write to.")
    p_ec.add_argument(
        "--type",
        dest="edge_type",
        choices=["depends_on", "supports", "contradicts", "derived_from", "mentions", "supersedes", "same_as"],
        required=True,
        help="Edge type.",
    )
    p_ec.add_argument("--from", dest="from_id", required=True, help="Edge from_id (claim_id/node_id/event_id).")
    p_ec.add_argument("--to", dest="to_id", required=True, help="Edge to_id (claim_id/node_id/event_id).")
    p_ec.add_argument("--visibility", choices=["private", "project", "global"], default="", help="Visibility label (defaults to scope).")
    p_ec.add_argument("--notes", default="", help="Optional notes for audit.")
    p_ec.add_argument("--json", action="store_true", help="Print as JSON.")

    p_el = edge_sub.add_parser("list", help="List edges (default: project scope).")
    p_el.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_el.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to list.")
    p_el.add_argument("--type", dest="edge_type", default="", help="Filter by edge_type (depends_on/supports/...).")
    p_el.add_argument("--from", dest="from_id", default="", help="Filter by from_id.")
    p_el.add_argument("--to", dest="to_id", default="", help="Filter by to_id.")
    p_el.add_argument("--limit", type=int, default=50, help="Maximum number of edges to print.")
    p_el.add_argument("--json", action="store_true", help="Print as JSON.")

    p_es = edge_sub.add_parser("show", help="Show an edge by id.")
    p_es.add_argument("id", help="Edge id (ed_...).")
    p_es.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_es.add_argument("--scope", choices=["project", "global", "effective"], default="effective", help="Where to resolve the id.")
    p_es.add_argument("--json", action="store_true", help="Print as JSON.")

    p_why = sub.add_parser("why", help="Root-cause tracing (WhyTrace) using Thought DB claims.")
    why_sub = p_why.add_subparsers(dest="why_cmd", required=True)

    p_wyl = why_sub.add_parser("last", help="Generate a WhyTrace for the latest batch decision/evidence.")
    p_wyl.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wyl.add_argument("--top-k", type=int, default=12, help="Number of candidate claims to consider.")
    p_wyl.add_argument("--as-of", default="", help="RFC3339 as-of timestamp (defaults to now).")
    p_wyl.add_argument("--json", action="store_true", help="Print as JSON.")

    p_wye = why_sub.add_parser("event", help="Generate a WhyTrace for an EvidenceLog event_id.")
    p_wye.add_argument("event_id", help="EvidenceLog event_id (ev_...).")
    p_wye.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wye.add_argument("--top-k", type=int, default=12, help="Number of candidate claims to consider.")
    p_wye.add_argument("--as-of", default="", help="RFC3339 as-of timestamp (defaults to now).")
    p_wye.add_argument("--json", action="store_true", help="Print as JSON.")

    p_wyc = why_sub.add_parser("claim", help="Generate a WhyTrace for a claim id.")
    p_wyc.add_argument("claim_id", help="Claim id (cl_...).")
    p_wyc.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_wyc.add_argument("--scope", choices=["project", "global", "effective"], default="effective", help="Where to resolve the claim id.")
    p_wyc.add_argument("--top-k", type=int, default=12, help="Number of candidate claims to consider.")
    p_wyc.add_argument("--as-of", default="", help="RFC3339 as-of timestamp (defaults to now).")
    p_wyc.add_argument("--json", action="store_true", help="Print as JSON.")

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

        llm = None
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

        # Record values changes into a global EvidenceLog so later Claims can cite stable event_id provenance.
        base_after = store.load_base()
        values_ev = write_values_set_event(
            home_dir=store.home_dir,
            values_text=values,
            compiled_mindspec=base_after,
            notes="mi init",
        )
        values_event_id = str(values_ev.get("event_id") or "").strip()
        if values_event_id:
            print(f"[mi] recorded global values_set event_id={values_event_id}", file=sys.stderr)
        else:
            print("[mi] warning: failed to record global values_set event_id; skipping values->claims migration", file=sys.stderr)
            return 0

        # Migrate values into global Thought DB preference/goal claims (best-effort).
        if args.no_compile:
            print("[mi] values->claims skipped (--no-compile)", file=sys.stderr)
            return 0
        if bool(args.no_values_claims):
            print("[mi] values->claims skipped (--no-values-claims)", file=sys.stderr)
            return 0
        if llm is None:
            print("[mi] values->claims skipped (mind provider unavailable)", file=sys.stderr)
            return 0

        try:
            # Use a dummy ProjectPaths id to avoid accidentally creating a project mapping during global init.
            dummy_pp = ProjectPaths(home_dir=store.home_dir, project_root=Path("."), _project_id="__global__")
            tdb = ThoughtDbStore(home_dir=store.home_dir, project_paths=dummy_pp)

            existing = existing_values_claims(tdb=tdb, limit=120)
            retractable_ids = [
                str(c.get("claim_id") or "").strip()
                for c in existing
                if isinstance(c, dict) and str(c.get("claim_id") or "").strip()
            ]

            prompt2 = values_claim_patch_prompt(
                values_text=values,
                compiled_mindspec=base_after,
                existing_values_claims=existing,
                allowed_event_ids=[values_event_id],
                allowed_retract_claim_ids=retractable_ids,
                notes="mi init (values -> Thought DB claims)",
            )
            patch_obj = llm.call(schema_filename="values_claim_patch.json", prompt=prompt2, tag="values_claim_patch").obj

            tcfg = base_after.get("thought_db") if isinstance(base_after.get("thought_db"), dict) else {}
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
                a = applied.applied if isinstance(applied.applied, dict) else {}
                written = a.get("written") if isinstance(a.get("written"), list) else []
                linked = a.get("linked_existing") if isinstance(a.get("linked_existing"), list) else []
                edges = a.get("written_edges") if isinstance(a.get("written_edges"), list) else []
                print(
                    f"[mi] values->claims ok: written={len(written)} linked_existing={len(linked)} edges={len(edges)} retracted={len(applied.retracted)}",
                    file=sys.stderr,
                )
        except Exception as e:
            print(f"[mi] values->claims migration failed: {type(e).__name__}: {e}", file=sys.stderr)

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
                applied_ids = rec.get("applied_claim_ids") if isinstance(rec.get("applied_claim_ids"), list) else []
                if not applied_ids:
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
            mem = MemoryService(store.home_dir)
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
        if idx_mapped:
            print(f"index_mapped_project_id={idx_mapped}")
        print(f"evidence_log={out['evidence_log']}")
        print(f"learned_path={out['learned_path']}")
        print(f"transcripts_dir={out['transcripts_dir']}")
        print(f"thoughtdb_dir={out['thoughtdb_dir']}")
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

    if args.cmd == "claim":
        project_root = _resolve_project_root_from_args(store, str(getattr(args, "cd", "") or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        loaded2 = store.load(project_root)
        overlay2 = loaded2.project_overlay if isinstance(loaded2.project_overlay, dict) else {}
        if not isinstance(overlay2, dict):
            overlay2 = {}

        tdb = ThoughtDbStore(home_dir=store.home_dir, project_paths=pp)

        def _view_for_scope(scope: str) -> object:
            sc = str(scope or "project").strip()
            if sc not in ("project", "global"):
                sc = "project"
            return tdb.load_view(scope=sc)

        def _iter_effective_claims(*, include_inactive: bool, include_aliases: bool) -> list[dict]:
            proj = tdb.load_view(scope="project")
            glob = tdb.load_view(scope="global")
            out: list[dict] = []
            seen: set[str] = set()

            def sig_for(c: dict) -> str:
                ct = str(c.get("claim_type") or "").strip()
                text = str(c.get("text") or "").strip()
                return claim_signature(claim_type=ct, scope="effective", project_id="", text=text)

            for c in proj.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases):
                if not isinstance(c, dict):
                    continue
                s = sig_for(c)
                if s:
                    seen.add(s)
                out.append(c)

            for c in glob.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases):
                if not isinstance(c, dict):
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
            include_inactive = bool(getattr(args, "all", False))
            include_aliases = bool(getattr(args, "all", False))

            if scope == "effective":
                items = _iter_effective_claims(include_inactive=include_inactive, include_aliases=include_aliases)
            else:
                v = tdb.load_view(scope=scope)
                items = list(v.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases))
                items.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)

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
            tcfg = loaded2.base.get("thought_db") if isinstance(loaded2.base.get("thought_db"), dict) else {}
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
                mindspec_base=sanitize_mindspec_base_for_runtime(loaded2.base if isinstance(getattr(loaded2, "base", None), dict) else {}),
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
        project_root = _resolve_project_root_from_args(store, str(getattr(args, "cd", "") or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        tdb = ThoughtDbStore(home_dir=store.home_dir, project_paths=pp)

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
            include_inactive = bool(getattr(args, "all", False))
            include_aliases = bool(getattr(args, "all", False))

            if scope == "effective":
                items = _iter_effective_nodes(include_inactive=include_inactive, include_aliases=include_aliases)
            else:
                v = tdb.load_view(scope=scope)
                items = list(v.iter_nodes(include_inactive=include_inactive, include_aliases=include_aliases))
                items.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)

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
                    GlobalPaths(home_dir=store.home_dir).thoughtdb_global_nodes_path
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
                MemoryService(store.home_dir).upsert_items([it])
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
        project_root = _resolve_project_root_from_args(store, str(getattr(args, "cd", "") or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        tdb = ThoughtDbStore(home_dir=store.home_dir, project_paths=pp)

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
        project_root = _resolve_project_root_from_args(store, str(getattr(args, "cd", "") or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)

        # Providers/stores.
        tdb = ThoughtDbStore(home_dir=store.home_dir, project_paths=pp)
        mem = MemoryService(store.home_dir)
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
                for key in ("decide_next", "evidence_item", "codex_input"):
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
            candidates = collect_candidate_claims(
                tdb=tdb,
                mem=mem,
                project_paths=pp,
                query=query,
                top_k=top_k,
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
        project_root = _resolve_project_root_from_args(store, str(args.cd or ""))
        pp = ProjectPaths(home_dir=store.home_dir, project_root=project_root)
        loaded2 = store.load(project_root)
        overlay2 = loaded2.project_overlay if isinstance(loaded2.project_overlay, dict) else {}
        if not isinstance(overlay2, dict):
            overlay2 = {}

        wf_store = WorkflowStore(pp)
        wf_global = GlobalWorkflowStore(GlobalPaths(home_dir=store.home_dir))
        wf_reg = WorkflowRegistry(project_store=wf_store, global_store=wf_global)
        tdb = ThoughtDbStore(home_dir=store.home_dir, project_paths=pp)

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
                tdb_ctx = build_decide_next_thoughtdb_context(
                    tdb=tdb,
                    as_of_ts=now_rfc3339(),
                    task=req,
                    hands_last_message="",
                    recent_evidence=[],
                )
                tdb_ctx_obj = tdb_ctx.to_prompt_obj()
                prompt = edit_workflow_prompt(
                    mindspec_base=sanitize_mindspec_base_for_runtime(loaded2.base if isinstance(getattr(loaded2, "base", None), dict) else {}),
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
            applied_ids0 = suggestion.get("applied_claim_ids")
            if isinstance(applied_ids0, list) and any(str(x).strip() for x in applied_ids0):
                already_applied = True
            applied_ids1 = suggestion.get("applied_entry_ids")
            if isinstance(applied_ids1, list) and any(str(x).strip() for x in applied_ids1):
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
                        "severity": str(ch.get("severity") or "").strip(),
                    }
                )

            if not normalized:
                print(f"(no applicable learned changes in suggestion {sug_id})")
                return 0

            if bool(args.dry_run):
                print(json.dumps({"suggestion_id": sug_id, "changes": normalized}, indent=2, sort_keys=True))
                return 0

            extra = str(args.extra_rationale or "").strip()
            tdb = ThoughtDbStore(home_dir=store.home_dir, project_paths=pp)
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
                    "applied_entry_ids": [],
                    "applied_claim_ids": applied_claim_ids,
                }
            )
            print(f"Applied suggestion {sug_id}: {len(applied_claim_ids)} preference claims")
            for cid in applied_claim_ids:
                print(cid)
            return 0

    return 2
