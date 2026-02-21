from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..core.paths import GlobalPaths, ProjectPaths
from ..memory.service import MemoryService
from .context import ThoughtDbContext, build_decide_next_thoughtdb_context
from .graph import build_subgraph_for_id
from .model import claim_signature
from .store import ThoughtDbStore
from .why import (
    WhyTraceOutcome,
    collect_candidate_claims,
    collect_candidate_claims_for_target,
    find_evidence_event,
    query_from_evidence_event,
    run_why_trace,
)


class ThoughtDbApplicationService:
    """Application-facing Thought DB helpers.

    This layer keeps command/runtime code focused on orchestration and IO while
    centralizing common Thought DB retrieval/lookup patterns.
    """

    def __init__(
        self,
        *,
        tdb: ThoughtDbStore,
        project_paths: ProjectPaths,
        mem: MemoryService | None = None,
        mind: Any | None = None,
    ) -> None:
        self._tdb = tdb
        self._pp = project_paths
        self._mem = mem
        self._mind = mind

    def bind_mind(self, mind: Any) -> None:
        self._mind = mind

    def bind_mem(self, mem: MemoryService) -> None:
        self._mem = mem

    def build_decide_context(
        self,
        *,
        as_of_ts: str,
        task: str,
        hands_last_message: str,
        recent_evidence: list[dict[str, Any]],
        max_nodes: int = 6,
        max_values_claims: int = 8,
        max_pref_goal_claims: int = 8,
        max_query_claims: int = 10,
        max_edges: int = 20,
    ) -> ThoughtDbContext:
        return build_decide_next_thoughtdb_context(
            tdb=self._tdb,
            as_of_ts=str(as_of_ts or "").strip(),
            task=str(task or ""),
            hands_last_message=str(hands_last_message or ""),
            recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
            mem=self._mem,
            max_nodes=max_nodes,
            max_values_claims=max_values_claims,
            max_pref_goal_claims=max_pref_goal_claims,
            max_query_claims=max_query_claims,
            max_edges=max_edges,
        )

    def build_workflow_edit_context(
        self,
        *,
        as_of_ts: str,
        task: str,
        recent_evidence: list[dict[str, Any]] | None = None,
    ) -> ThoughtDbContext:
        return self.build_decide_context(
            as_of_ts=as_of_ts,
            task=task,
            hands_last_message="",
            recent_evidence=recent_evidence if isinstance(recent_evidence, list) else [],
        )

    def build_subgraph(
        self,
        *,
        scope: str,
        root_id: str,
        depth: int,
        direction: str,
        edge_types: set[str] | None,
        include_inactive: bool,
        include_aliases: bool,
        as_of_ts: str = "",
    ) -> dict[str, Any]:
        return build_subgraph_for_id(
            tdb=self._tdb,
            scope=str(scope or "project"),
            root_id=str(root_id or ""),
            depth=int(depth),
            direction=str(direction or "both"),
            edge_types=edge_types if isinstance(edge_types, set) else set(),
            include_inactive=bool(include_inactive),
            include_aliases=bool(include_aliases),
            as_of_ts=str(as_of_ts or ""),
        )

    @staticmethod
    def _norm_scope(scope: str) -> str:
        sc = str(scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"
        return sc

    def _view(self, scope: str):
        return self._tdb.load_view(scope=self._norm_scope(scope))

    def _enrich_claim(self, *, view: Any, obj: dict[str, Any], claim_id: str, requested_id: str = "") -> dict[str, Any]:
        out = dict(obj)
        out["status"] = view.claim_status(claim_id)
        out["canonical_id"] = view.resolve_id(claim_id)
        rid = str(requested_id or "").strip()
        if rid and rid != claim_id:
            out["requested_id"] = rid
        return out

    def _enrich_node(self, *, view: Any, obj: dict[str, Any], node_id: str, requested_id: str = "") -> dict[str, Any]:
        out = dict(obj)
        out["status"] = view.node_status(node_id)
        out["canonical_id"] = view.resolve_id(node_id)
        rid = str(requested_id or "").strip()
        if rid and rid != node_id:
            out["requested_id"] = rid
        return out

    def find_claim(self, *, scope: str, claim_id: str) -> tuple[str, dict[str, Any] | None]:
        cid = str(claim_id or "").strip()
        if not cid:
            return "", None
        sc = self._norm_scope(scope)
        view = self._view(sc)
        if cid in view.claims_by_id:
            return sc, self._enrich_claim(view=view, obj=view.claims_by_id[cid], claim_id=cid)
        canon = view.resolve_id(cid)
        if canon and canon in view.claims_by_id:
            return sc, self._enrich_claim(view=view, obj=view.claims_by_id[canon], claim_id=canon, requested_id=cid)
        return "", None

    def find_claim_effective(self, claim_id: str) -> tuple[str, dict[str, Any] | None]:
        cid = str(claim_id or "").strip()
        if not cid:
            return "", None
        for scope in ("project", "global"):
            found_scope, obj = self.find_claim(scope=scope, claim_id=cid)
            if obj:
                return found_scope, obj
        return "", None

    def find_node(self, *, scope: str, node_id: str) -> tuple[str, dict[str, Any] | None]:
        nid = str(node_id or "").strip()
        if not nid:
            return "", None
        sc = self._norm_scope(scope)
        view = self._view(sc)
        if nid in view.nodes_by_id:
            return sc, self._enrich_node(view=view, obj=view.nodes_by_id[nid], node_id=nid)
        canon = view.resolve_id(nid)
        if canon and canon in view.nodes_by_id:
            return sc, self._enrich_node(view=view, obj=view.nodes_by_id[canon], node_id=canon, requested_id=nid)
        return "", None

    def find_node_effective(self, node_id: str) -> tuple[str, dict[str, Any] | None]:
        nid = str(node_id or "").strip()
        if not nid:
            return "", None
        for scope in ("project", "global"):
            found_scope, obj = self.find_node(scope=scope, node_id=nid)
            if obj:
                return found_scope, obj
        return "", None

    def list_effective_claims(
        self,
        *,
        include_inactive: bool,
        include_aliases: bool,
        as_of_ts: str,
        filter_fn: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        proj = self._view("project")
        glob = self._view("global")
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _ok(obj: dict[str, Any]) -> bool:
            if filter_fn is None:
                return True
            try:
                return bool(filter_fn(obj))
            except Exception:
                return False

        def _sig_for(c: dict[str, Any]) -> str:
            ct = str(c.get("claim_type") or "").strip()
            text = str(c.get("text") or "").strip()
            return claim_signature(claim_type=ct, scope="effective", project_id="", text=text)

        for c in proj.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases, as_of_ts=as_of_ts):
            if not isinstance(c, dict):
                continue
            if not _ok(c):
                continue
            sig = _sig_for(c)
            if sig:
                seen.add(sig)
            out.append(c)

        for c in glob.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases, as_of_ts=as_of_ts):
            if not isinstance(c, dict):
                continue
            if not _ok(c):
                continue
            sig = _sig_for(c)
            if sig and sig in seen:
                continue
            out.append(c)

        out.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)
        return out

    def list_effective_nodes(
        self,
        *,
        include_inactive: bool,
        include_aliases: bool,
        filter_fn: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        proj = self._view("project")
        glob = self._view("global")
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _ok(obj: dict[str, Any]) -> bool:
            if filter_fn is None:
                return True
            try:
                return bool(filter_fn(obj))
            except Exception:
                return False

        for n in proj.iter_nodes(include_inactive=include_inactive, include_aliases=include_aliases):
            if not isinstance(n, dict):
                continue
            if not _ok(n):
                continue
            nid = str(n.get("node_id") or "").strip()
            if nid:
                seen.add(nid)
            out.append(n)

        for n in glob.iter_nodes(include_inactive=include_inactive, include_aliases=include_aliases):
            if not isinstance(n, dict):
                continue
            if not _ok(n):
                continue
            nid = str(n.get("node_id") or "").strip()
            if nid and nid in seen:
                continue
            out.append(n)

        out.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)
        return out

    def related_edges_for_id(self, *, scope: str, item_id: str) -> list[dict[str, Any]]:
        iid = str(item_id or "").strip()
        if not iid:
            return []
        sc = self._norm_scope(scope)
        v = self._view(sc)
        canon = v.resolve_id(iid)
        out: list[dict[str, Any]] = []
        for e in v.edges:
            if not isinstance(e, dict):
                continue
            frm = str(e.get("from_id") or "").strip()
            to = str(e.get("to_id") or "").strip()
            if iid in (frm, to) or (canon and canon in (frm, to)):
                out.append(e)
        return out

    def find_evidence_event(self, *, evidence_log_path: Path, event_id: str) -> dict[str, Any] | None:
        return find_evidence_event(evidence_log_path=evidence_log_path, event_id=event_id)

    def find_evidence_event_prefer_project(
        self,
        *,
        home_dir: Path,
        event_id: str,
        global_only: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        eid = str(event_id or "").strip()
        if not eid:
            return "", None
        if bool(global_only):
            gobj = find_evidence_event(evidence_log_path=GlobalPaths(home_dir=home_dir).global_evidence_log_path, event_id=eid)
            return ("global", gobj) if isinstance(gobj, dict) else ("", None)

        pobj = find_evidence_event(evidence_log_path=self._pp.evidence_log_path, event_id=eid)
        if isinstance(pobj, dict):
            return "project", pobj
        gobj = find_evidence_event(evidence_log_path=GlobalPaths(home_dir=home_dir).global_evidence_log_path, event_id=eid)
        return ("global", gobj) if isinstance(gobj, dict) else ("", None)

    @staticmethod
    def query_from_evidence_event(target_obj: dict[str, Any]) -> str:
        return query_from_evidence_event(target_obj)

    def collect_why_candidates_for_target(
        self,
        *,
        target_obj: dict[str, Any],
        query: str,
        top_k: int,
        as_of_ts: str,
        target_event_id: str = "",
    ) -> list[dict[str, Any]]:
        if self._mem is None:
            return []
        return collect_candidate_claims_for_target(
            tdb=self._tdb,
            mem=self._mem,
            project_paths=self._pp,
            target_obj=target_obj,
            query=query,
            top_k=top_k,
            as_of_ts=as_of_ts,
            target_event_id=target_event_id,
        )

    def run_end_why_candidates(
        self,
        *,
        target_obj: dict[str, Any],
        target_event_id: str,
        top_k: int,
        as_of_ts: str,
    ) -> tuple[str, list[dict[str, Any]], list[str]]:
        query = self.query_from_evidence_event(target_obj if isinstance(target_obj, dict) else {})
        candidates = self.collect_why_candidates_for_target(
            target_obj=target_obj if isinstance(target_obj, dict) else {},
            query=query,
            top_k=top_k,
            as_of_ts=as_of_ts,
            target_event_id=target_event_id,
        )
        candidate_ids = [
            str(c.get("claim_id") or "").strip()
            for c in candidates
            if isinstance(c, dict) and str(c.get("claim_id") or "").strip()
        ]
        return query, candidates, candidate_ids

    def collect_why_candidates(
        self,
        *,
        query: str,
        top_k: int,
        target_event_id: str = "",
    ) -> list[dict[str, Any]]:
        if self._mem is None:
            return []
        return collect_candidate_claims(
            tdb=self._tdb,
            mem=self._mem,
            project_paths=self._pp,
            query=query,
            top_k=top_k,
            target_event_id=target_event_id,
        )

    def run_why_trace_for_target(
        self,
        *,
        target: dict[str, Any],
        candidate_claims: list[dict[str, Any]],
        as_of_ts: str,
        write_edges_from_event_id: str,
        min_write_confidence: float = 0.7,
    ) -> WhyTraceOutcome:
        if self._mind is None:
            raise RuntimeError("mind provider is not bound")
        if self._mem is None:
            raise RuntimeError("memory service is not bound")
        return run_why_trace(
            mind=self._mind,
            tdb=self._tdb,
            mem=self._mem,
            project_paths=self._pp,
            target=target,
            candidate_claims=candidate_claims,
            as_of_ts=as_of_ts,
            write_edges_from_event_id=write_edges_from_event_id,
            min_write_confidence=float(min_write_confidence),
        )
