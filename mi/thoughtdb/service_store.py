from __future__ import annotations

from typing import Any, Callable

from .model import claim_signature, edge_key, min_visibility
from .append_store import ThoughtAppendStore
from .view_store import ThoughtViewStore


class ThoughtServiceStore:
    """Business-level Thought DB operations composed over append + view stores."""

    def __init__(
        self,
        *,
        append_store: ThoughtAppendStore,
        view_store: ThoughtViewStore,
        project_id_for_scope: Callable[[str], str],
    ) -> None:
        self._append = append_store
        self._view = view_store
        self._project_id_for_scope = project_id_for_scope

    def apply_mined_output(
        self,
        *,
        output: dict[str, Any],
        allowed_event_ids: set[str],
        min_confidence: float,
        max_claims: int,
    ) -> dict[str, Any]:
        """Validate+append mined claims + edges (high-threshold active claims; best-effort)."""

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
            "project": self._view.existing_signature_map(scope="project"),
            "global": self._view.existing_signature_map(scope="global"),
        }
        existing_sig = {
            "project": set(existing_sig_to_id["project"].keys()),
            "global": set(existing_sig_to_id["global"].keys()),
        }

        # Filter and sort claims by confidence descending.
        sugs: list[dict[str, Any]] = []
        for raw in claims_in or []:
            if not isinstance(raw, dict):
                continue
            local_id = str(raw.get("local_id") or "").strip()
            if not local_id:
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
                cid = self._append.append_claim_create(
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
            "project": self._view.existing_edge_keys(scope="project"),
            "global": self._view.existing_edge_keys(scope="global"),
        }
        view_project = self._view.load_view(scope="project")
        view_global = self._view.load_view(scope="global")

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

            ek = edge_key(edge_type=et, from_id=frm_id, to_id=to_id)
            if ek in edge_keys_by_scope.get(sc, set()):
                skipped.append({"kind": "edge", "reason": "duplicate_edge", "detail": ek})
                continue

            vis = min_visibility(vis1, vis2)
            notes = str(raw.get("notes") or "").strip()
            try:
                eid = self._append.append_edge(
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
