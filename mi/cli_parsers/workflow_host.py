from __future__ import annotations

from typing import Any


def add_workflow_host_subparsers(*, sub: Any) -> None:
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


__all__ = ["add_workflow_host_subparsers"]

