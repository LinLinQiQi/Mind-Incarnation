from __future__ import annotations

from typing import Any


def add_thoughtdb_subparsers(*, sub: Any) -> None:
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


__all__ = ["add_thoughtdb_subparsers"]

