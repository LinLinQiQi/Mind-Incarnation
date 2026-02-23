from __future__ import annotations

from typing import Any

from ..memory.text import truncate
from .store import ThoughtDbView


def _extract_source_event_ids(source_refs: Any, *, limit: int) -> list[str]:
    refs = source_refs if isinstance(source_refs, list) else []
    ev_ids: list[str] = []
    for r in refs:
        if isinstance(r, dict) and r.get("event_id"):
            ev_ids.append(str(r.get("event_id")))
    return [x for x in ev_ids if str(x).strip()][: max(0, int(limit))]


def compact_claim_for_context(c: dict[str, Any], *, view: ThoughtDbView) -> dict[str, Any]:
    cid = str(c.get("claim_id") or "").strip()
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
        "source_event_ids": _extract_source_event_ids(c.get("source_refs"), limit=6),
    }


def compact_edge_for_context(e: dict[str, Any], *, scope: str) -> dict[str, Any]:
    return {
        "edge_type": str(e.get("edge_type") or "").strip(),
        "from_id": str(e.get("from_id") or "").strip(),
        "to_id": str(e.get("to_id") or "").strip(),
        "scope": scope,
        "notes": truncate(str(e.get("notes") or "").strip(), 160),
    }


def compact_node_for_context(n: dict[str, Any], *, view: ThoughtDbView) -> dict[str, Any]:
    nid = str(n.get("node_id") or "").strip()
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
        "source_event_ids": _extract_source_event_ids(n.get("source_refs"), limit=6),
    }


def compact_claim_for_values(c: dict[str, Any], *, view: ThoughtDbView) -> dict[str, Any]:
    cid = str(c.get("claim_id") or "").strip()
    return {
        "claim_id": cid,
        "canonical_id": view.resolve_id(cid),
        "status": view.claim_status(cid),
        "claim_type": str(c.get("claim_type") or "").strip(),
        "scope": str(c.get("scope") or "").strip(),
        "visibility": str(c.get("visibility") or "").strip(),
        "asserted_ts": str(c.get("asserted_ts") or "").strip(),
        "valid_from": c.get("valid_from"),
        "valid_to": c.get("valid_to"),
        "text": truncate(str(c.get("text") or "").strip(), 420),
        "tags": [str(x) for x in (c.get("tags") or []) if str(x).strip()][:16] if isinstance(c.get("tags"), list) else [],
        "source_event_ids": _extract_source_event_ids(c.get("source_refs"), limit=6),
    }


def compact_claim_for_graph(view: ThoughtDbView, cid: str, *, status: str, canonical_id: str) -> dict[str, Any]:
    c = view.claims_by_id.get(cid) if isinstance(view.claims_by_id.get(cid), dict) else {}
    tags = c.get("tags") if isinstance(c.get("tags"), list) else []
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
        "text": truncate(str(c.get("text") or "").strip(), 800),
        "tags": [str(x) for x in tags if str(x).strip()][:24],
        "source_event_ids": _extract_source_event_ids(c.get("source_refs"), limit=8),
    }


def compact_node_for_graph(view: ThoughtDbView, nid: str, *, status: str, canonical_id: str) -> dict[str, Any]:
    n = view.nodes_by_id.get(nid) if isinstance(view.nodes_by_id.get(nid), dict) else {}
    tags = n.get("tags") if isinstance(n.get("tags"), list) else []
    return {
        "node_id": nid,
        "canonical_id": canonical_id,
        "status": status,
        "node_type": str(n.get("node_type") or "").strip(),
        "scope": str(n.get("scope") or view.scope).strip(),
        "visibility": str(n.get("visibility") or "").strip(),
        "asserted_ts": str(n.get("asserted_ts") or "").strip(),
        "title": truncate(str(n.get("title") or "").strip(), 240),
        "text": truncate(str(n.get("text") or "").strip(), 1000),
        "tags": [str(x) for x in tags if str(x).strip()][:24],
        "source_event_ids": _extract_source_event_ids(n.get("source_refs"), limit=8),
    }


__all__ = [
    "compact_claim_for_context",
    "compact_edge_for_context",
    "compact_node_for_context",
    "compact_claim_for_graph",
    "compact_node_for_graph",
    "compact_claim_for_values",
]

