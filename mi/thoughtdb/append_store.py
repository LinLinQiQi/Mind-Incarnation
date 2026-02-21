from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..core.storage import append_jsonl, now_rfc3339
from .model import THOUGHTDB_VERSION, new_claim_id, new_edge_id, new_node_id


class ThoughtAppendStore:
    """Append-only write path for Thought DB claims/nodes/edges."""

    def __init__(
        self,
        *,
        claims_path_for_scope: Callable[[str], Path],
        edges_path_for_scope: Callable[[str], Path],
        nodes_path_for_scope: Callable[[str], Path],
        project_id_for_scope: Callable[[str], str],
        ensure_scope_dirs: Callable[[str], None],
        on_append: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._claims_path_for_scope = claims_path_for_scope
        self._edges_path_for_scope = edges_path_for_scope
        self._nodes_path_for_scope = nodes_path_for_scope
        self._project_id_for_scope = project_id_for_scope
        self._ensure_scope_dirs = ensure_scope_dirs
        self._on_append = on_append

    def append_claim_create(
        self,
        *,
        claim_type: str,
        text: str,
        scope: str,
        visibility: str,
        valid_from: str | None,
        valid_to: str | None,
        tags: list[str],
        source_event_ids: list[str],
        confidence: float,
        notes: str,
    ) -> str:
        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"
        vis = (visibility or "project").strip()
        if vis not in ("private", "project", "global"):
            vis = "project"

        ct = (claim_type or "").strip()
        if ct not in ("fact", "preference", "assumption", "goal"):
            ct = "fact"

        t = (text or "").strip()
        if not t:
            raise ValueError("claim text is empty")

        self._ensure_scope_dirs(sc)
        cid = new_claim_id()
        pid = self._project_id_for_scope(sc)
        ev_ids = [str(x).strip() for x in (source_event_ids or []) if str(x).strip()]
        ev_ids = ev_ids[:8]
        refs = [{"kind": "evidence_event", "event_id": x} for x in ev_ids]
        obj: dict[str, Any] = {
            "kind": "claim",
            "version": THOUGHTDB_VERSION,
            "claim_id": cid,
            "claim_type": ct,
            "text": t,
            "visibility": vis,
            "scope": sc,
            "project_id": pid,
            "asserted_ts": now_rfc3339(),
            "valid_from": (str(valid_from).strip() if isinstance(valid_from, str) and str(valid_from).strip() else None),
            "valid_to": (str(valid_to).strip() if isinstance(valid_to, str) and str(valid_to).strip() else None),
            "status": "active",
            "tags": [str(x).strip() for x in (tags or []) if str(x).strip()][:20],
            "source_refs": refs,
            "confidence": float(confidence),
            "notes": (notes or "").strip(),
        }
        append_jsonl(self._claims_path_for_scope(sc), obj)
        try:
            self._on_append(sc, obj)
        except Exception:
            pass
        return cid

    def append_claim_retract(
        self,
        *,
        claim_id: str,
        scope: str,
        rationale: str,
        source_event_ids: list[str],
    ) -> None:
        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"
        cid = (claim_id or "").strip()
        if not cid:
            raise ValueError("claim_id is required")

        self._ensure_scope_dirs(sc)
        ev_ids = [str(x).strip() for x in (source_event_ids or []) if str(x).strip()]
        refs = [{"kind": "evidence_event", "event_id": x} for x in ev_ids[:8]]
        obj: dict[str, Any] = {
            "kind": "claim_retract",
            "version": THOUGHTDB_VERSION,
            "ts": now_rfc3339(),
            "claim_id": cid,
            "rationale": (rationale or "").strip(),
            "source_refs": refs,
        }
        append_jsonl(self._claims_path_for_scope(sc), obj)
        try:
            self._on_append(sc, obj)
        except Exception:
            pass

    def append_node_create(
        self,
        *,
        node_type: str,
        title: str,
        text: str,
        scope: str,
        visibility: str,
        tags: list[str],
        source_event_ids: list[str],
        confidence: float,
        notes: str,
    ) -> str:
        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"
        vis = (visibility or "project").strip()
        if vis not in ("private", "project", "global"):
            vis = "project"

        nt = (node_type or "").strip()
        if nt not in ("decision", "action", "summary"):
            raise ValueError(f"invalid node_type: {node_type!r}")

        t = (text or "").strip()
        if not t:
            raise ValueError("node text is empty")

        ttl = (title or "").strip()
        if not ttl:
            ttl = t.splitlines()[0].strip()
        if len(ttl) > 140:
            ttl = ttl[:137] + "..."

        try:
            conf = float(confidence)
        except Exception:
            conf = 0.0
        conf = max(0.0, min(1.0, conf))

        self._ensure_scope_dirs(sc)
        nid = new_node_id()
        pid = self._project_id_for_scope(sc)
        ev_ids = [str(x).strip() for x in (source_event_ids or []) if str(x).strip()][:12]
        refs = [{"kind": "evidence_event", "event_id": x} for x in ev_ids]
        obj: dict[str, Any] = {
            "kind": "node",
            "version": THOUGHTDB_VERSION,
            "node_id": nid,
            "node_type": nt,
            "title": ttl,
            "text": t,
            "visibility": vis,
            "scope": sc,
            "project_id": pid,
            "asserted_ts": now_rfc3339(),
            "tags": [str(x).strip() for x in (tags or []) if str(x).strip()][:20],
            "source_refs": refs,
            "confidence": conf,
            "notes": (notes or "").strip(),
        }
        append_jsonl(self._nodes_path_for_scope(sc), obj)
        try:
            self._on_append(sc, obj)
        except Exception:
            pass
        return nid

    def append_node_retract(
        self,
        *,
        node_id: str,
        scope: str,
        rationale: str,
        source_event_ids: list[str],
    ) -> None:
        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"
        nid = (node_id or "").strip()
        if not nid:
            raise ValueError("node_id is required")

        self._ensure_scope_dirs(sc)
        ev_ids = [str(x).strip() for x in (source_event_ids or []) if str(x).strip()]
        refs = [{"kind": "evidence_event", "event_id": x} for x in ev_ids[:8]]
        obj: dict[str, Any] = {
            "kind": "node_retract",
            "version": THOUGHTDB_VERSION,
            "ts": now_rfc3339(),
            "node_id": nid,
            "rationale": (rationale or "").strip(),
            "source_refs": refs,
        }
        append_jsonl(self._nodes_path_for_scope(sc), obj)
        try:
            self._on_append(sc, obj)
        except Exception:
            pass

    def append_edge(
        self,
        *,
        edge_type: str,
        from_id: str,
        to_id: str,
        scope: str,
        visibility: str,
        source_event_ids: list[str],
        notes: str,
    ) -> str:
        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"
        et = (edge_type or "").strip()
        allowed = ("depends_on", "supports", "contradicts", "derived_from", "mentions", "supersedes", "same_as")
        if et not in allowed:
            raise ValueError(f"invalid edge_type: {edge_type!r}")
        frm = (from_id or "").strip()
        to = (to_id or "").strip()
        if not frm or not to:
            raise ValueError("edge from_id/to_id are required")

        vis = (visibility or "project").strip()
        if vis not in ("private", "project", "global"):
            vis = "project"

        self._ensure_scope_dirs(sc)
        eid = new_edge_id()
        pid = self._project_id_for_scope(sc)
        ev_ids = [str(x).strip() for x in (source_event_ids or []) if str(x).strip()]
        refs = [{"kind": "evidence_event", "event_id": x} for x in ev_ids[:8]]
        obj: dict[str, Any] = {
            "kind": "edge",
            "version": THOUGHTDB_VERSION,
            "edge_id": eid,
            "edge_type": et,
            "from_id": frm,
            "to_id": to,
            "visibility": vis,
            "scope": sc,
            "project_id": pid,
            "asserted_ts": now_rfc3339(),
            "source_refs": refs,
            "notes": (notes or "").strip(),
        }
        append_jsonl(self._edges_path_for_scope(sc), obj)
        try:
            self._on_append(sc, obj)
        except Exception:
            pass
        return eid
