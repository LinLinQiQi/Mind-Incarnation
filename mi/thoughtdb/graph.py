from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from ..core.storage import now_rfc3339
from .store import ThoughtDbStore, ThoughtDbView


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _claim_valid_as_of(c: dict[str, Any], *, as_of_ts: str) -> bool:
    t = (as_of_ts or "").strip()
    if not t:
        return True
    vf = c.get("valid_from")
    vt = c.get("valid_to")
    if isinstance(vf, str) and vf.strip() and vf.strip() > t:
        return False
    if isinstance(vt, str) and vt.strip() and t >= vt.strip():
        return False
    return True


def _edge_key(e: dict[str, Any]) -> str:
    et = str(e.get("edge_type") or "").strip()
    frm = str(e.get("from_id") or "").strip()
    to = str(e.get("to_id") or "").strip()
    return f"{et}|{frm}|{to}"


def _view_knows_id(v: ThoughtDbView, id0: str) -> bool:
    i = (id0 or "").strip()
    if not i:
        return False
    return i in v.claims_by_id or i in v.nodes_by_id or i in v.redirects_same_as


def _resolve_id_effective(v_proj: ThoughtDbView, v_glob: ThoughtDbView, id0: str) -> str:
    i = (id0 or "").strip()
    if not i:
        return ""
    if _view_knows_id(v_proj, i):
        return v_proj.resolve_id(i)
    if _view_knows_id(v_glob, i):
        return v_glob.resolve_id(i)
    return i


def _reverse_aliases(v: ThoughtDbView) -> dict[str, set[str]]:
    """Build canonical_id -> {alias_ids} map from redirects (best-effort)."""

    rev: dict[str, set[str]] = {}
    for dup in v.redirects_same_as:
        d = str(dup or "").strip()
        if not d:
            continue
        canon = v.resolve_id(d)
        if not canon or canon == d:
            continue
        rev.setdefault(canon, set()).add(d)
    return rev


def _iter_edges_for_keys(
    v: ThoughtDbView,
    *,
    keys: Iterable[str],
    direction: str,
) -> Iterable[dict[str, Any]]:
    seen_edge_ids: set[str] = set()
    want_out = direction in ("out", "both")
    want_in = direction in ("in", "both")

    for k in keys:
        kk = str(k or "").strip()
        if not kk:
            continue
        if want_out:
            for e in v.edges_by_from.get(kk, []) or []:
                if not isinstance(e, dict):
                    continue
                eid = str(e.get("edge_id") or "").strip()
                if eid and eid in seen_edge_ids:
                    continue
                if eid:
                    seen_edge_ids.add(eid)
                yield e
        if want_in:
            for e in v.edges_by_to.get(kk, []) or []:
                if not isinstance(e, dict):
                    continue
                eid = str(e.get("edge_id") or "").strip()
                if eid and eid in seen_edge_ids:
                    continue
                if eid:
                    seen_edge_ids.add(eid)
                yield e


def _neighbors_for_edge(e: dict[str, Any], *, cur: str, direction: str) -> list[str]:
    frm = str(e.get("from_id") or "").strip()
    to = str(e.get("to_id") or "").strip()
    if not frm or not to:
        return []
    out: list[str] = []
    if direction in ("out", "both") and frm == cur:
        out.append(to)
    if direction in ("in", "both") and to == cur:
        out.append(frm)
    # If we are matching via an alias key, allow both-end matching.
    if direction == "both" and cur not in (frm, to):
        if cur == frm:
            out.append(to)
        elif cur == to:
            out.append(frm)
    return out


def _compact_claim(view: ThoughtDbView, cid: str, *, status: str, canonical_id: str) -> dict[str, Any]:
    c = view.claims_by_id.get(cid) if isinstance(view.claims_by_id.get(cid), dict) else {}
    tags = c.get("tags") if isinstance(c.get("tags"), list) else []
    refs = c.get("source_refs") if isinstance(c.get("source_refs"), list) else []
    ev_ids: list[str] = []
    for r in refs:
        if isinstance(r, dict) and str(r.get("event_id") or "").strip():
            ev_ids.append(str(r.get("event_id")))
    ev_ids = [x for x in ev_ids if x.strip()][:8]
    return {
        "claim_id": cid,
        "canonical_id": canonical_id,
        "status": status,
        "claim_type": str(c.get("claim_type") or "").strip(),
        "scope": str(c.get("scope") or view.scope).strip(),
        "visibility": str(c.get("visibility") or "").strip(),
        "asserted_ts": str(c.get("asserted_ts") or "").strip(),
        "valid_from": c.get("valid_from"),
        "valid_to": c.get("valid_to"),
        "text": _truncate(str(c.get("text") or "").strip(), 800),
        "tags": [str(x) for x in tags if str(x).strip()][:24],
        "source_event_ids": ev_ids,
    }


