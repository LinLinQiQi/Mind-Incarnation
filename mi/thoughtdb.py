from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .paths import GlobalPaths, ProjectPaths
from .storage import append_jsonl, ensure_dir, iter_jsonl, now_rfc3339


THOUGHTDB_VERSION = "v1"


def new_claim_id() -> str:
    return f"cl_{time.time_ns()}_{secrets.token_hex(4)}"


def new_edge_id() -> str:
    return f"ed_{time.time_ns()}_{secrets.token_hex(4)}"


def _norm_text(text: str) -> str:
    return " ".join((text or "").strip().split()).lower()


def claim_signature(*, claim_type: str, scope: str, project_id: str, text: str) -> str:
    """Stable signature for deduping obvious identical claims."""
    base = f"{claim_type.strip()}|{scope.strip()}|{project_id.strip()}|{_norm_text(text)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


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
    edges: list[dict[str, Any]]
    redirects_same_as: dict[str, str]
    superseded_ids: set[str]
    retracted_ids: set[str]

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


class ThoughtDbStore:
    """Append-only Thought DB store (Claims + Edges).

    Source of truth for MI runs remains EvidenceLog + raw transcripts. Thought DB
    adds durable, reusable Claim/Edge records that reference EvidenceLog event_id.
    """

    def __init__(self, *, home_dir: Path, project_paths: ProjectPaths) -> None:
        self._home_dir = Path(home_dir).expanduser().resolve()
        self._project_paths = project_paths
        self._gp = GlobalPaths(home_dir=self._home_dir)

    def _claims_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_claims_path
        return self._project_paths.thoughtdb_claims_path

    def _edges_path(self, scope: str) -> Path:
        if scope == "global":
            return self._gp.thoughtdb_global_edges_path
        return self._project_paths.thoughtdb_edges_path

    def _project_id_for_scope(self, scope: str) -> str:
        return "" if scope == "global" else self._project_paths.project_id

    def _ensure_scope_dirs(self, scope: str) -> None:
        claims = self._claims_path(scope)
        edges = self._edges_path(scope)
        ensure_dir(claims.parent)
        ensure_dir(edges.parent)

    def load_view(self, *, scope: str) -> ThoughtDbView:
        sc = (scope or "project").strip()
        if sc not in ("project", "global"):
            sc = "project"

        claims_path = self._claims_path(sc)
        edges_path = self._edges_path(sc)

        claims_by_id: dict[str, dict[str, Any]] = {}
        retracted: set[str] = set()

        for obj in iter_jsonl(claims_path):
            if not isinstance(obj, dict):
                continue
            k = str(obj.get("kind") or "").strip()
            if k == "claim":
                cid = str(obj.get("claim_id") or "").strip()
                if cid:
                    claims_by_id[cid] = obj
            elif k == "claim_retract":
                cid = str(obj.get("claim_id") or "").strip()
                if cid:
                    retracted.add(cid)

        edges: list[dict[str, Any]] = []
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
            if et == "same_as" and frm and to:
                redirects[frm] = to
            if et == "supersedes" and frm and to:
                superseded.add(frm)

        pid = self._project_id_for_scope(sc)
        return ThoughtDbView(
            scope=sc,
            project_id=pid,
            claims_by_id=claims_by_id,
            edges=edges,
            redirects_same_as=redirects,
            superseded_ids=superseded,
            retracted_ids=retracted,
        )

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
        """Validate+append mined claims (high-threshold active claims).

        Returns:
        {
          "written": [{"claim_id": "...", "scope": "..."}],
          "skipped": [{"reason": "...", "text": "..."}]
        }
        """

        try:
            min_conf = float(min_confidence)
        except Exception:
            min_conf = 0.9
        if min_conf < 0:
            min_conf = 0.0
        if min_conf > 1:
            min_conf = 1.0

        try:
            max_n = int(max_claims)
        except Exception:
            max_n = 6
        if max_n < 0:
            max_n = 0
        if max_n > 20:
            max_n = 20
        if max_n == 0:
            return {"written": [], "skipped": []}

        sugs: list[dict[str, Any]] = []
        for raw in mined_claims or []:
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
            sugs.append(raw)

        # Sort by confidence descending and cap.
        sugs2 = sorted(
            sugs,
            key=lambda x: float(x.get("confidence") or 0.0) if isinstance(x, dict) else 0.0,
            reverse=True,
        )[:max_n]

        skipped: list[dict[str, str]] = []
        written: list[dict[str, str]] = []

        # Dedup obvious identical claims per-scope.
        existing_by_scope = {
            "project": self.existing_signatures(scope="project"),
            "global": self.existing_signatures(scope="global"),
        }

        for raw in sugs2:
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
                skipped.append({"reason": "no_valid_source_event_ids", "text": text[:200]})
                continue

            sig = claim_signature(claim_type=ct, scope=scope, project_id=self._project_id_for_scope(scope), text=text)
            if sig in existing_by_scope.get(scope, set()):
                skipped.append({"reason": "duplicate_signature", "text": text[:200]})
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
                skipped.append({"reason": f"write_error:{type(e).__name__}", "text": text[:200]})
                continue

            existing_by_scope.setdefault(scope, set()).add(sig)
            written.append({"claim_id": cid, "scope": scope})

        return {"written": written, "skipped": skipped}

