from __future__ import annotations

import argparse
import os
from pathlib import Path

from .core.config import load_config
from .core.paths import default_home_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mi",
        description="Mind Incarnation (MI) V1: a values-driven mind layer above execution agents (default Hands: Codex CLI).",
    )
    parser.add_argument(
        "--home",
        default=os.environ.get("MI_HOME"),
        help="MI home directory (defaults to $MI_HOME or ~/.mind-incarnation).",
    )
    parser.add_argument(
        "-C",
        "--cd",
        dest="global_cd",
        default="",
        help="Default project root for project-scoped commands (supports @last/@pinned/@alias). Must appear before subcommand; subcommand --cd overrides.",
    )
    parser.add_argument(
        "--here",
        action="store_true",
        help="Force project root to the current working directory (useful for monorepo subdirs). Ignored if --cd/-C is provided. Must appear before subcommand.",
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

    p_init = sub.add_parser("init", help="Initialize global values/preferences (canonical: Thought DB).")
    p_init.add_argument(
        "--values",
        help="Values/preferences prompt text. If omitted or '-', read from stdin.",
        default="-",
    )
    p_init.add_argument(
        "--no-compile",
        action="store_true",
        help="Do not call the model; record values_set + raw values only (no derived values claims).",
    )
    p_init.add_argument(
        "--no-values-claims",
        action="store_true",
        help="Skip migrating values/preferences into global Thought DB preference/goal Claims.",
    )
    p_init.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the compiled values structure but do not write Thought DB.",
    )
    p_init.add_argument(
        "--show",
        action="store_true",
        help="Print the compiled values summary and decision procedure.",
    )

    p_values = sub.add_parser("values", help="Manage canonical values/preferences in Thought DB.")
    values_sub = p_values.add_subparsers(dest="values_cmd", required=True)
    p_vs = values_sub.add_parser("set", help="Set/update global values (writes values_set + raw claim; optional derived claims).")
    p_vs.add_argument(
        "--text",
        default="-",
        help="Values/preferences prompt text. If omitted or '-', read from stdin.",
    )
    p_vs.add_argument(
        "--no-compile",
        action="store_true",
        help="Do not call the model; record values_set + raw values only (no derived values claims).",
    )
    p_vs.add_argument(
        "--no-values-claims",
        action="store_true",
        help="Skip deriving values:base claims (still records raw values).",
    )
    p_vs.add_argument(
        "--show",
        action="store_true",
        help="Print the compiled values summary and decision procedure.",
    )
    p_vshow = values_sub.add_parser("show", help="Show the latest raw values + derived values claims.")
    p_vshow.add_argument("--json", action="store_true", help="Print as JSON.")

    p_settings = sub.add_parser("settings", help="Manage MI operational settings (canonical: Thought DB claims).")
    settings_sub = p_settings.add_subparsers(dest="settings_cmd", required=True)
    p_sshow = settings_sub.add_parser("show", help="Show resolved operational settings (project overrides global).")
    p_sshow.add_argument("--cd", default="", help="Project root used to resolve project overrides.")
    p_sshow.add_argument("--json", action="store_true", help="Print as JSON.")
    p_sset = settings_sub.add_parser("set", help="Set operational settings as canonical Thought DB claims.")
    p_sset.add_argument("--cd", default="", help="Project root used for project-scoped overrides.")
    p_sset.add_argument("--scope", choices=["global", "project"], default="global", help="Where to write the setting claims.")
    p_sset.add_argument(
        "--ask-when-uncertain",
        choices=["ask", "proceed"],
        default="",
        help="Default when MI is uncertain (canonical setting claim).",
    )
    p_sset.add_argument(
        "--refactor-intent",
        choices=["behavior_preserving", "behavior_changing"],
        default="",
        help="Default refactor intent (canonical setting claim).",
    )
    p_sset.add_argument("--dry-run", action="store_true", help="Show what would be written without writing.")

    p_run = sub.add_parser("run", help="Run MI batch autopilot (Hands configured via mi config).")
    p_run.add_argument("task", nargs="+", help="User task for Hands to execute (multi-word; quotes optional).")
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
        "--quiet",
        action="store_true",
        help="Suppress live output and the end summary (useful for scripts/CI).",
    )
    p_run.add_argument(
        "--hands-raw",
        action="store_true",
        help="Print raw Hands stdout/stderr as captured (Codex: JSON event lines) instead of rendered output.",
    )
    p_run.add_argument(
        "--no-mi-prompt",
        action="store_true",
        help="Do not print the full MI->Hands prompt (still persisted to EvidenceLog).",
    )
    p_run.add_argument(
        "--redact",
        action="store_true",
        help="Best-effort redact common secret/token patterns in live display output (stored logs remain unchanged).",
    )
    p_run.add_argument(
        "--why",
        action="store_true",
        help="Opt-in: run one WhyTrace at run end (writes kind=why_trace; may materialize depends_on edges).",
    )

    p_status = sub.add_parser("status", help="Show everyday status for the current project (read-only).")
    p_status.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_status.add_argument("--json", action="store_true", help="Print as JSON.")
    p_status.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_show = sub.add_parser(
        "show",
        help="Show an MI resource by id (ev_/cl_/nd_/wf_/ed_) or a transcript .jsonl path (best-effort).",
    )
    p_show.add_argument("ref", help="Resource id (ev_/cl_/nd_/wf_/ed_) or transcript path.")
    p_show.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_show.add_argument(
        "--global",
        dest="show_global",
        action="store_true",
        help="For ev_... refs: search global EvidenceLog only (skip project fallback).",
    )
    p_show.add_argument("-n", "--lines", type=int, default=200, help="Number of transcript lines to show when ref is a .jsonl path.")
    p_show.add_argument("--jsonl", action="store_true", help="When showing a transcript path: print raw JSONL lines.")
    p_show.add_argument("--json", action="store_true", help="Print as JSON when possible.")
    p_show.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_ls = sub.add_parser("ls", help="List MI resources (front-door aliases).")
    ls_sub = p_ls.add_subparsers(dest="ls_kind", required=True)

    p_ls_claims = ls_sub.add_parser("claims", help="Alias for: mi claim list")
    p_ls_claims.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ls_claims.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to list.")
    p_ls_claims.add_argument("--all", action="store_true", help="Include superseded/retracted and alias claims.")
    p_ls_claims.add_argument("--tag", action="append", default=[], help="Filter by tag (repeatable).")
    p_ls_claims.add_argument("--contains", default="", help="Case-insensitive substring filter over claim text.")
    p_ls_claims.add_argument(
        "--type",
        dest="claim_type",
        action="append",
        default=[],
        help="Filter by claim_type (fact/preference/assumption/goal). Repeatable.",
    )
    p_ls_claims.add_argument(
        "--status",
        action="append",
        default=[],
        choices=["active", "superseded", "retracted"],
        help="Filter by derived status (repeatable).",
    )
    p_ls_claims.add_argument("--as-of", default="", help="RFC3339 as-of timestamp (filters valid_from/valid_to; defaults to now).")
    p_ls_claims.add_argument("--limit", type=int, default=0, help="Limit number of results (0 means no limit).")
    p_ls_claims.add_argument("--json", action="store_true", help="Print as JSON.")

    p_ls_nodes = ls_sub.add_parser("nodes", help="Alias for: mi node list")
    p_ls_nodes.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ls_nodes.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to list.")
    p_ls_nodes.add_argument("--all", action="store_true", help="Include superseded/retracted and alias nodes.")
    p_ls_nodes.add_argument("--tag", action="append", default=[], help="Filter by tag (repeatable).")
    p_ls_nodes.add_argument("--contains", default="", help="Case-insensitive substring filter over node title/text.")
    p_ls_nodes.add_argument("--type", dest="node_type", action="append", default=[], help="Filter by node_type (decision/action/summary). Repeatable.")
    p_ls_nodes.add_argument(
        "--status",
        action="append",
        default=[],
        choices=["active", "superseded", "retracted"],
        help="Filter by derived status (repeatable).",
    )
    p_ls_nodes.add_argument("--limit", type=int, default=0, help="Limit number of results (0 means no limit).")
    p_ls_nodes.add_argument("--json", action="store_true", help="Print as JSON.")

    p_ls_edges = ls_sub.add_parser("edges", help="Alias for: mi edge list")
    p_ls_edges.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ls_edges.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to list.")
    p_ls_edges.add_argument("--type", dest="edge_type", default="", help="Filter by edge_type (depends_on/supports/...).")
    p_ls_edges.add_argument("--from", dest="from_id", default="", help="Filter by from_id.")
    p_ls_edges.add_argument("--to", dest="to_id", default="", help="Filter by to_id.")
    p_ls_edges.add_argument("--limit", type=int, default=50, help="Maximum number of edges to print.")
    p_ls_edges.add_argument("--json", action="store_true", help="Print as JSON.")

    p_ls_workflows = ls_sub.add_parser("workflows", help="Alias for: mi workflow list")
    p_ls_workflows.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ls_workflows.add_argument(
        "--scope",
        choices=["project", "global", "effective"],
        default="project",
        help="Which store to list (effective merges project+global with project precedence).",
    )

    p_edit = sub.add_parser("edit", help="Edit an MI resource by id (V1: workflows only).")
    p_edit.add_argument("ref", help="Resource id (wf_...).")
    p_edit.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_edit.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to edit.")
    p_edit.add_argument(
        "--project-override",
        action="store_true",
        help="When scope=global, write a per-project override patch instead of editing the global workflow file.",
    )
    p_edit.add_argument(
        "--request",
        default="-",
        help="Edit request text. If omitted or '-', read a single line from stdin.",
    )
    p_edit.add_argument("--loop", action="store_true", help="After applying, prompt for more edits until blank.")
    p_edit.add_argument("--dry-run", action="store_true", help="Show proposed edits but do not write.")

    p_claim = sub.add_parser("claim", help="Manage Thought DB claims (atomic reusable arguments).")
    claim_sub = p_claim.add_subparsers(dest="claim_cmd", required=True)

    p_cll = claim_sub.add_parser("list", help="List claims (default: active + canonical).")
    p_cll.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_cll.add_argument("--scope", choices=["project", "global", "effective"], default="project", help="Which store to list.")
    p_cll.add_argument("--all", action="store_true", help="Include superseded/retracted and alias claims.")
    p_cll.add_argument("--tag", action="append", default=[], help="Filter by tag (repeatable).")
    p_cll.add_argument("--contains", default="", help="Case-insensitive substring filter over claim text.")
    p_cll.add_argument(
        "--type",
        dest="claim_type",
        action="append",
        default=[],
        help="Filter by claim_type (fact/preference/assumption/goal). Repeatable.",
    )
    p_cll.add_argument(
        "--status",
        action="append",
        default=[],
        choices=["active", "superseded", "retracted"],
        help="Filter by derived status (repeatable).",
    )
    p_cll.add_argument("--as-of", default="", help="RFC3339 as-of timestamp (filters valid_from/valid_to; defaults to now).")
    p_cll.add_argument("--limit", type=int, default=0, help="Limit number of results (0 means no limit).")
    p_cll.add_argument("--json", action="store_true", help="Print as JSON.")

    p_cls = claim_sub.add_parser("show", help="Show a claim by id.")
    p_cls.add_argument("id", help="Claim id (cl_...).")
    p_cls.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_cls.add_argument("--scope", choices=["project", "global", "effective"], default="effective", help="Where to resolve the id.")
    p_cls.add_argument("--graph", action="store_true", help="Include a subgraph (JSON-only).")
    p_cls.add_argument("--depth", type=int, default=1, help="Subgraph depth (0..6).")
    p_cls.add_argument("--direction", choices=["out", "in", "both"], default="both", help="Subgraph traversal direction.")
    p_cls.add_argument("--edge-type", action="append", default=[], dest="edge_types", help="Filter subgraph by edge_type (repeatable).")
    p_cls.add_argument("--include-inactive", action="store_true", help="Include superseded/retracted items in subgraph.")
    p_cls.add_argument("--include-aliases", action="store_true", help="Include same_as alias ids in subgraph.")
    p_cls.add_argument("--json", action="store_true", help="Print as JSON.")

    p_clm = claim_sub.add_parser("mine", help="On-demand mine claims from the current segment buffer (best-effort).")
    p_clm.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_clm.add_argument("--min-confidence", type=float, default=-1.0, help="Override config.runtime.thought_db.min_confidence.")
    p_clm.add_argument("--max-claims", type=int, default=-1, help="Override config.runtime.thought_db.max_claims_per_checkpoint.")
    p_clm.add_argument("--json", action="store_true", help="Print result as JSON.")

    p_cas = claim_sub.add_parser(
        "apply-suggested",
        help="Apply a previously suggested preference tightening from EvidenceLog (append-only).",
    )
    p_cas.add_argument("suggestion_id", help="Suggestion id from EvidenceLog record kind=learn_suggested.")
    p_cas.add_argument("--cd", default="", help="Project root used to locate EvidenceLog and Thought DB storage.")
    p_cas.add_argument("--dry-run", action="store_true", help="Show what would be applied without writing.")
    p_cas.add_argument("--force", action="store_true", help="Apply even if the suggestion looks already applied.")
    p_cas.add_argument(
        "--extra-rationale",
        default="",
        help="Optional extra rationale to append to the applied claims (for audit).",
    )

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
    p_nl.add_argument("--tag", action="append", default=[], help="Filter by tag (repeatable).")
    p_nl.add_argument("--contains", default="", help="Case-insensitive substring filter over node title/text.")
    p_nl.add_argument("--type", dest="node_type", action="append", default=[], help="Filter by node_type (decision/action/summary). Repeatable.")
    p_nl.add_argument(
        "--status",
        action="append",
        default=[],
        choices=["active", "superseded", "retracted"],
        help="Filter by derived status (repeatable).",
    )
    p_nl.add_argument("--limit", type=int, default=0, help="Limit number of results (0 means no limit).")
    p_nl.add_argument("--json", action="store_true", help="Print as JSON.")

    p_ns = node_sub.add_parser("show", help="Show a node by id.")
    p_ns.add_argument("id", help="Node id (nd_...).")
    p_ns.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ns.add_argument("--scope", choices=["project", "global", "effective"], default="effective", help="Where to resolve the id.")
    p_ns.add_argument("--graph", action="store_true", help="Include a subgraph (JSON-only).")
    p_ns.add_argument("--depth", type=int, default=1, help="Subgraph depth (0..6).")
    p_ns.add_argument("--direction", choices=["out", "in", "both"], default="both", help="Subgraph traversal direction.")
    p_ns.add_argument("--edge-type", action="append", default=[], dest="edge_types", help="Filter subgraph by edge_type (repeatable).")
    p_ns.add_argument("--include-inactive", action="store_true", help="Include superseded/retracted items in subgraph.")
    p_ns.add_argument("--include-aliases", action="store_true", help="Include same_as alias ids in subgraph.")
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

    p_ev_show = ev_sub.add_parser("show", help="Show an EvidenceLog record by event_id.")
    p_ev_show.add_argument("event_id", help="EvidenceLog event_id (ev_...).")
    p_ev_show.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ev_show.add_argument("--global", dest="ev_global", action="store_true", help="Search the global EvidenceLog instead of the project one.")
    p_ev_show.add_argument("--json", action="store_true", help="Print as JSON.")
    p_ev_show.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

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
    p_mir = mi_sub.add_parser("rebuild", help="Rebuild memory index from MI stores (workflows + Thought DB + snapshots).")
    p_mir.add_argument("--no-snapshots", action="store_true", help="Skip indexing snapshot records from EvidenceLog.")
    p_mir.add_argument("--json", action="store_true", help="Print rebuild result as JSON.")

    p_proj = sub.add_parser("project", help="Inspect per-project MI state (overlay + resolved paths).")
    proj_sub = p_proj.add_subparsers(dest="project_cmd", required=True)
    p_ps = proj_sub.add_parser("show", help="Show the project overlay and resolved storage paths.")
    p_ps.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ps.add_argument("--json", action="store_true", help="Print as JSON.")
    p_ps.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")
    p_pst = proj_sub.add_parser("status", help="Show how MI resolves the project root (read-only; no side effects).")
    p_pst.add_argument("--cd", default="", help="Project root used to locate MI artifacts (supports @last/@pinned/@alias).")
    p_pst.add_argument("--json", action="store_true", help="Print as JSON.")
    p_ppin = proj_sub.add_parser("pin", help="Pin a project for quick selection (@pinned).")
    p_ppin.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_ppin.add_argument("--json", action="store_true", help="Print as JSON.")
    p_punpin = proj_sub.add_parser("unpin", help="Clear the pinned project selection (@pinned).")
    p_punpin.add_argument("--json", action="store_true", help="Print as JSON.")
    p_puse = proj_sub.add_parser("use", help="Set the last-used project selection (@last).")
    p_puse.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_puse.add_argument("--json", action="store_true", help="Print as JSON.")

    p_palias = proj_sub.add_parser("alias", help="Manage project root aliases (usable via --cd @alias).")
    alias_sub = p_palias.add_subparsers(dest="alias_cmd", required=True)
    p_paa = alias_sub.add_parser("add", help="Add or update an alias to a project root.")
    p_paa.add_argument("name", help="Alias name (e.g., repo1).")
    p_paa.add_argument("--cd", default="", help="Project root to bind the alias to.")
    p_paa.add_argument("--json", action="store_true", help="Print as JSON.")
    p_par = alias_sub.add_parser("rm", help="Remove an alias.")
    p_par.add_argument("name", help="Alias name to remove.")
    p_par.add_argument("--json", action="store_true", help="Print as JSON.")
    p_pal = alias_sub.add_parser("list", help="List aliases.")
    p_pal.add_argument("--json", action="store_true", help="Print as JSON.")

    p_gc = sub.add_parser("gc", help="Garbage collect / archive MI artifacts (optional).")
    gc_sub = p_gc.add_subparsers(dest="gc_cmd", required=True)
    p_gct = gc_sub.add_parser("transcripts", help="Archive older transcripts to reduce disk usage (safe, reversible).")
    p_gct.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_gct.add_argument("--keep-hands", type=int, default=50, help="Keep N most recent raw Hands transcripts.")
    p_gct.add_argument("--keep-mind", type=int, default=200, help="Keep N most recent raw Mind transcripts.")
    p_gct.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run).")
    p_gct.add_argument("--json", action="store_true", help="Print result as JSON.")

    p_gctdb = gc_sub.add_parser(
        "thoughtdb",
        help="Compact Thought DB JSONL files by archiving then rewriting them (safe, reversible).",
    )
    p_gctdb.add_argument("--cd", default="", help="Project root used to locate MI artifacts (unless --global).")
    p_gctdb.add_argument("--global", dest="gc_global", action="store_true", help="Compact the global Thought DB instead of the current project.")
    p_gctdb.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run).")
    p_gctdb.add_argument("--json", action="store_true", help="Print result as JSON.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    home_dir = Path(str(args.home)).expanduser().resolve() if args.home else default_home_dir()
    cfg = load_config(home_dir)

    from .cli_dispatch import dispatch

    return dispatch(args=args, home_dir=home_dir, cfg=cfg)