def _compact_node(view: ThoughtDbView, nid: str, *, status: str, canonical_id: str) -> dict[str, Any]:
    n = view.nodes_by_id.get(nid) if isinstance(view.nodes_by_id.get(nid), dict) else {}
    tags = n.get("tags") if isinstance(n.get("tags"), list) else []
    refs = n.get("source_refs") if isinstance(n.get("source_refs"), list) else []
    ev_ids: list[str] = []
    for r in refs:
        if isinstance(r, dict) and str(r.get("event_id") or "").strip():
            ev_ids.append(str(r.get("event_id")))
    ev_ids = [x for x in ev_ids if x.strip()][:8]
    return {
        "node_id": nid,
        "canonical_id": canonical_id,
        "status": status,
        "node_type": str(n.get("node_type") or "").strip(),
        "scope": str(n.get("scope") or view.scope).strip(),
        "visibility": str(n.get("visibility") or "").strip(),
        "asserted_ts": str(n.get("asserted_ts") or "").strip(),
        "title": _truncate(str(n.get("title") or "").strip(), 240),
        "text": _truncate(str(n.get("text") or "").strip(), 1000),
        "tags": [str(x) for x in tags if str(x).strip()][:24],
        "source_event_ids": ev_ids,
    }


def _edge_out(
    *,
    e: dict[str, Any],
    from_canon: str,
    to_canon: str,
    include_aliases: bool,
) -> dict[str, Any]:
    frm = str(e.get("from_id") or "").strip()
    to = str(e.get("to_id") or "").strip()
    out = {
        "edge_id": str(e.get("edge_id") or "").strip(),
        "edge_type": str(e.get("edge_type") or "").strip(),
        "scope": str(e.get("scope") or "").strip(),
        "project_id": str(e.get("project_id") or "").strip(),
        "visibility": str(e.get("visibility") or "").strip(),
        "asserted_ts": str(e.get("asserted_ts") or "").strip(),
        "notes": str(e.get("notes") or "").strip(),
        "source_refs": e.get("source_refs") if isinstance(e.get("source_refs"), list) else [],
        "from_id": frm,
        "to_id": to,
        "from_id_canonical": from_canon,
        "to_id_canonical": to_canon,
    }
    if not include_aliases:
        # Keep the output graph consistent: prefer canonical ids as endpoints.
        out["from_id"] = from_canon
        out["to_id"] = to_canon
        if frm and frm != from_canon:
            out["from_id_raw"] = frm
        if to and to != to_canon:
            out["to_id_raw"] = to
    return out


@dataclass(frozen=True)
class _EffectiveViews:
    proj: ThoughtDbView
    glob: ThoughtDbView
    aliases_proj: dict[str, set[str]]
    aliases_glob: dict[str, set[str]]

    def resolve_id(self, id0: str) -> str:
        return _resolve_id_effective(self.proj, self.glob, id0)

    def alias_keys_for(self, canon: str) -> set[str]:
        c = (canon or "").strip()
        if not c:
            return set()
        out: set[str] = set()
        out |= self.aliases_proj.get(c, set())
        out |= self.aliases_glob.get(c, set())
        return out

    def find_claim(self, cid: str) -> tuple[ThoughtDbView | None, dict[str, Any] | None]:
        c = (cid or "").strip()
        if not c:
            return None, None
        for v in (self.proj, self.glob):
            if c in v.claims_by_id:
                return v, v.claims_by_id.get(c)
            canon = v.resolve_id(c)
            if canon and canon in v.claims_by_id:
                return v, v.claims_by_id.get(canon)
        return None, None

    def find_node(self, nid: str) -> tuple[ThoughtDbView | None, dict[str, Any] | None]:
        n = (nid or "").strip()
        if not n:
            return None, None
        for v in (self.proj, self.glob):
            if n in v.nodes_by_id:
                return v, v.nodes_by_id.get(n)
            canon = v.resolve_id(n)
            if canon and canon in v.nodes_by_id:
                return v, v.nodes_by_id.get(canon)
        return None, None

    def claim_status(self, cid: str) -> tuple[str, str]:
        c = (cid or "").strip()
        if not c:
            return "", ""
        for v in (self.proj, self.glob):
            if _view_knows_id(v, c):
                canon = v.resolve_id(c)
                return v.claim_status(canon), canon
        return "unknown", c

    def node_status(self, nid: str) -> tuple[str, str]:
        n = (nid or "").strip()
        if not n:
            return "", ""
        for v in (self.proj, self.glob):
            if _view_knows_id(v, n):
                canon = v.resolve_id(n)
                return v.node_status(canon), canon
        return "unknown", n


