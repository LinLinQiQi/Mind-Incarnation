from __future__ import annotations

from typing import Any

from .model import ThoughtDbView


def claim_active_and_valid(view: ThoughtDbView, claim_id: str, *, as_of_ts: str) -> bool:
    """Return True iff claim_id refers to an active, non-alias claim valid at as_of_ts (best-effort)."""

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


def node_active(view: ThoughtDbView, node_id: str) -> bool:
    """Return True iff node_id refers to an active, non-alias node (best-effort)."""

    nid = str(node_id or "").strip()
    if not nid:
        return False
    if nid in view.redirects_same_as:
        return False
    return view.node_status(nid) == "active"


def edges_adjacent(view: ThoughtDbView, node_id: str) -> list[dict[str, Any]]:
    """Return edges adjacent to node_id (best-effort; both in+out)."""

    nid = str(node_id or "").strip()
    if not nid:
        return []
    out: list[dict[str, Any]] = []
    out.extend([x for x in (view.edges_by_from.get(nid) or []) if isinstance(x, dict)])
    out.extend([x for x in (view.edges_by_to.get(nid) or []) if isinstance(x, dict)])
    return out


__all__ = ["claim_active_and_valid", "edges_adjacent", "node_active"]

