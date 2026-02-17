from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core.paths import GlobalPaths, ProjectPaths
from ..core.storage import append_jsonl, ensure_dir, iter_jsonl, now_rfc3339, read_json, atomic_write_json


THOUGHTDB_VERSION = "v1"
_VIEW_SNAPSHOT_KIND = "mi.thoughtdb.view_snapshot"
_VIEW_SNAPSHOT_VERSION = "v1"


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


def _min_visibility(a: str, b: str) -> str:
    """Return the more restrictive visibility label (private < project < global)."""

    rank = {"private": 0, "project": 1, "global": 2}
    aa = (a or "").strip()
    bb = (b or "").strip()
    if aa not in rank:
        aa = "project"
    if bb not in rank:
        bb = "project"
    return aa if rank[aa] <= rank[bb] else bb


def _edge_key(*, edge_type: str, from_id: str, to_id: str) -> str:
    return f"{(edge_type or '').strip()}|{(from_id or '').strip()}|{(to_id or '').strip()}"


def _follow_redirects(start: str, redirects: dict[str, str], *, limit: int = 20) -> str:
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
        return _follow_redirects(claim_id, self.redirects_same_as)

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


class ThoughtDbStore:
    """Append-only Thought DB store (Claims + Edges).

    Source of truth for MI runs remains EvidenceLog + raw transcripts. Thought DB
    adds durable, reusable Claim/Edge records that reference EvidenceLog event_id.
    """

    def __init__(self, *, home_dir: Path, project_paths: ProjectPaths) -> None:
        self._home_dir = Path(home_dir).expanduser().resolve()
        self._project_paths = project_paths
        self._gp = GlobalPaths(home_dir=self._home_dir)
        self._view_cache: dict[str, tuple[ThoughtDbView, tuple[tuple[int, int], tuple[int, int], tuple[int, int]]]] = {}

    @property
    def home_dir(self) -> Path:
        return self._home_dir

    def _scope_metas(self, scope: str) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
        def meta(p: Path) -> tuple[int, int]:
            try:
                st = p.stat()
            except FileNotFoundError:
                return 0, 0
            except Exception:
                return 0, 0
            return int(getattr(st, "st_size", 0) or 0), int(getattr(st, "st_mtime_ns", 0) or 0)

        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"
        return meta(self._claims_path(sc)), meta(self._edges_path(sc)), meta(self._nodes_path(sc))

    def _claims_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_claims_path
        return self._project_paths.thoughtdb_claims_path

    def _edges_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_edges_path
        return self._project_paths.thoughtdb_edges_path

    def _nodes_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_nodes_path
        return self._project_paths.thoughtdb_nodes_path

    def _project_id_for_scope(self, scope: str) -> str:
        return "" if scope == "global" else self._project_paths.project_id

    def _ensure_scope_dirs(self, scope: str) -> None:
        claims = self._claims_path(scope)
        edges = self._edges_path(scope)
        nodes = self._nodes_path(scope)
        ensure_dir(claims.parent)
        ensure_dir(edges.parent)
        ensure_dir(nodes.parent)

    def _view_snapshot_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_dir / "view.snapshot.json"
        return self._project_paths.thoughtdb_dir / "view.snapshot.json"

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
        if str(obj.get("kind") or "").strip() != _VIEW_SNAPSHOT_KIND:
            return None
        if str(obj.get("version") or "").strip() != _VIEW_SNAPSHOT_VERSION:
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
            "kind": _VIEW_SNAPSHOT_KIND,
            "version": _VIEW_SNAPSHOT_VERSION,
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

        claims_path = self._claims_path(sc)
        edges_path = self._edges_path(sc)
        nodes_path = self._nodes_path(sc)

        claims_by_id: dict[str, dict[str, Any]] = {}
        claims_by_tag: dict[str, set[str]] = {}
        retracted: set[str] = set()

        for obj in iter_jsonl(claims_path):
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
        for obj in iter_jsonl(nodes_path):
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
        for obj in iter_jsonl(edges_path):
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
            out.add(_edge_key(edge_type=et, from_id=frm, to_id=to))
        return out

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
        append_jsonl(self._claims_path(sc), obj)
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
        append_jsonl(
            self._claims_path(sc),
            {
                "kind": "claim_retract",
                "version": THOUGHTDB_VERSION,
                "ts": now_rfc3339(),
                "claim_id": cid,
                "rationale": (rationale or "").strip(),
                "source_refs": refs,
            },
        )

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
        append_jsonl(self._nodes_path(sc), obj)
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
        append_jsonl(
            self._nodes_path(sc),
            {
                "kind": "node_retract",
                "version": THOUGHTDB_VERSION,
                "ts": now_rfc3339(),
                "node_id": nid,
                "rationale": (rationale or "").strip(),
                "source_refs": refs,
            },
        )

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
        append_jsonl(
            self._edges_path(sc),
            {
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
            },
        )
        return eid

    def apply_mined_claims(
        self,
        *,
        mined_claims: list[dict[str, Any]],
        allowed_event_ids: set[str],
        min_confidence: float,
        max_claims: int,
    ) -> dict[str, Any]:
        """Back-compat wrapper: apply mined claims only (ignore edges).

        Prefer using `apply_mined_output(...)` when you have the full mine_claims output.
        """

        res = self.apply_mined_output(
            output={"claims": mined_claims or [], "edges": [], "notes": ""},
            allowed_event_ids=allowed_event_ids,
            min_confidence=min_confidence,
            max_claims=max_claims,
        )
        # Keep the older return surface.
        return {
            "written": res.get("written", []) if isinstance(res, dict) else [],
            "skipped": res.get("skipped", []) if isinstance(res, dict) else [],
        }

    def apply_mined_output(
        self,
        *,
        output: dict[str, Any],
        allowed_event_ids: set[str],
        min_confidence: float,
        max_claims: int,
    ) -> dict[str, Any]:
        """Validate+append mined claims + edges (high-threshold active claims; best-effort).

        Returns:
        {
          "written": [{"local_id": "...", "claim_id": "...", "scope": "..."}],
          "linked_existing": [{"local_id": "...", "claim_id": "...", "scope": "..."}],
          "written_edges": [{"edge_id": "...", "scope": "...", "edge_type": "...", "from_id": "...", "to_id": "..."}],
          "skipped": [{"kind":"claim|edge", "reason":"...", "detail":"..."}]
        }
        """

        try:
            min_conf = float(min_confidence)
        except Exception:
            min_conf = 0.9
        min_conf = max(0.0, min(1.0, min_conf))

        try:
            max_n = int(max_claims)
        except Exception:
            max_n = 6
        max_n = max(0, min(20, max_n))
        if max_n == 0:
            return {"written": [], "linked_existing": [], "written_edges": [], "skipped": []}

        mined_claims = output.get("claims") if isinstance(output, dict) else None
        mined_edges = output.get("edges") if isinstance(output, dict) else None
        claims_in = mined_claims if isinstance(mined_claims, list) else []
        edges_in = mined_edges if isinstance(mined_edges, list) else []

        # Dedup obvious identical claims per-scope; also allow linking to an existing canonical claim id.
        existing_sig_to_id = {
            "project": self.existing_signature_map(scope="project"),
            "global": self.existing_signature_map(scope="global"),
        }
        existing_sig = {
            "project": set(existing_sig_to_id["project"].keys()),
            "global": set(existing_sig_to_id["global"].keys()),
        }

        # Filter and sort claims by confidence descending.
        sugs: list[dict[str, Any]] = []
        for idx, raw in enumerate(claims_in or []):
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            try:
                conf = float(raw.get("confidence") or 0.0)
            except Exception:
                conf = 0.0
            if conf < min_conf:
                continue
            # Back-compat: allow missing local_id (synthetic).
            if not str(raw.get("local_id") or "").strip():
                raw = dict(raw)
                raw["local_id"] = f"c{idx+1}"
            sugs.append(raw)

        sugs2 = sorted(
            sugs,
            key=lambda x: float(x.get("confidence") or 0.0) if isinstance(x, dict) else 0.0,
            reverse=True,
        )[:max_n]

        skipped: list[dict[str, str]] = []
        written: list[dict[str, str]] = []
        linked_existing: list[dict[str, str]] = []

        local_to_claim: dict[str, str] = {}
        local_meta: dict[str, dict[str, str]] = {}

        for raw in sugs2:
            local_id = str(raw.get("local_id") or "").strip()
            if not local_id:
                continue
            if local_id in local_to_claim:
                skipped.append({"kind": "claim", "reason": "duplicate_local_id", "detail": local_id})
                continue

            ct = str(raw.get("claim_type") or "").strip()
            text = str(raw.get("text") or "").strip()
            scope = str(raw.get("scope") or "project").strip()
            if scope not in ("project", "global"):
                scope = "project"
            vis = str(raw.get("visibility") or ("global" if scope == "global" else "project")).strip()
            if vis not in ("private", "project", "global"):
                vis = "project"

            # Only allow EvidenceLog event_id citations.
            raw_ev = raw.get("source_event_ids") if isinstance(raw.get("source_event_ids"), list) else []
            ev_ids = [str(x).strip() for x in raw_ev if str(x).strip()]
            ev_ids2 = [x for x in ev_ids if x in allowed_event_ids]
            if not ev_ids2:
                skipped.append({"kind": "claim", "reason": "no_valid_source_event_ids", "detail": text[:200]})
                continue

            sig = claim_signature(claim_type=ct, scope=scope, project_id=self._project_id_for_scope(scope), text=text)
            if sig in existing_sig.get(scope, set()):
                existing_id = existing_sig_to_id.get(scope, {}).get(sig, "")
                if existing_id:
                    local_to_claim[local_id] = existing_id
                    local_meta[local_id] = {"scope": scope, "visibility": vis}
                    linked_existing.append({"local_id": local_id, "claim_id": existing_id, "scope": scope})
                    continue
                skipped.append({"kind": "claim", "reason": "duplicate_signature", "detail": text[:200]})
                continue

            vf = raw.get("valid_from")
            vt = raw.get("valid_to")
            valid_from = vf if isinstance(vf, str) and vf.strip() else None
            valid_to = vt if isinstance(vt, str) and vt.strip() else None
            tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
            tags2 = [str(x).strip() for x in tags if str(x).strip()]
            notes = str(raw.get("notes") or "").strip()
            try:
                conf = float(raw.get("confidence") or 0.0)
            except Exception:
                conf = 0.0

            try:
                cid = self.append_claim_create(
                    claim_type=ct,
                    text=text,
                    scope=scope,
                    visibility=vis,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    tags=tags2,
                    source_event_ids=ev_ids2,
                    confidence=conf,
                    notes=notes,
                )
            except Exception as e:
                skipped.append({"kind": "claim", "reason": f"write_error:{type(e).__name__}", "detail": text[:200]})
                continue

            existing_sig.setdefault(scope, set()).add(sig)
            existing_sig_to_id.setdefault(scope, {})[sig] = cid
            local_to_claim[local_id] = cid
            local_meta[local_id] = {"scope": scope, "visibility": vis}
            written.append({"local_id": local_id, "claim_id": cid, "scope": scope})

        # Apply edges (optional, best-effort). Edge refs can be local_id or existing claim_id.
        written_edges: list[dict[str, str]] = []
        edge_keys_by_scope = {
            "project": self.existing_edge_keys(scope="project"),
            "global": self.existing_edge_keys(scope="global"),
        }
        view_project = self.load_view(scope="project")
        view_global = self.load_view(scope="global")

        def resolve_ref(ref: str) -> tuple[str, str, str]:
            """Return (scope, claim_id, visibility) or ("","","") if unresolved."""
            r = (ref or "").strip()
            if not r:
                return "", "", ""
            if r in local_to_claim:
                meta = local_meta.get(r, {})
                return str(meta.get("scope") or ""), local_to_claim[r], str(meta.get("visibility") or "")
            # Existing claim id (project/global).
            if r in view_project.claims_by_id:
                vis2 = str(view_project.claims_by_id.get(r, {}).get("visibility") or "")
                return "project", r, vis2
            if r in view_global.claims_by_id:
                vis2 = str(view_global.claims_by_id.get(r, {}).get("visibility") or "")
                return "global", r, vis2
            return "", "", ""

        # Cap edge count to avoid noisy graphs.
        max_edges = max(0, min(40, max_n * 6))
        for raw in edges_in[:max_edges]:
            if not isinstance(raw, dict):
                continue
            et = str(raw.get("edge_type") or "").strip()
            frm_ref = str(raw.get("from_claim_id") or "").strip()
            to_ref = str(raw.get("to_claim_id") or "").strip()
            if not et or not frm_ref or not to_ref:
                skipped.append({"kind": "edge", "reason": "missing_fields", "detail": f"{et}:{frm_ref}->{to_ref}"})
                continue
            try:
                conf = float(raw.get("confidence") or 0.0)
            except Exception:
                conf = 0.0
            if conf < min_conf:
                skipped.append({"kind": "edge", "reason": "below_confidence", "detail": f"{et}:{frm_ref}->{to_ref}"})
                continue

            sc1, frm_id, vis1 = resolve_ref(frm_ref)
            sc2, to_id, vis2 = resolve_ref(to_ref)
            if not frm_id or not to_id:
                skipped.append({"kind": "edge", "reason": "unresolved_ref", "detail": f"{et}:{frm_ref}->{to_ref}"})
                continue
            if sc1 != sc2:
                skipped.append({"kind": "edge", "reason": "cross_scope", "detail": f"{et}:{frm_id}({sc1})->{to_id}({sc2})"})
                continue
            sc = sc1
            if sc not in ("project", "global"):
                skipped.append({"kind": "edge", "reason": "invalid_scope", "detail": sc})
                continue

            # Only allow EvidenceLog event_id citations.
            raw_ev = raw.get("source_event_ids") if isinstance(raw.get("source_event_ids"), list) else []
            ev_ids = [str(x).strip() for x in raw_ev if str(x).strip()]
            ev_ids2 = [x for x in ev_ids if x in allowed_event_ids]
            if not ev_ids2:
                skipped.append({"kind": "edge", "reason": "no_valid_source_event_ids", "detail": f"{et}:{frm_id}->{to_id}"})
                continue

            ek = _edge_key(edge_type=et, from_id=frm_id, to_id=to_id)
            if ek in edge_keys_by_scope.get(sc, set()):
                skipped.append({"kind": "edge", "reason": "duplicate_edge", "detail": ek})
                continue

            vis = _min_visibility(vis1, vis2)
            notes = str(raw.get("notes") or "").strip()
            try:
                eid = self.append_edge(
                    edge_type=et,
                    from_id=frm_id,
                    to_id=to_id,
                    scope=sc,
                    visibility=vis,
                    source_event_ids=ev_ids2,
                    notes=notes,
                )
            except Exception as e:
                skipped.append({"kind": "edge", "reason": f"write_error:{type(e).__name__}", "detail": ek})
                continue

            edge_keys_by_scope.setdefault(sc, set()).add(ek)
            written_edges.append({"edge_id": eid, "scope": sc, "edge_type": et, "from_id": frm_id, "to_id": to_id})

        return {
            "written": written,
            "linked_existing": linked_existing,
            "written_edges": written_edges,
            "skipped": skipped,
        }
