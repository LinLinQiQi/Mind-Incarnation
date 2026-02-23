from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..memory.service import MemoryService
from ..memory.text import tokenize_query, truncate
from .store import ThoughtDbStore, ThoughtDbView
from .values import VALUES_BASE_TAG, VALUES_RAW_TAG, VALUES_SUMMARY_TAG
from .pins import PINNED_PREF_GOAL_TAGS
from .retrieval import (
    _claim_active_and_valid as _claim_active_and_valid_view,
    _node_active as _node_active_view,
    expand_one_hop,
    seed_ids_from_memory,
)


def _safe_list_str(items: Any, *, limit: int) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for x in items:
        if len(out) >= limit:
            break
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def _collect_query_text(*, task: str, hands_last_message: str, recent_evidence: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if str(task or "").strip():
        parts.append(str(task).strip())
    if str(hands_last_message or "").strip():
        parts.append(str(hands_last_message).strip())

    # Pull a small amount of signal from evidence (unknowns/risk/facts/results).
    for rec in recent_evidence[-6:]:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("kind") or "").strip() != "evidence":
            continue
        parts.extend(_safe_list_str(rec.get("unknowns"), limit=6))
        parts.extend(_safe_list_str(rec.get("risk_signals"), limit=6))
        parts.extend(_safe_list_str(rec.get("facts"), limit=6))
        parts.extend(_safe_list_str(rec.get("results"), limit=4))

    return "\n".join([p for p in parts if p]).strip()


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _score_tokens(tokens: list[str], *, text: str) -> int:
    if not tokens:
        return 0
    t = _norm(text)
    score = 0
    for tok in tokens:
        if tok and tok in t:
            score += 1
    return score


def _compact_claim(c: dict[str, Any], *, view: ThoughtDbView) -> dict[str, Any]:
    cid = str(c.get("claim_id") or "").strip()
    refs = c.get("source_refs") if isinstance(c.get("source_refs"), list) else []
    ev_ids: list[str] = []
    for r in refs:
        if isinstance(r, dict) and r.get("event_id"):
            ev_ids.append(str(r.get("event_id")))
    ev_ids = [x for x in ev_ids if x.strip()][:6]
    return {
        "claim_id": cid,
        "canonical_id": view.resolve_id(cid),
        "status": view.claim_status(cid),
        "claim_type": str(c.get("claim_type") or "").strip(),
        "scope": str(c.get("scope") or "").strip(),
        "visibility": str(c.get("visibility") or "").strip(),
        "valid_from": c.get("valid_from"),
        "valid_to": c.get("valid_to"),
        "text": truncate(str(c.get("text") or "").strip(), 480),
        "tags": [str(x) for x in (c.get("tags") or []) if str(x).strip()][:16] if isinstance(c.get("tags"), list) else [],
        "source_event_ids": ev_ids,
    }


def _compact_edge(e: dict[str, Any], *, scope: str) -> dict[str, Any]:
    return {
        "edge_type": str(e.get("edge_type") or "").strip(),
        "from_id": str(e.get("from_id") or "").strip(),
        "to_id": str(e.get("to_id") or "").strip(),
        "scope": scope,
        "notes": truncate(str(e.get("notes") or "").strip(), 160),
    }


def _compact_node(n: dict[str, Any], *, view: ThoughtDbView) -> dict[str, Any]:
    nid = str(n.get("node_id") or "").strip()
    refs = n.get("source_refs") if isinstance(n.get("source_refs"), list) else []
    ev_ids: list[str] = []
    for r in refs:
        if isinstance(r, dict) and r.get("event_id"):
            ev_ids.append(str(r.get("event_id")))
    ev_ids = [x for x in ev_ids if x.strip()][:6]
    tags = n.get("tags") if isinstance(n.get("tags"), list) else []
    return {
        "node_id": nid,
        "canonical_id": view.resolve_id(nid),
        "status": view.node_status(nid),
        "node_type": str(n.get("node_type") or "").strip(),
        "scope": str(n.get("scope") or "").strip(),
        "visibility": str(n.get("visibility") or "").strip(),
        "asserted_ts": str(n.get("asserted_ts") or "").strip(),
        "title": truncate(str(n.get("title") or "").strip(), 160),
        "text": truncate(str(n.get("text") or "").strip(), 560),
        "tags": [str(x) for x in tags if str(x).strip()][:16] if isinstance(tags, list) else [],
        "source_event_ids": ev_ids,
    }


