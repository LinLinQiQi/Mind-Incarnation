from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..memory.service import MemoryService
from ..memory.types import MemoryItem
from .store import ThoughtDbView


@dataclass(frozen=True)
class MemorySeedIds:
    project_claim_ids: list[str]
    global_claim_ids: list[str]
    project_node_ids: list[str]
    global_node_ids: list[str]
    notes: str


@dataclass(frozen=True)
class OneHopExpansion:
    claim_ids: list[str]
    node_ids: list[str]
    notes: str


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        s = str(x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _extract_source_id(item: MemoryItem, *, kind: str, key: str) -> str:
    for r in item.source_refs:
        if not isinstance(r, dict):
            continue
        if str(r.get("kind") or "").strip() != kind:
            continue
        v = r.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def seed_ids_from_memory(
    *,
    mem: MemoryService,
    query_compact: str,
    project_id: str,
    candidate_k: int = 50,
) -> MemorySeedIds:
    """Use the memory index (FTS) as a candidate generator for Thought DB context.

    Design choice: Memory search may return items from other projects. For decide_next
    Thought DB context we keep only:
    - current project scope (project_id match)
    - global scope
    """

    q = str(query_compact or "").strip()
    pid = str(project_id or "").strip()
    if not q:
        return MemorySeedIds([], [], [], [], notes="skipped: empty query_compact")

    try:
        k = int(candidate_k or 50)
    except Exception:
        k = 50
    k = max(5, min(200, k))

    items: list[MemoryItem] = []
    try:
        items = mem.search(query=q, top_k=k, kinds={"claim", "node"}, include_global=True, exclude_project_id="")
    except Exception:
        items = []

    kept: list[MemoryItem] = []
    dropped_other = 0
    for it in items:
        if it.scope == "global":
            kept.append(it)
            continue
        if it.scope == "project" and pid and str(it.project_id or "").strip() == pid:
            kept.append(it)
            continue
        dropped_other += 1

    proj_claim: list[str] = []
    glob_claim: list[str] = []
    proj_node: list[str] = []
    glob_node: list[str] = []

    for it in kept:
        if it.kind == "claim":
            cid = _extract_source_id(it, kind="thoughtdb_claim", key="claim_id")
            if not cid:
                continue
            if it.scope == "global":
                glob_claim.append(cid)
            else:
                proj_claim.append(cid)
        elif it.kind == "node":
            nid = _extract_source_id(it, kind="thoughtdb_node", key="node_id")
            if not nid:
                continue
            if it.scope == "global":
                glob_node.append(nid)
            else:
                proj_node.append(nid)

    proj_claim = _dedupe_keep_order(proj_claim)
    glob_claim = _dedupe_keep_order(glob_claim)
    proj_node = _dedupe_keep_order(proj_node)
    glob_node = _dedupe_keep_order(glob_node)

    notes = f"fts_seeds(items={len(items)} kept={len(kept)} dropped_other={dropped_other} k={k})"
    return MemorySeedIds(proj_claim, glob_claim, proj_node, glob_node, notes=notes)


def _claim_active_and_valid(view: ThoughtDbView, claim_id: str, *, as_of_ts: str) -> bool:
    cid = str(claim_id or "").strip()
    if not cid:
        return False
    if cid in view.redirects_same_as:
        return False
    if view.claim_status(cid) != "active":
        return False
    c = view.claims_by_id.get(cid)
    if not isinstance(c, dict):
        return False
    t = str(as_of_ts or "").strip()
    if t:
        vf = c.get("valid_from")
        vt = c.get("valid_to")
        if isinstance(vf, str) and vf.strip() and vf.strip() > t:
            return False
        if isinstance(vt, str) and vt.strip() and t >= vt.strip():
            return False
    return True


def _node_active(view: ThoughtDbView, node_id: str) -> bool:
    nid = str(node_id or "").strip()
    if not nid:
        return False
    if nid in view.redirects_same_as:
        return False
    return view.node_status(nid) == "active"


def _edges_adjacent(view: ThoughtDbView, node_id: str) -> list[dict[str, Any]]:
    nid = str(node_id or "").strip()
    if not nid:
        return []
    out: list[dict[str, Any]] = []
    out.extend([x for x in (view.edges_by_from.get(nid) or []) if isinstance(x, dict)])
    out.extend([x for x in (view.edges_by_to.get(nid) or []) if isinstance(x, dict)])
    return out


def expand_one_hop(
    *,
    v_proj: ThoughtDbView,
    v_glob: ThoughtDbView,
    seed_ids: set[str],
    as_of_ts: str,
    max_new_claims: int,
    max_new_nodes: int,
    edge_types: set[str] | None = None,
) -> OneHopExpansion:
    """Expand candidate ids by 1 hop using Thought DB edges (best-effort)."""

    allow = edge_types or {"depends_on", "supports", "contradicts", "derived_from", "mentions", "supersedes", "same_as"}
    seeds = {str(x).strip() for x in (seed_ids or set()) if str(x).strip()}
    if not seeds:
        return OneHopExpansion([], [], notes="expand_one_hop: skipped (no seeds)")

    try:
        max_c = int(max_new_claims or 0)
    except Exception:
        max_c = 0
    try:
        max_n = int(max_new_nodes or 0)
    except Exception:
        max_n = 0
    max_c = max(0, max_c)
    max_n = max(0, max_n)
    if max_c <= 0 and max_n <= 0:
        return OneHopExpansion([], [], notes="expand_one_hop: skipped (no budget)")

    added_claims: list[str] = []
    added_nodes: list[str] = []
    seen_added: set[str] = set()
    seen_edges: set[str] = set()

    def classify(other: str) -> str:
        if other.startswith("cl_") or other in v_proj.claims_by_id or other in v_glob.claims_by_id:
            return "claim"
        if other.startswith("nd_") or other in v_proj.nodes_by_id or other in v_glob.nodes_by_id:
            return "node"
        return ""

    def ok_other(other: str, kind: str) -> bool:
        if kind == "claim":
            return _claim_active_and_valid(v_proj, other, as_of_ts=as_of_ts) or _claim_active_and_valid(v_glob, other, as_of_ts=as_of_ts)
        if kind == "node":
            return _node_active(v_proj, other) or _node_active(v_glob, other)
        return False

    for view in (v_proj, v_glob):  # prefer project edges
        for sid in sorted(seeds):
            for e in _edges_adjacent(view, sid):
                if len(added_claims) >= max_c and len(added_nodes) >= max_n:
                    break
                if not isinstance(e, dict):
                    continue
                if str(e.get("kind") or "").strip() != "edge":
                    continue
                et = str(e.get("edge_type") or "").strip()
                if et not in allow:
                    continue
                frm = str(e.get("from_id") or "").strip()
                to = str(e.get("to_id") or "").strip()
                if not frm or not to:
                    continue
                ek = f"{view.scope}:{et}:{frm}->{to}"
                if ek in seen_edges:
                    continue
                seen_edges.add(ek)

                other = to if frm == sid else (frm if to == sid else "")
                if not other or other in seeds or other in seen_added:
                    continue
                knd = classify(other)
                if not knd or not ok_other(other, knd):
                    continue

                if knd == "claim" and len(added_claims) < max_c:
                    added_claims.append(other)
                    seen_added.add(other)
                elif knd == "node" and len(added_nodes) < max_n:
                    added_nodes.append(other)
                    seen_added.add(other)
            if len(added_claims) >= max_c and len(added_nodes) >= max_n:
                break
        if len(added_claims) >= max_c and len(added_nodes) >= max_n:
            break

    notes = f"expand_one_hop(added_claims={len(added_claims)} added_nodes={len(added_nodes)} seeds={len(seeds)})"
    return OneHopExpansion(added_claims, added_nodes, notes=notes)
