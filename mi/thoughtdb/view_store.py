from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..core.storage import now_rfc3339, read_json, atomic_write_json
from .model import (
    VIEW_SNAPSHOT_KIND,
    VIEW_SNAPSHOT_VERSION,
    ThoughtDbView,
    claim_signature,
    edge_key,
)


class ThoughtViewStore:
    """Materialized view + snapshot/cache layer for Thought DB."""

    def __init__(
        self,
        *,
        claims_path_for_scope: Callable[[str], Path],
        edges_path_for_scope: Callable[[str], Path],
        nodes_path_for_scope: Callable[[str], Path],
        iter_jsonl_reader: Callable[[Path], Any],
        project_id_for_scope: Callable[[str], str],
        scope_metas: Callable[[str], tuple[tuple[int, int], tuple[int, int], tuple[int, int]]],
        view_snapshot_path: Callable[[str], Path],
    ) -> None:
        self._claims_path_for_scope = claims_path_for_scope
        self._edges_path_for_scope = edges_path_for_scope
        self._nodes_path_for_scope = nodes_path_for_scope
        self._iter_jsonl = iter_jsonl_reader
        self._project_id_for_scope = project_id_for_scope
        self._scope_metas = scope_metas
        self._view_snapshot_path = view_snapshot_path
        self._view_cache: dict[str, tuple[ThoughtDbView, tuple[tuple[int, int], tuple[int, int], tuple[int, int]]]] = {}

    def _snapshot_metas_obj(self, metas: tuple[tuple[int, int], tuple[int, int], tuple[int, int]]) -> dict[str, dict[str, int]]:
        return {
            "claims": {"size": int(metas[0][0]), "mtime_ns": int(metas[0][1])},
            "edges": {"size": int(metas[1][0]), "mtime_ns": int(metas[1][1])},
            "nodes": {"size": int(metas[2][0]), "mtime_ns": int(metas[2][1])},
        }

    def _load_view_snapshot(self, *, scope: str, metas: tuple[tuple[int, int], tuple[int, int], tuple[int, int]]) -> ThoughtDbView | None:
        """Load a persisted ThoughtDbView snapshot when it matches current file metas (best-effort)."""

        path = self._view_snapshot_path(scope)
        try:
            obj = read_json(path, default=None)
        except Exception:
            obj = None
        if not isinstance(obj, dict):
            return None
        if str(obj.get("kind") or "").strip() != VIEW_SNAPSHOT_KIND:
            return None
        if str(obj.get("version") or "").strip() != VIEW_SNAPSHOT_VERSION:
            return None
        if str(obj.get("scope") or "").strip() != str(scope or "").strip():
            return None

        want = self._snapshot_metas_obj(metas)
        got = obj.get("source_metas")
        if not isinstance(got, dict):
            return None
        if got != want:
            return None

        view_obj = obj.get("view")
        if not isinstance(view_obj, dict):
            return None

        claims_by_id = view_obj.get("claims_by_id")
        nodes_by_id = view_obj.get("nodes_by_id")
        edges = view_obj.get("edges")
        redirects = view_obj.get("redirects_same_as")
        superseded_ids = view_obj.get("superseded_ids")
        retracted_ids = view_obj.get("retracted_ids")
        retracted_node_ids = view_obj.get("retracted_node_ids")

        if not isinstance(claims_by_id, dict):
            return None
        if not isinstance(nodes_by_id, dict):
            return None
        if not isinstance(edges, list):
            return None
        if not isinstance(redirects, dict):
            return None
        if not isinstance(superseded_ids, list):
            superseded_ids = []
        if not isinstance(retracted_ids, list):
            retracted_ids = []
        if not isinstance(retracted_node_ids, list):
            retracted_node_ids = []

        # Rebuild lightweight indices; snapshot stores only base view to avoid duplication.
        claims_by_tag: dict[str, set[str]] = {}
        for cid, c in claims_by_id.items():
            if not isinstance(c, dict):
                continue
            tags = c.get("tags") if isinstance(c.get("tags"), list) else []
            for t in tags:
                ts = str(t or "").strip()
                if ts:
                    claims_by_tag.setdefault(ts, set()).add(str(cid))

        nodes_by_tag: dict[str, set[str]] = {}
        for nid, n in nodes_by_id.items():
            if not isinstance(n, dict):
                continue
            tags = n.get("tags") if isinstance(n.get("tags"), list) else []
            for t in tags:
                ts = str(t or "").strip()
                if ts:
                    nodes_by_tag.setdefault(ts, set()).add(str(nid))

        edges_by_from: dict[str, list[dict[str, Any]]] = {}
        edges_by_to: dict[str, list[dict[str, Any]]] = {}
        for e in edges:
            if not isinstance(e, dict):
                continue
            frm = str(e.get("from_id") or "").strip()
            to = str(e.get("to_id") or "").strip()
            if frm:
                edges_by_from.setdefault(frm, []).append(e)
            if to:
                edges_by_to.setdefault(to, []).append(e)

        claim_ts: list[tuple[str, str]] = []
        for cid, c in claims_by_id.items():
            if not isinstance(c, dict):
                continue
            claim_ts.append((str(c.get("asserted_ts") or "").strip(), str(cid)))
        claim_ts.sort(key=lambda x: x[0], reverse=True)

        node_ts: list[tuple[str, str]] = []
        for nid, n in nodes_by_id.items():
            if not isinstance(n, dict):
                continue
            node_ts.append((str(n.get("asserted_ts") or "").strip(), str(nid)))
        node_ts.sort(key=lambda x: x[0], reverse=True)

        pid = self._project_id_for_scope(scope)
        return ThoughtDbView(
            scope=scope,
            project_id=pid,
            claims_by_id={str(k): v for k, v in claims_by_id.items() if str(k).strip() and isinstance(v, dict)},
            nodes_by_id={str(k): v for k, v in nodes_by_id.items() if str(k).strip() and isinstance(v, dict)},
            edges=[x for x in edges if isinstance(x, dict)],
            redirects_same_as={str(k): str(v).strip() for k, v in redirects.items() if str(k).strip() and str(v).strip()},
            superseded_ids={str(x).strip() for x in superseded_ids if str(x).strip()},
            retracted_ids={str(x).strip() for x in retracted_ids if str(x).strip()},
            retracted_node_ids={str(x).strip() for x in retracted_node_ids if str(x).strip()},
            claims_by_tag=claims_by_tag,
            nodes_by_tag=nodes_by_tag,
            edges_by_from=edges_by_from,
            edges_by_to=edges_by_to,
            claim_ids_by_asserted_ts_desc=[cid for _ts, cid in claim_ts if cid],
            node_ids_by_asserted_ts_desc=[nid for _ts, nid in node_ts if nid],
        )

    def _write_view_snapshot(
        self,
        *,
        scope: str,
        metas: tuple[tuple[int, int], tuple[int, int], tuple[int, int]],
        view: ThoughtDbView,
    ) -> None:
        """Persist a minimal view snapshot for faster cold loads (best-effort)."""

        path = self._view_snapshot_path(scope)
        obj: dict[str, Any] = {
            "kind": VIEW_SNAPSHOT_KIND,
            "version": VIEW_SNAPSHOT_VERSION,
            "built_ts": now_rfc3339(),
            "scope": scope,
            "project_id": str(view.project_id or ""),
            "source_metas": self._snapshot_metas_obj(metas),
            "view": {
                "claims_by_id": view.claims_by_id,
                "nodes_by_id": view.nodes_by_id,
                "edges": view.edges,
                "redirects_same_as": view.redirects_same_as,
                "superseded_ids": sorted(view.superseded_ids),
                "retracted_ids": sorted(view.retracted_ids),
                "retracted_node_ids": sorted(view.retracted_node_ids),
            },
        }
        atomic_write_json(path, obj)

    def update_cache_after_append(self, *, scope: str, obj: dict[str, Any]) -> None:
        """Incrementally update an in-memory cached view after an append (best-effort)."""

        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"

        cached = self._view_cache.get(sc)
        if not cached:
            return
        view = cached[0]
        if not isinstance(view, ThoughtDbView):
            return
        if not isinstance(obj, dict):
            return

        kind = str(obj.get("kind") or "").strip()
        v2: ThoughtDbView | None = None

        if kind == "claim":
            cid = str(obj.get("claim_id") or "").strip()
            if not cid:
                return
            claims_by_id = dict(view.claims_by_id)
            claims_by_id[cid] = obj

            claims_by_tag = dict(view.claims_by_tag)
            tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
            for t in tags:
                ts = str(t or "").strip()
                if not ts:
                    continue
                cur = claims_by_tag.get(ts)
                nxt = set(cur) if isinstance(cur, set) else set()
                nxt.add(cid)
                claims_by_tag[ts] = nxt

            ids = list(view.claim_ids_by_asserted_ts_desc)
            ids.insert(0, cid)

            v2 = ThoughtDbView(
                scope=view.scope,
                project_id=view.project_id,
                claims_by_id=claims_by_id,
                nodes_by_id=view.nodes_by_id,
                edges=view.edges,
                redirects_same_as=view.redirects_same_as,
                superseded_ids=view.superseded_ids,
                retracted_ids=view.retracted_ids,
                retracted_node_ids=view.retracted_node_ids,
                claims_by_tag=claims_by_tag,
                nodes_by_tag=view.nodes_by_tag,
                edges_by_from=view.edges_by_from,
                edges_by_to=view.edges_by_to,
                claim_ids_by_asserted_ts_desc=ids,
                node_ids_by_asserted_ts_desc=view.node_ids_by_asserted_ts_desc,
            )

        elif kind == "claim_retract":
            cid = str(obj.get("claim_id") or "").strip()
            if not cid:
                return
            retracted = set(view.retracted_ids)
            retracted.add(cid)
            v2 = ThoughtDbView(
                scope=view.scope,
                project_id=view.project_id,
                claims_by_id=view.claims_by_id,
                nodes_by_id=view.nodes_by_id,
                edges=view.edges,
                redirects_same_as=view.redirects_same_as,
                superseded_ids=view.superseded_ids,
                retracted_ids=retracted,
                retracted_node_ids=view.retracted_node_ids,
                claims_by_tag=view.claims_by_tag,
                nodes_by_tag=view.nodes_by_tag,
                edges_by_from=view.edges_by_from,
                edges_by_to=view.edges_by_to,
                claim_ids_by_asserted_ts_desc=view.claim_ids_by_asserted_ts_desc,
                node_ids_by_asserted_ts_desc=view.node_ids_by_asserted_ts_desc,
            )

        elif kind == "node":
            nid = str(obj.get("node_id") or "").strip()
            if not nid:
                return
            nodes_by_id = dict(view.nodes_by_id)
            nodes_by_id[nid] = obj

            nodes_by_tag = dict(view.nodes_by_tag)
            tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
            for t in tags:
                ts = str(t or "").strip()
                if not ts:
                    continue
                cur = nodes_by_tag.get(ts)
                nxt = set(cur) if isinstance(cur, set) else set()
                nxt.add(nid)
                nodes_by_tag[ts] = nxt

            ids = list(view.node_ids_by_asserted_ts_desc)
            ids.insert(0, nid)

            v2 = ThoughtDbView(
                scope=view.scope,
                project_id=view.project_id,
                claims_by_id=view.claims_by_id,
                nodes_by_id=nodes_by_id,
                edges=view.edges,
                redirects_same_as=view.redirects_same_as,
                superseded_ids=view.superseded_ids,
                retracted_ids=view.retracted_ids,
                retracted_node_ids=view.retracted_node_ids,
                claims_by_tag=view.claims_by_tag,
                nodes_by_tag=nodes_by_tag,
                edges_by_from=view.edges_by_from,
                edges_by_to=view.edges_by_to,
                claim_ids_by_asserted_ts_desc=view.claim_ids_by_asserted_ts_desc,
                node_ids_by_asserted_ts_desc=ids,
            )

        elif kind == "node_retract":
            nid = str(obj.get("node_id") or "").strip()
            if not nid:
                return
            retracted_nodes = set(view.retracted_node_ids)
            retracted_nodes.add(nid)
            v2 = ThoughtDbView(
                scope=view.scope,
                project_id=view.project_id,
                claims_by_id=view.claims_by_id,
                nodes_by_id=view.nodes_by_id,
                edges=view.edges,
                redirects_same_as=view.redirects_same_as,
                superseded_ids=view.superseded_ids,
                retracted_ids=view.retracted_ids,
                retracted_node_ids=retracted_nodes,
                claims_by_tag=view.claims_by_tag,
                nodes_by_tag=view.nodes_by_tag,
                edges_by_from=view.edges_by_from,
                edges_by_to=view.edges_by_to,
                claim_ids_by_asserted_ts_desc=view.claim_ids_by_asserted_ts_desc,
                node_ids_by_asserted_ts_desc=view.node_ids_by_asserted_ts_desc,
            )

        elif kind == "edge":
            et = str(obj.get("edge_type") or "").strip()
            frm = str(obj.get("from_id") or "").strip()
            to = str(obj.get("to_id") or "").strip()
            if not et or not frm or not to:
                return

            edges = list(view.edges)
            edges.append(obj)

            edges_by_from = dict(view.edges_by_from)
            curf = edges_by_from.get(frm)
            nxtf = list(curf) if isinstance(curf, list) else []
            nxtf.append(obj)
            edges_by_from[frm] = nxtf

            edges_by_to = dict(view.edges_by_to)
            curt = edges_by_to.get(to)
            nxtt = list(curt) if isinstance(curt, list) else []
            nxtt.append(obj)
            edges_by_to[to] = nxtt

            redirects = view.redirects_same_as
            if et == "same_as":
                redirects2 = dict(view.redirects_same_as)
                redirects2[frm] = to
                redirects = redirects2

            superseded = view.superseded_ids
            if et == "supersedes":
                superseded2 = set(view.superseded_ids)
                superseded2.add(frm)
                superseded = superseded2

            v2 = ThoughtDbView(
                scope=view.scope,
                project_id=view.project_id,
                claims_by_id=view.claims_by_id,
                nodes_by_id=view.nodes_by_id,
                edges=edges,
                redirects_same_as=redirects,
                superseded_ids=superseded,
                retracted_ids=view.retracted_ids,
                retracted_node_ids=view.retracted_node_ids,
                claims_by_tag=view.claims_by_tag,
                nodes_by_tag=view.nodes_by_tag,
                edges_by_from=edges_by_from,
                edges_by_to=edges_by_to,
                claim_ids_by_asserted_ts_desc=view.claim_ids_by_asserted_ts_desc,
                node_ids_by_asserted_ts_desc=view.node_ids_by_asserted_ts_desc,
            )

        if v2 is None:
            return

        metas = self._scope_metas(sc)
        self._view_cache[sc] = (v2, metas)

    def flush_snapshots_best_effort(self) -> None:
        """Persist view snapshots for any cached scopes (best-effort)."""

        for sc, (view, _metas) in list(self._view_cache.items()):
            if sc not in ("project", "global"):
                continue
            if not isinstance(view, ThoughtDbView):
                continue
            try:
                metas2 = self._scope_metas(sc)
                self._write_view_snapshot(scope=sc, metas=metas2, view=view)
                self._view_cache[sc] = (view, metas2)
            except Exception:
                continue

    def load_view(self, *, scope: str) -> ThoughtDbView:
        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"

        metas = self._scope_metas(sc)
        cached = self._view_cache.get(sc)
        if cached and cached[1] == metas:
            return cached[0]

        snap = None
        try:
            snap = self._load_view_snapshot(scope=sc, metas=metas)
        except Exception:
            snap = None
        if snap is not None:
            self._view_cache[sc] = (snap, metas)
            return snap

        claims_path = self._claims_path_for_scope(sc)
        edges_path = self._edges_path_for_scope(sc)
        nodes_path = self._nodes_path_for_scope(sc)

        claims_by_id: dict[str, dict[str, Any]] = {}
        claims_by_tag: dict[str, set[str]] = {}
        retracted: set[str] = set()

        for obj in self._iter_jsonl(claims_path):
            if not isinstance(obj, dict):
                continue
            k = str(obj.get("kind") or "").strip()
            if k == "claim":
                cid = str(obj.get("claim_id") or "").strip()
                if cid:
                    claims_by_id[cid] = obj
                    tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
                    for t in tags:
                        ts = str(t or "").strip()
                        if ts:
                            claims_by_tag.setdefault(ts, set()).add(cid)
            elif k == "claim_retract":
                cid = str(obj.get("claim_id") or "").strip()
                if cid:
                    retracted.add(cid)

        nodes_by_id: dict[str, dict[str, Any]] = {}
        nodes_by_tag: dict[str, set[str]] = {}
        retracted_nodes: set[str] = set()
        for obj in self._iter_jsonl(nodes_path):
            if not isinstance(obj, dict):
                continue
            k = str(obj.get("kind") or "").strip()
            if k == "node":
                nid = str(obj.get("node_id") or "").strip()
                if nid:
                    nodes_by_id[nid] = obj
                    tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
                    for t in tags:
                        ts = str(t or "").strip()
                        if ts:
                            nodes_by_tag.setdefault(ts, set()).add(nid)
            elif k == "node_retract":
                nid = str(obj.get("node_id") or "").strip()
                if nid:
                    retracted_nodes.add(nid)

        edges: list[dict[str, Any]] = []
        edges_by_from: dict[str, list[dict[str, Any]]] = {}
        edges_by_to: dict[str, list[dict[str, Any]]] = {}
        redirects: dict[str, str] = {}
        superseded: set[str] = set()
        for obj in self._iter_jsonl(edges_path):
            if not isinstance(obj, dict):
                continue
            if str(obj.get("kind") or "").strip() != "edge":
                continue
            edges.append(obj)
            et = str(obj.get("edge_type") or "").strip()
            frm = str(obj.get("from_id") or "").strip()
            to = str(obj.get("to_id") or "").strip()
            if frm:
                edges_by_from.setdefault(frm, []).append(obj)
            if to:
                edges_by_to.setdefault(to, []).append(obj)
            if et == "same_as" and frm and to:
                redirects[frm] = to
            if et == "supersedes" and frm and to:
                superseded.add(frm)

        pid = self._project_id_for_scope(sc)
        # Precompute time-sorted ids for common retrieval patterns.
        claim_ts: list[tuple[str, str]] = []
        for cid, c in claims_by_id.items():
            if not isinstance(c, dict):
                continue
            claim_ts.append((str(c.get("asserted_ts") or "").strip(), cid))
        claim_ts.sort(key=lambda x: x[0], reverse=True)

        node_ts: list[tuple[str, str]] = []
        for nid, n in nodes_by_id.items():
            if not isinstance(n, dict):
                continue
            node_ts.append((str(n.get("asserted_ts") or "").strip(), nid))
        node_ts.sort(key=lambda x: x[0], reverse=True)

        view = ThoughtDbView(
            scope=sc,
            project_id=pid,
            claims_by_id=claims_by_id,
            nodes_by_id=nodes_by_id,
            edges=edges,
            claims_by_tag=claims_by_tag,
            nodes_by_tag=nodes_by_tag,
            edges_by_from=edges_by_from,
            edges_by_to=edges_by_to,
            claim_ids_by_asserted_ts_desc=[cid for _ts, cid in claim_ts if cid],
            node_ids_by_asserted_ts_desc=[nid for _ts, nid in node_ts if nid],
            redirects_same_as=redirects,
            superseded_ids=superseded,
            retracted_ids=retracted,
            retracted_node_ids=retracted_nodes,
        )
        self._view_cache[sc] = (view, metas)
        try:
            self._write_view_snapshot(scope=sc, metas=metas, view=view)
        except Exception:
            pass
        return view

    def existing_signatures(self, *, scope: str) -> set[str]:
        v = self.load_view(scope=scope)
        out: set[str] = set()
        for c in v.iter_claims(include_inactive=True, include_aliases=True):
            if not isinstance(c, dict):
                continue
            ct = str(c.get("claim_type") or "").strip()
            text = str(c.get("text") or "").strip()
            if not ct or not text:
                continue
            out.add(claim_signature(claim_type=ct, scope=v.scope, project_id=v.project_id, text=text))
        return out

    def existing_signature_map(self, *, scope: str) -> dict[str, str]:
        """Return signature -> canonical claim_id for the scope (best-effort)."""

        v = self.load_view(scope=scope)
        out: dict[str, str] = {}
        for cid, c in v.claims_by_id.items():
            if cid in v.redirects_same_as:
                continue
            if not isinstance(c, dict):
                continue
            ct = str(c.get("claim_type") or "").strip()
            text = str(c.get("text") or "").strip()
            if not ct or not text:
                continue
            sig = claim_signature(claim_type=ct, scope=v.scope, project_id=v.project_id, text=text)
            if sig and sig not in out:
                out[sig] = cid
        return out

    def existing_edge_keys(self, *, scope: str) -> set[str]:
        v = self.load_view(scope=scope)
        out: set[str] = set()
        for e in v.edges:
            if not isinstance(e, dict):
                continue
            et = str(e.get("edge_type") or "").strip()
            frm = str(e.get("from_id") or "").strip()
            to = str(e.get("to_id") or "").strip()
            if not et or not frm or not to:
                continue
            out.add(edge_key(edge_type=et, from_id=frm, to_id=to))
        return out