@dataclass(frozen=True)
class ThoughtDbContext:
    """Compact Thought DB subgraph context for Mind prompts (deterministic retrieval)."""

    as_of_ts: str
    query: str
    nodes: list[dict[str, Any]]
    values_claims: list[dict[str, Any]]
    pref_goal_claims: list[dict[str, Any]]
    query_claims: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    notes: str

    def to_prompt_obj(self) -> dict[str, Any]:
        return {
            "as_of_ts": self.as_of_ts,
            "query": truncate(self.query, 1200),
            "nodes": self.nodes,
            "values_claims": self.values_claims,
            "pref_goal_claims": self.pref_goal_claims,
            "query_claims": self.query_claims,
            "edges": self.edges,
            "notes": self.notes,
        }


def build_decide_next_thoughtdb_context(
    *,
    tdb: ThoughtDbStore,
    as_of_ts: str,
    task: str,
    hands_last_message: str,
    recent_evidence: list[dict[str, Any]],
    mem: MemoryService | None = None,
    max_nodes: int = 6,
    max_values_claims: int = 8,
    max_pref_goal_claims: int = 8,
    max_query_claims: int = 10,
    max_edges: int = 20,
) -> ThoughtDbContext:
    """Build a compact Thought DB context for decide_next (always-on, small budget)."""

    t = str(as_of_ts or "").strip()
    q = _collect_query_text(task=task, hands_last_message=hands_last_message, recent_evidence=recent_evidence)
    tokens = tokenize_query(q, max_tokens=18)
    q_compact = " ".join(tokens).strip()

    v_proj = tdb.load_view(scope="project")
    v_glob = tdb.load_view(scope="global")

    seeds = None
    seed_notes = ""
    if q_compact and isinstance(mem, MemoryService):
        seeds = seed_ids_from_memory(mem=mem, query_compact=q_compact, project_id=v_proj.project_id, candidate_k=50)
        seed_notes = seeds.notes

    def _claim_active_and_valid(view: ThoughtDbView, claim_id: str) -> bool:
        return _claim_active_and_valid_view(view, claim_id, as_of_ts=t)

    def _node_active(view: ThoughtDbView, node_id: str) -> bool:
        return _node_active_view(view, node_id)

    # Nodes: include a small set of recent/high-signal Decision/Action/Summary nodes so
    # decide_next can benefit from past decisions/steps without replaying the full transcript.
    nodes: list[dict[str, Any]] = []
    max_nodes_total = max(0, int(max_nodes))
    included_node_ids: set[str] = set()

    def _add_node_by_id(nid: str, *, view: ThoughtDbView) -> None:
        nonlocal nodes
        if len(nodes) >= max_nodes_total:
            return
        n = view.nodes_by_id.get(nid)
        if not isinstance(n, dict):
            return
        nodes.append(_compact_node(n, view=view))
        included_node_ids.add(nid)

    # Always include the latest global values summary node (if present).
    best_vs_id = ""
    best_vs_ts = ""
    for nid in sorted(v_glob.nodes_by_tag.get(VALUES_SUMMARY_TAG, set())):
        if not _node_active(v_glob, nid):
            continue
        n = v_glob.nodes_by_id.get(nid)
        if not isinstance(n, dict):
            continue
        if str(n.get("node_type") or "").strip() != "summary":
            continue
        ts = str(n.get("asserted_ts") or "").strip()
        if ts >= best_vs_ts:
            best_vs_id = nid
            best_vs_ts = ts
    if best_vs_id and max_nodes_total > 0:
        _add_node_by_id(best_vs_id, view=v_glob)

    # Include a few most recent project nodes (best-effort).
    max_recent_project_nodes = min(3, max(0, max_nodes_total - len(nodes)))
    recent_added = 0
    for nid in v_proj.node_ids_by_asserted_ts_desc:
        if recent_added >= max_recent_project_nodes:
            break
        if nid in included_node_ids:
            continue
        if not _node_active(v_proj, nid):
            continue
        _add_node_by_id(nid, view=v_proj)
        recent_added += 1

    # Query-ranked nodes: prefer Memory FTS seeds; fall back to token scanning.
    if len(nodes) < max_nodes_total and seeds:
        for nid in seeds.project_node_ids:
            if len(nodes) >= max_nodes_total:
                break
            if nid in included_node_ids:
                continue
            if not _node_active(v_proj, nid):
                continue
            _add_node_by_id(nid, view=v_proj)
        for nid in seeds.global_node_ids:
            if len(nodes) >= max_nodes_total:
                break
            if nid in included_node_ids:
                continue
            if not _node_active(v_glob, nid):
                continue
            _add_node_by_id(nid, view=v_glob)

    if len(nodes) < max_nodes_total and tokens:
        scored_nodes: list[tuple[int, int, str, str, ThoughtDbView]] = []
        for view, scope_rank in ((v_proj, 0), (v_glob, 1)):
            for n in view.iter_nodes(include_inactive=False, include_aliases=False):
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("node_id") or "").strip()
                if not nid or nid in included_node_ids:
                    continue
                title = str(n.get("title") or "").strip()
                text = str(n.get("text") or "").strip()
                if not title and not text:
                    continue
                score = _score_tokens(tokens, text=(title + "\n" + text).strip())
                if score <= 0:
                    continue
                ts = str(n.get("asserted_ts") or "").strip()
                scored_nodes.append((score, scope_rank, ts, nid, view))

        scored_nodes.sort(key=lambda x: str(x[2] or ""), reverse=True)
        scored_nodes.sort(key=lambda x: int(x[1]), reverse=False)
        scored_nodes.sort(key=lambda x: -int(x[0]), reverse=False)

        for _score, _rank, _ts, nid, view in scored_nodes:
            if len(nodes) >= max_nodes_total:
                break
            if nid in included_node_ids:
                continue
            if not _node_active(view, nid):
                continue
            _add_node_by_id(nid, view=view)

    # Values claims: active global preference/goal claims tagged as values:base.
    values_claims: list[dict[str, Any]] = []
    values_ids: set[str] = set()
    vals: list[tuple[str, str]] = []  # (asserted_ts, claim_id)
    for cid in v_glob.claims_by_tag.get(VALUES_BASE_TAG, set()):
        if not _claim_active_and_valid(v_glob, cid):
            continue
        c = v_glob.claims_by_id.get(cid)
        if not isinstance(c, dict):
            continue
        ct = str(c.get("claim_type") or "").strip()
        if ct not in ("preference", "goal"):
            continue
        vals.append((str(c.get("asserted_ts") or "").strip(), cid))
    vals.sort(key=lambda x: x[0], reverse=True)
    for _ts, cid in vals[: max(0, int(max_values_claims))]:
        c = v_glob.claims_by_id.get(cid)
        if not isinstance(c, dict):
            continue
        values_claims.append(_compact_claim(c, view=v_glob))
        values_ids.add(cid)

    # Canonical preference/goal claims beyond values:base.
    pref_goal_claims: list[dict[str, Any]] = []
    pref_goal_ids: set[str] = set()
    pinned_ids: set[str] = set()

    if PINNED_PREF_GOAL_TAGS:
        pinned: list[tuple[int, str, str, ThoughtDbView]] = []
        for view, scope_rank in ((v_proj, 0), (v_glob, 1)):
            for tag in PINNED_PREF_GOAL_TAGS:
                for cid in view.claims_by_tag.get(tag, set()):
                    if cid in pinned_ids or cid in values_ids:
                        continue
                    if not _claim_active_and_valid(view, cid):
                        continue
                    c = view.claims_by_id.get(cid)
                    if not isinstance(c, dict):
                        continue
                    ct = str(c.get("claim_type") or "").strip()
                    if ct not in ("preference", "goal"):
                        continue
                    pinned_ids.add(cid)
                    pinned.append((scope_rank, str(c.get("asserted_ts") or "").strip(), cid, view))
        pinned.sort(key=lambda x: str(x[1] or ""), reverse=True)
        pinned.sort(key=lambda x: int(x[0]), reverse=False)
        for _rank, _ts, cid, view in pinned:
            if len(pref_goal_claims) >= max(0, int(max_pref_goal_claims)):
                break
            c = view.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            pref_goal_claims.append(_compact_claim(c, view=view))
            pref_goal_ids.add(cid)

    # Fill remaining pref/goal budget with recent preferences/goals (project first, then global).
    for view in (v_proj, v_glob):
        if len(pref_goal_claims) >= max(0, int(max_pref_goal_claims)):
            break
        for cid in view.claim_ids_by_asserted_ts_desc:
            if len(pref_goal_claims) >= max(0, int(max_pref_goal_claims)):
                break
            if cid in values_ids or cid in pinned_ids or cid in pref_goal_ids:
                continue
            if not _claim_active_and_valid(view, cid):
                continue
            c = view.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            ct = str(c.get("claim_type") or "").strip()
            if ct not in ("preference", "goal"):
                continue
            tags = c.get("tags") if isinstance(c.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            if VALUES_BASE_TAG in tagset or VALUES_RAW_TAG in tagset:
                continue
            pref_goal_claims.append(_compact_claim(c, view=view))
            pref_goal_ids.add(cid)

    # Query-ranked claims: prefer Memory FTS seeds; fall back to token scanning.
    query_claims: list[dict[str, Any]] = []
    included_claim_ids: set[str] = set(values_ids) | set(pref_goal_ids)

    if seeds:
        cands: list[tuple[int, str, str, ThoughtDbView]] = []
        for cid in seeds.project_claim_ids:
            if cid in included_claim_ids:
                continue
            if not _claim_active_and_valid(v_proj, cid):
                continue
            c = v_proj.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            tags = c.get("tags") if isinstance(c.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            if VALUES_RAW_TAG in tagset:
                continue
            cands.append((0, str(c.get("asserted_ts") or "").strip(), cid, v_proj))
        for cid in seeds.global_claim_ids:
            if cid in included_claim_ids:
                continue
            if not _claim_active_and_valid(v_glob, cid):
                continue
            c = v_glob.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            tags = c.get("tags") if isinstance(c.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            if VALUES_RAW_TAG in tagset:
                continue
            cands.append((1, str(c.get("asserted_ts") or "").strip(), cid, v_glob))
        cands.sort(key=lambda x: str(x[1] or ""), reverse=True)
        cands.sort(key=lambda x: int(x[0]), reverse=False)
        for _rank, _ts, cid, view in cands:
            if len(query_claims) >= max(0, int(max_query_claims)):
                break
            if cid in included_claim_ids:
                continue
            c = view.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            query_claims.append(_compact_claim(c, view=view))
            included_claim_ids.add(cid)

    # Token-based fallback (or filler when memory seeds are insufficient).
    if len(query_claims) < max(0, int(max_query_claims)) and tokens:
        scored: list[tuple[int, int, str, str, ThoughtDbView]] = []
        for view, scope_rank in ((v_proj, 0), (v_glob, 1)):
            for c in view.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=t):
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("claim_id") or "").strip()
                if not cid or cid in included_claim_ids:
                    continue
                tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                tagset = {str(x).strip() for x in tags if str(x).strip()}
                if VALUES_RAW_TAG in tagset:
                    continue
                text = str(c.get("text") or "").strip()
                if not text:
                    continue
                score = _score_tokens(tokens, text=text)
                if score <= 0:
                    continue
                ct = str(c.get("claim_type") or "").strip()
                if ct in ("preference", "goal"):
                    score += 1
                scored.append((score, scope_rank, str(c.get("asserted_ts") or "").strip(), cid, view))

        scored.sort(key=lambda x: str(x[2] or ""), reverse=True)
        scored.sort(key=lambda x: int(x[1]), reverse=False)
        scored.sort(key=lambda x: -int(x[0]), reverse=False)

        for _score, _rank, _ts, cid, view in scored:
            if len(query_claims) >= max(0, int(max_query_claims)):
                break
            if cid in included_claim_ids:
                continue
            c = view.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            query_claims.append(_compact_claim(c, view=view))
            included_claim_ids.add(cid)

    # One-hop edge expansion: pull in direct neighbors (claims/nodes) within remaining budgets.
    expand_notes = ""
    rem_claims = max(0, int(max_query_claims) - len(query_claims))
    rem_nodes = max(0, int(max_nodes_total) - len(nodes))
    if rem_claims > 0 or rem_nodes > 0:
        seed_ids = set(included_node_ids) | set(included_claim_ids)
        exp = expand_one_hop(
            v_proj=v_proj,
            v_glob=v_glob,
            seed_ids=seed_ids,
            as_of_ts=t,
            max_new_claims=rem_claims,
            max_new_nodes=rem_nodes,
            edge_types={"depends_on", "supports", "contradicts", "derived_from", "mentions", "supersedes", "same_as"},
        )
        expand_notes = exp.notes
        for cid in exp.claim_ids:
            if len(query_claims) >= max(0, int(max_query_claims)):
                break
            if cid in included_claim_ids:
                continue
            view = v_proj if cid in v_proj.claims_by_id else v_glob
            if not _claim_active_and_valid(view, cid):
                continue
            c = view.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            query_claims.append(_compact_claim(c, view=view))
            included_claim_ids.add(cid)
        for nid in exp.node_ids:
            if len(nodes) >= max_nodes_total:
                break
            if nid in included_node_ids:
                continue
            view = v_proj if nid in v_proj.nodes_by_id else v_glob
            if not _node_active(view, nid):
                continue
            _add_node_by_id(nid, view=view)

    included_ids: set[str] = set(included_claim_ids) | set(included_node_ids)

    # Allow edges that reference recent EvidenceLog event_ids for provenance (`derived_from`, etc.).
    recent_event_ids: set[str] = set()
    for rec in (recent_evidence or [])[-12:]:
        if not isinstance(rec, dict):
            continue
        eid = rec.get("event_id")
        if isinstance(eid, str) and eid.strip():
            recent_event_ids.add(eid.strip())
        if len(recent_event_ids) >= 18:
            break
    edge_allow_ids = set(included_ids) | set(recent_event_ids)

    # Edges among included claims/nodes (small budget).
    edge_types = {"depends_on", "supports", "contradicts", "derived_from", "mentions", "supersedes", "same_as"}
    edges: list[dict[str, Any]] = []
    seen_edges: set[str] = set()

    def _add_edges_from_view(view: ThoughtDbView) -> None:
        nonlocal edges
        for nid in sorted(edge_allow_ids):
            if len(edges) >= max(0, int(max_edges)):
                return
            adjacent = []
            adjacent.extend([x for x in (view.edges_by_from.get(nid) or []) if isinstance(x, dict)])
            adjacent.extend([x for x in (view.edges_by_to.get(nid) or []) if isinstance(x, dict)])
            for e in adjacent:
                if len(edges) >= max(0, int(max_edges)):
                    return
                if str(e.get("kind") or "").strip() != "edge":
                    continue
                et = str(e.get("edge_type") or "").strip()
                if et not in edge_types:
                    continue
                frm = str(e.get("from_id") or "").strip()
                to = str(e.get("to_id") or "").strip()
                if not frm or not to:
                    continue
                if frm not in edge_allow_ids or to not in edge_allow_ids:
                    continue
                key = f"{view.scope}:{et}:{frm}->{to}"
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edges.append(_compact_edge(e, scope=view.scope))

    # Prefer project edges first, then global.
    _add_edges_from_view(v_proj)
    _add_edges_from_view(v_glob)

    notes = (
        f"tokens={len(tokens)} nodes={len(nodes)} values_claims={len(values_claims)} pref_goal_claims={len(pref_goal_claims)} "
        f"query_claims={len(query_claims)} edges={len(edges)} budgets(values={max_values_claims} pref_goal={max_pref_goal_claims} "
        f"query={max_query_claims} nodes={max_nodes} edges={max_edges}) seed={seed_notes or '(none)'} expand={expand_notes or '(none)'}"
    )
    return ThoughtDbContext(
        as_of_ts=t,
        query=q,
        nodes=nodes,
        values_claims=values_claims,
        pref_goal_claims=pref_goal_claims,
        query_claims=query_claims,
        edges=edges,
        notes=notes,
    )