def build_subgraph_for_id(
    *,
    tdb: ThoughtDbStore,
    scope: str,
    root_id: str,
    depth: int,
    direction: str,
    edge_types: set[str] | None,
    include_inactive: bool,
    include_aliases: bool,
    as_of_ts: str = "",
) -> dict[str, Any]:
    """Build a bounded subgraph around a root id (claim_id/node_id/event_id).

    Output is JSON-friendly and intended for CLI inspection.
    """

    sc = (scope or "project").strip()
    if sc not in ("project", "global", "effective"):
        sc = "project"
    rid = (root_id or "").strip()
    if not rid:
        return {"root_id": "", "depth": 0, "direction": "both", "claims": [], "nodes": [], "edges": [], "missing_ids": []}

    try:
        dmax = int(depth)
    except Exception:
        dmax = 1
    dmax = max(0, min(6, dmax))

    dir0 = (direction or "both").strip()
    if dir0 not in ("out", "in", "both"):
        dir0 = "both"

    asof = (as_of_ts or "").strip() or now_rfc3339()

    etypes = {str(x).strip() for x in (edge_types or set()) if str(x).strip()}
    if not etypes:
        etypes = set()

    # View(s)
    eff: _EffectiveViews | None = None
    v_single: ThoughtDbView | None = None
    aliases_single: dict[str, set[str]] = {}
    if sc == "effective":
        v_proj = tdb.load_view(scope="project")
        v_glob = tdb.load_view(scope="global")
        eff = _EffectiveViews(
            proj=v_proj,
            glob=v_glob,
            aliases_proj=_reverse_aliases(v_proj),
            aliases_glob=_reverse_aliases(v_glob),
        )
    else:
        v_single = tdb.load_view(scope=sc)
        aliases_single = _reverse_aliases(v_single)

    def node_key(id0: str) -> str:
        i = (id0 or "").strip()
        if not i:
            return ""
        if include_aliases:
            return i
        if eff is not None:
            return eff.resolve_id(i)
        assert v_single is not None
        return v_single.resolve_id(i)

    def equivalent_edge_lookup_keys(id0: str) -> set[str]:
        i = (id0 or "").strip()
        if not i:
            return set()
        if include_aliases:
            return {i}
        canon = node_key(i)
        out = {i, canon} if canon else {i}
        if eff is not None:
            out |= eff.alias_keys_for(canon)
        else:
            out |= aliases_single.get(canon, set())
        return {x for x in out if str(x).strip()}

    def status_and_view_for(id0: str) -> tuple[str, str, ThoughtDbView | None]:
        """Return (kind, status, view_used) for claim/node ids; kind is claim|node|unknown."""

        i = (id0 or "").strip()
        if not i:
            return "unknown", "unknown", None
        if eff is not None:
            v, c = eff.find_claim(i)
            if isinstance(c, dict):
                st, canon = eff.claim_status(i)
                return "claim", st, v
            v2, n = eff.find_node(i)
            if isinstance(n, dict):
                st, canon = eff.node_status(i)
                return "node", st, v2
            return "unknown", "unknown", None
        assert v_single is not None
        if i in v_single.claims_by_id or v_single.resolve_id(i) in v_single.claims_by_id:
            st = v_single.claim_status(v_single.resolve_id(i))
            return "claim", st, v_single
        if i in v_single.nodes_by_id or v_single.resolve_id(i) in v_single.nodes_by_id:
            st = v_single.node_status(v_single.resolve_id(i))
            return "node", st, v_single
        return "unknown", "unknown", None

    def claim_obj_for(id0: str) -> tuple[ThoughtDbView | None, str]:
        i = (id0 or "").strip()
        if not i:
            return None, ""
        if eff is not None:
            v, c = eff.find_claim(i)
            if isinstance(c, dict) and v is not None:
                return v, str(c.get("claim_id") or v.resolve_id(i)).strip() or v.resolve_id(i)
            return None, ""
        assert v_single is not None
        canon = v_single.resolve_id(i)
        if canon in v_single.claims_by_id:
            return v_single, canon
        return None, ""

    def node_obj_for(id0: str) -> tuple[ThoughtDbView | None, str]:
        i = (id0 or "").strip()
        if not i:
            return None, ""
        if eff is not None:
            v, n = eff.find_node(i)
            if isinstance(n, dict) and v is not None:
                return v, str(n.get("node_id") or v.resolve_id(i)).strip() or v.resolve_id(i)
            return None, ""
        assert v_single is not None
        canon = v_single.resolve_id(i)
        if canon in v_single.nodes_by_id:
            return v_single, canon
        return None, ""

    # BFS traversal
    root_key = node_key(rid)
    queue: deque[tuple[str, int]] = deque([(root_key, 0)])
    seen_depth: dict[str, int] = {root_key: 0}
    included: set[str] = {root_key}

    collected_edges: dict[str, dict[str, Any]] = {}

    while queue:
        cur, d = queue.popleft()
        if not cur:
            continue
        if d >= dmax:
            continue

        keys = equivalent_edge_lookup_keys(cur)

        def iter_edges() -> Iterable[dict[str, Any]]:
            if eff is not None:
                # Project edges win.
                seen_triples: set[str] = set()
                for e in _iter_edges_for_keys(eff.proj, keys=keys, direction=dir0):
                    if not isinstance(e, dict):
                        continue
                    k = _edge_key(e)
                    seen_triples.add(k)
                    yield e
                for e in _iter_edges_for_keys(eff.glob, keys=keys, direction=dir0):
                    if not isinstance(e, dict):
                        continue
                    k = _edge_key(e)
                    if k in seen_triples:
                        continue
                    yield e
            else:
                assert v_single is not None
                yield from _iter_edges_for_keys(v_single, keys=keys, direction=dir0)

        for e in iter_edges():
            if not isinstance(e, dict):
                continue
            et = str(e.get("edge_type") or "").strip()
            if etypes and et not in etypes:
                continue

            # Consider neighbors for each lookup key we used; this keeps alias lookups connected.
            # This is bounded by depth and a small alias set in practice.
            neighbors: list[str] = []
            for k in keys:
                neighbors.extend(_neighbors_for_edge(e, cur=str(k), direction=dir0))
            if not neighbors:
                continue

            for nb in neighbors:
                nb_raw = str(nb or "").strip()
                if not nb_raw:
                    continue
                nb_key = node_key(nb_raw)
                if not nb_key:
                    continue

                # Filter inactive items (root is always included).
                if nb_key != root_key:
                    kind, st, v_used = status_and_view_for(nb_key)
                    if kind == "claim" and v_used is not None:
                        cid = v_used.resolve_id(nb_key)
                        cobj = v_used.claims_by_id.get(cid) if isinstance(v_used.claims_by_id.get(cid), dict) else None
                        if isinstance(cobj, dict) and (not include_inactive) and (st != "active" or (not _claim_valid_as_of(cobj, as_of_ts=asof))):
                            continue
                    elif kind == "node" and (not include_inactive) and st != "active":
                        continue

                included.add(nb_key)
                prev = seen_depth.get(nb_key)
                if prev is None or (d + 1) < prev:
                    seen_depth[nb_key] = d + 1
                    queue.append((nb_key, d + 1))

                eid = str(e.get("edge_id") or "").strip()
                key2 = eid if eid else _edge_key(e)
                if key2 not in collected_edges:
                    collected_edges[key2] = e

    # Materialize entities.
    claims_out: list[dict[str, Any]] = []
    nodes_out: list[dict[str, Any]] = []
    missing: set[str] = set()

    for i in sorted(included):
        if not i:
            continue
        v, cid = claim_obj_for(i)
        if v is not None and cid:
            # Root is included even if inactive; apply inactive filtering to other items.
            st = v.claim_status(cid)
            canon = v.resolve_id(cid)
            cobj = v.claims_by_id.get(cid) if isinstance(v.claims_by_id.get(cid), dict) else None
            if i != root_key and isinstance(cobj, dict) and (not include_inactive) and (st != "active" or (not _claim_valid_as_of(cobj, as_of_ts=asof))):
                continue
            claims_out.append(_compact_claim(v, cid, status=st, canonical_id=canon))
            continue

        v2, nid = node_obj_for(i)
        if v2 is not None and nid:
            st = v2.node_status(nid)
            canon = v2.resolve_id(nid)
            if i != root_key and (not include_inactive) and st != "active":
                continue
            nodes_out.append(_compact_node(v2, nid, status=st, canonical_id=canon))
            continue

        missing.add(i)

    # Materialize edges (canonicalize endpoints when include_aliases is false).
    edges_out: list[dict[str, Any]] = []
    for e in collected_edges.values():
        if not isinstance(e, dict):
            continue
        frm = str(e.get("from_id") or "").strip()
        to = str(e.get("to_id") or "").strip()
        frm_c = node_key(frm) if frm else frm
        to_c = node_key(to) if to else to
        edges_out.append(_edge_out(e=e, from_canon=frm_c, to_canon=to_c, include_aliases=include_aliases))

    # Sort for stable output (newest edges first when possible).
    edges_out.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)
    claims_out.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)
    nodes_out.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)

    return {
        "root_id": rid,
        "root_id_canonical": root_key,
        "depth": dmax,
        "direction": dir0,
        "edge_types": sorted(etypes) if etypes else [],
        "include_inactive": bool(include_inactive),
        "include_aliases": bool(include_aliases),
        "as_of_ts": asof,
        "claims": claims_out,
        "nodes": nodes_out,
        "edges": edges_out,
        "missing_ids": sorted(missing),
    }

