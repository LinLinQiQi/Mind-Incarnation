from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

THOUGHTDB_VERSION = "v1"
VIEW_SNAPSHOT_KIND = "mi.thoughtdb.view_snapshot"
VIEW_SNAPSHOT_VERSION = "v1"


def new_claim_id() -> str:
    return f"cl_{time.time_ns()}_{secrets.token_hex(4)}"


def new_edge_id() -> str:
    return f"ed_{time.time_ns()}_{secrets.token_hex(4)}"


def new_node_id() -> str:
    return f"nd_{time.time_ns()}_{secrets.token_hex(4)}"


def _norm_text(text: str) -> str:
    return " ".join((text or "").strip().split()).lower()


def claim_signature(*, claim_type: str, scope: str, project_id: str, text: str) -> str:
    """Stable signature for deduping obvious identical claims."""
    base = f"{claim_type.strip()}|{scope.strip()}|{project_id.strip()}|{_norm_text(text)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def min_visibility(a: str, b: str) -> str:
    """Return the more restrictive visibility label (private < project < global)."""

    rank = {"private": 0, "project": 1, "global": 2}
    aa = (a or "").strip()
    bb = (b or "").strip()
    if aa not in rank:
        aa = "project"
    if bb not in rank:
        bb = "project"
    return aa if rank[aa] <= rank[bb] else bb


def edge_key(*, edge_type: str, from_id: str, to_id: str) -> str:
    return f"{(edge_type or '').strip()}|{(from_id or '').strip()}|{(to_id or '').strip()}"


def follow_redirects(start: str, redirects: dict[str, str], *, limit: int = 20) -> str:
    cur = (start or "").strip()
    if not cur:
        return ""
    seen: set[str] = set()
    for _ in range(max(1, limit)):
        if cur in seen:
            break
        seen.add(cur)
        nxt = redirects.get(cur)
        if not nxt or nxt == cur:
            break
        cur = nxt
    return cur


@dataclass(frozen=True)
class ThoughtDbView:
    """Materialized view of Thought DB for a single scope (project or global)."""

    scope: str
    project_id: str
    claims_by_id: dict[str, dict[str, Any]]
    nodes_by_id: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]
    redirects_same_as: dict[str, str]
    superseded_ids: set[str]
    retracted_ids: set[str]
    retracted_node_ids: set[str]
    # Lightweight indices to avoid repeatedly scanning large dicts in hot paths.
    claims_by_tag: dict[str, set[str]] = field(default_factory=dict)
    nodes_by_tag: dict[str, set[str]] = field(default_factory=dict)
    edges_by_from: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    edges_by_to: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    claim_ids_by_asserted_ts_desc: list[str] = field(default_factory=list)
    node_ids_by_asserted_ts_desc: list[str] = field(default_factory=list)

    def resolve_id(self, claim_id: str) -> str:
        return follow_redirects(claim_id, self.redirects_same_as)

    def claim_status(self, claim_id: str) -> str:
        cid = (claim_id or "").strip()
        if not cid:
            return "unknown"
        if cid in self.retracted_ids:
            return "retracted"
        if cid in self.superseded_ids:
            return "superseded"
        return "active"

    def iter_claims(
        self,
        *,
        include_inactive: bool,
        include_aliases: bool,
        as_of_ts: str = "",
    ) -> Iterable[dict[str, Any]]:
        """Iterate claims (best-effort) with derived status and redirect info.

        - include_aliases=False hides claims that have a same_as redirect.
        - include_inactive=False hides superseded/retracted claims.
        - as_of_ts (RFC3339) filters by valid_from/valid_to when provided.
        """

        t = (as_of_ts or "").strip()
        for cid, c in self.claims_by_id.items():
            if not isinstance(c, dict):
                continue
            if not include_aliases and cid in self.redirects_same_as:
                continue
            status = self.claim_status(cid)
            if not include_inactive and status != "active":
                continue

            if t:
                vf = c.get("valid_from")
                vt = c.get("valid_to")
                if isinstance(vf, str) and vf.strip() and vf.strip() > t:
                    continue
                if isinstance(vt, str) and vt.strip() and t >= vt.strip():
                    continue

            out = dict(c)
            out["status"] = status
            out["canonical_id"] = self.resolve_id(cid)
            yield out

    def node_status(self, node_id: str) -> str:
        nid = (node_id or "").strip()
        if not nid:
            return "unknown"
        if nid in self.retracted_node_ids:
            return "retracted"
        if nid in self.superseded_ids:
            return "superseded"
        return "active"

    def iter_nodes(
        self,
        *,
        include_inactive: bool,
        include_aliases: bool,
    ) -> Iterable[dict[str, Any]]:
        """Iterate nodes (Decision/Action/Summary) with derived status/redirect info."""

        for nid, n in self.nodes_by_id.items():
            if not isinstance(n, dict):
                continue
            if not include_aliases and nid in self.redirects_same_as:
                continue
            status = self.node_status(nid)
            if not include_inactive and status != "active":
                continue
            out = dict(n)
            out["status"] = status
            out["canonical_id"] = self.resolve_id(nid)
            yield out
