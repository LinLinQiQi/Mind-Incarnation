from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..memory.service import MemoryService
from ..core.paths import ProjectPaths
from ..runtime.prompts import why_trace_prompt
from ..core.storage import iter_jsonl, now_rfc3339
from .retrieval import expand_one_hop
from .store import ThoughtDbStore


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def find_evidence_event(*, evidence_log_path: Path, event_id: str) -> dict[str, Any] | None:
    eid = (event_id or "").strip()
    if not eid:
        return None
    for obj in iter_jsonl(evidence_log_path):
        if isinstance(obj, dict) and str(obj.get("event_id") or "").strip() == eid:
            return obj
    return None


def query_from_evidence_event(obj: dict[str, Any]) -> str:
    kind = str(obj.get("kind") or "").strip()
    # EvidenceItem records in V1 may omit `kind`. Treat the presence of the standard
    # evidence fields as an evidence-like record for query extraction.
    if kind == "evidence" or (not kind and ("facts" in obj or "results" in obj or "unknowns" in obj)):
        facts = obj.get("facts") if isinstance(obj.get("facts"), list) else []
        results = obj.get("results") if isinstance(obj.get("results"), list) else []
        unknowns = obj.get("unknowns") if isinstance(obj.get("unknowns"), list) else []
        parts = [str(x).strip() for x in [*facts[:6], *results[:6], *unknowns[:4]] if str(x).strip()]
        return " ".join(parts).strip()
    if kind == "decide_next":
        parts = [
            str(obj.get("status") or "").strip(),
            str(obj.get("next_action") or "").strip(),
            str(obj.get("notes") or "").strip(),
            str(obj.get("next_hands_input") or "").strip(),
        ]
        return " ".join([x for x in parts if x]).strip()
    if kind == "hands_input":
        return str(obj.get("input") or "").strip()
    if kind == "workflow_trigger":
        return " ".join([str(obj.get("workflow_name") or ""), str(obj.get("trigger_pattern") or "")]).strip()
    return _truncate(json.dumps(obj, sort_keys=True), 1400)


def _compact_claim(c: dict[str, Any], *, status: str, canonical_id: str) -> dict[str, Any]:
    refs = c.get("source_refs") if isinstance(c.get("source_refs"), list) else []
    ev_ids: list[str] = []
    for r in refs:
        if isinstance(r, dict) and r.get("event_id"):
            ev_ids.append(str(r.get("event_id")))
    ev_ids = [x for x in ev_ids if x.strip()][:6]
    return {
        "claim_id": str(c.get("claim_id") or "").strip(),
        "canonical_id": canonical_id,
        "status": status,
        "claim_type": str(c.get("claim_type") or "").strip(),
        "scope": str(c.get("scope") or "").strip(),
        "visibility": str(c.get("visibility") or "").strip(),
        "asserted_ts": str(c.get("asserted_ts") or "").strip(),
        "valid_from": c.get("valid_from"),
        "valid_to": c.get("valid_to"),
        "text": _truncate(str(c.get("text") or "").strip(), 480),
        "source_event_ids": ev_ids,
        "tags": [str(x) for x in (c.get("tags") or []) if str(x).strip()][:12] if isinstance(c.get("tags"), list) else [],
    }


def collect_candidate_claims(
    *,
    tdb: ThoughtDbStore,
    mem: MemoryService,
    project_paths: ProjectPaths,
    query: str,
    top_k: int,
    target_event_id: str = "",
) -> list[dict[str, Any]]:
    """Collect a bounded candidate claim list (project + global) for WhyTrace."""

    try:
        k = int(top_k)
    except Exception:
        k = 12
    k = max(1, min(40, k))

    q = str(query or "").strip()
    if not q:
        q = str(target_event_id or "").strip()
    if not q:
        return []

    mem.ingest_structured()
    hits = mem.search(query=q, top_k=min(80, k * 5), kinds={"claim"}, include_global=True, exclude_project_id="")

    # Load views once.
    v_proj = tdb.load_view(scope="project")
    v_glob = tdb.load_view(scope="global")

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Also include claims that directly cite the target event id (when provided).
    if target_event_id.strip():
        te = target_event_id.strip()
        for v in (v_proj, v_glob):
            for c in v.claims_by_id.values():
                if not isinstance(c, dict):
                    continue
                refs = c.get("source_refs") if isinstance(c.get("source_refs"), list) else []
                if any(isinstance(r, dict) and str(r.get("event_id") or "").strip() == te for r in refs):
                    cid = str(c.get("claim_id") or "").strip()
                    if cid and cid not in seen:
                        seen.add(cid)
                        out.append(_compact_claim(c, status=v.claim_status(cid), canonical_id=v.resolve_id(cid)))
                        if len(out) >= k:
                            return out

    for it in hits:
        if it.kind != "claim":
            continue
        # Filter: only current project + global (avoid other projects for WhyTrace by default).
        if it.scope == "project" and str(it.project_id or "").strip() != project_paths.project_id:
            continue

        # item_id format: claim:<scope>:<project_id|global>:<claim_id>
        parts = str(it.item_id or "").split(":")
        if len(parts) < 4:
            continue
        scope = parts[1].strip()
        cid = parts[-1].strip()
        if not cid or cid in seen:
            continue

        if scope == "global":
            c = v_glob.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            seen.add(cid)
            out.append(_compact_claim(c, status=v_glob.claim_status(cid), canonical_id=v_glob.resolve_id(cid)))
        else:
            c = v_proj.claims_by_id.get(cid)
            if not isinstance(c, dict):
                continue
            seen.add(cid)
            out.append(_compact_claim(c, status=v_proj.claim_status(cid), canonical_id=v_proj.resolve_id(cid)))

        if len(out) >= k:
            break

    return out


def _extract_thought_db_hints(obj: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Extract deterministic hint ids recorded in EvidenceLog by MI (best-effort).

    This is designed to work with `kind=decide_next` records that contain a compact
    `thought_db` summary (claim/node ids) so WhyTrace can be less dependent on FTS recall.
    """

    if not isinstance(obj, dict):
        return [], []

    tdb = obj.get("thought_db") if isinstance(obj.get("thought_db"), dict) else {}
    if not isinstance(tdb, dict):
        return [], []

    claim_ids: list[str] = []
    node_ids: list[str] = []

    def _add_many(dst: list[str], items: object) -> None:
        if not isinstance(items, list):
            return
        for x in items:
            if not isinstance(x, str):
                continue
            xs = x.strip()
            if xs:
                dst.append(xs)

    # Keep this order stable: values/defaults -> preferences/goals -> query-seeded.
    _add_many(claim_ids, tdb.get("values_claim_ids"))
    _add_many(claim_ids, tdb.get("pref_goal_claim_ids"))
    _add_many(claim_ids, tdb.get("query_claim_ids"))
    _add_many(node_ids, tdb.get("node_ids"))

    # Dedupe while preserving order.
    out_claims: list[str] = []
    seen_c: set[str] = set()
    for cid in claim_ids:
        if cid in seen_c:
            continue
        seen_c.add(cid)
        out_claims.append(cid)

    out_nodes: list[str] = []
    seen_n: set[str] = set()
    for nid in node_ids:
        if nid in seen_n:
            continue
        seen_n.add(nid)
        out_nodes.append(nid)

    return out_claims, out_nodes


def collect_candidate_claims_for_target(
    *,
    tdb: ThoughtDbStore,
    mem: MemoryService,
    project_paths: ProjectPaths,
    target_obj: dict[str, Any],
    query: str,
    top_k: int,
    as_of_ts: str,
    target_event_id: str = "",
) -> list[dict[str, Any]]:
    """Collect candidate claims for WhyTrace, preferring deterministic EvidenceLog hints when present."""

    hint_claim_ids, hint_node_ids = _extract_thought_db_hints(target_obj if isinstance(target_obj, dict) else {})
    if not hint_claim_ids and not hint_node_ids:
        # When no deterministic hints exist, fall back to the standard candidate collector.
        return collect_candidate_claims(
            tdb=tdb,
            mem=mem,
            project_paths=project_paths,
            query=query,
            top_k=top_k,
            target_event_id=target_event_id,
        )

    try:
        k = int(top_k)
    except Exception:
        k = 12
    k = max(1, min(40, k))

    t = str(as_of_ts or "").strip()
    ev_id = str(target_event_id or "").strip()

    # Load views once.
    v_proj = tdb.load_view(scope="project")
    v_glob = tdb.load_view(scope="global")

    def _claim_active_and_valid(view: Any, cid: str) -> bool:
        ccid = str(cid or "").strip()
        if not ccid:
            return False
        if view.claim_status(ccid) != "active":
            return False
        c = view.claims_by_id.get(ccid)
        if not isinstance(c, dict):
            return False
        if t:
            vf = c.get("valid_from")
            vt = c.get("valid_to")
            if isinstance(vf, str) and vf.strip() and vf.strip() > t:
                return False
            if isinstance(vt, str) and vt.strip() and t >= vt.strip():
                return False
        return True

    def _load_claim_by_id(cid: str) -> tuple[dict[str, Any] | None, Any | None, str]:
        """Resolve to a canonical claim record + view."""

        raw = str(cid or "").strip()
        if not raw:
            return None, None, ""

        if raw in v_proj.claims_by_id:
            canon = v_proj.resolve_id(raw)
            return v_proj.claims_by_id.get(canon), v_proj, canon
        if raw in v_glob.claims_by_id:
            canon = v_glob.resolve_id(raw)
            return v_glob.claims_by_id.get(canon), v_glob, canon

        canon_p = v_proj.resolve_id(raw)
        if canon_p and canon_p in v_proj.claims_by_id:
            return v_proj.claims_by_id.get(canon_p), v_proj, canon_p
        canon_g = v_glob.resolve_id(raw)
        if canon_g and canon_g in v_glob.claims_by_id:
            return v_glob.claims_by_id.get(canon_g), v_glob, canon_g

        return None, None, ""

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_claim_id(cid: str) -> None:
        nonlocal out
        if len(out) >= k:
            return
        c, view, canon = _load_claim_by_id(cid)
        if not isinstance(c, dict) or view is None or not canon:
            return
        if canon in seen:
            return
        if not _claim_active_and_valid(view, canon):
            return
        seen.add(canon)
        out.append(_compact_claim(c, status=view.claim_status(canon), canonical_id=view.resolve_id(canon)))

    # 1) Prefer deterministic hints from the decide_next record.
    for cid in hint_claim_ids:
        _add_claim_id(cid)
        if len(out) >= k:
            return out

    # 2) Include any claims that directly cite the target event id (best-effort).
    if ev_id:
        te = ev_id
        for view in (v_proj, v_glob):
            for c in view.claims_by_id.values():
                if len(out) >= k:
                    return out
                if not isinstance(c, dict):
                    continue
                refs = c.get("source_refs") if isinstance(c.get("source_refs"), list) else []
                if not any(isinstance(r, dict) and str(r.get("event_id") or "").strip() == te for r in refs):
                    continue
                cid = str(c.get("claim_id") or "").strip()
                if not cid:
                    continue
                canon = view.resolve_id(cid)
                if canon in seen:
                    continue
                if not _claim_active_and_valid(view, canon):
                    continue
                seen.add(canon)
                cc = view.claims_by_id.get(canon)
                if not isinstance(cc, dict):
                    continue
                out.append(_compact_claim(cc, status=view.claim_status(canon), canonical_id=view.resolve_id(canon)))

    # 3) One-hop expansion from (hint claims + hint nodes + target event id).
    rem = max(0, k - len(out))
    if rem > 0:
        seed_ids: set[str] = set(hint_claim_ids) | set(hint_node_ids)
        if ev_id:
            seed_ids.add(ev_id)
        exp = expand_one_hop(
            v_proj=v_proj,
            v_glob=v_glob,
            seed_ids=seed_ids,
            as_of_ts=t,
            max_new_claims=rem,
            max_new_nodes=0,
            edge_types={"depends_on", "supports", "contradicts", "derived_from", "mentions", "supersedes", "same_as"},
        )
        for cid in exp.claim_ids:
            _add_claim_id(cid)
            if len(out) >= k:
                return out

    # 4) Backfill from memory search (FTS) if needed.
    q = str(query or "").strip() or ev_id
    if len(out) < k and q:
        mem.ingest_structured()
        hits = mem.search(query=q, top_k=min(80, k * 5), kinds={"claim"}, include_global=True, exclude_project_id="")
        for it in hits:
            if len(out) >= k:
                break
            if it.kind != "claim":
                continue
            if it.scope == "project" and str(it.project_id or "").strip() != project_paths.project_id:
                continue
            parts = str(it.item_id or "").split(":")
            if len(parts) < 4:
                continue
            cid = parts[-1].strip()
            if not cid:
                continue
            _add_claim_id(cid)

    return out


@dataclass(frozen=True)
class WhyTraceOutcome:
    obj: dict[str, Any]
    mind_transcript_ref: str
    written_edge_ids: list[str]


def run_why_trace(
    *,
    mind: Any,
    tdb: ThoughtDbStore,
    mem: MemoryService,
    project_paths: ProjectPaths,
    target: dict[str, Any],
    candidate_claims: list[dict[str, Any]],
    as_of_ts: str,
    write_edges_from_event_id: str,
    min_write_confidence: float = 0.7,
) -> WhyTraceOutcome:
    """Run the WhyTrace mind call and optionally materialize depends_on edges from an event_id to chosen claims."""

    prompt = why_trace_prompt(
        target=target,
        as_of_ts=as_of_ts,
        candidate_claims=candidate_claims,
        notes="why_trace",
    )
    res = mind.call(schema_filename="why_trace.json", prompt=prompt, tag="why_trace")

    out = res.obj if hasattr(res, "obj") else {}
    if not isinstance(out, dict):
        out = {"status": "insufficient", "confidence": 0.0, "chosen_claim_ids": [], "explanation": "", "notes": "invalid output"}

    cand_ids = {str(c.get("claim_id") or "").strip() for c in candidate_claims if isinstance(c, dict) and str(c.get("claim_id") or "").strip()}
    raw_chosen = out.get("chosen_claim_ids") if isinstance(out.get("chosen_claim_ids"), list) else []
    chosen = [str(x).strip() for x in raw_chosen if isinstance(x, str) and str(x).strip()]
    # Enforce: only choose from candidates.
    chosen2: list[str] = []
    seen: set[str] = set()
    for cid in chosen:
        if cid in seen or cid not in cand_ids:
            continue
        seen.add(cid)
        chosen2.append(cid)
        if len(chosen2) >= 10:
            break
    out["chosen_claim_ids"] = chosen2

    written_edge_ids: list[str] = []
    ev_id = str(write_edges_from_event_id or "").strip()
    if ev_id:
        try:
            conf = float(out.get("confidence") or 0.0)
        except Exception:
            conf = 0.0
        if str(out.get("status") or "").strip() == "ok" and conf >= float(min_write_confidence) and chosen2:
            # Materialize depends_on edges into the project store (event_id -> claim_id).
            vis_by_id = {}
            for c in candidate_claims or []:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("claim_id") or "").strip()
                if cid:
                    vis_by_id[cid] = str(c.get("visibility") or "").strip()
            for cid in chosen2:
                try:
                    vis = "private" if vis_by_id.get(cid) == "private" else "project"
                    eid = tdb.append_edge(
                        edge_type="depends_on",
                        from_id=ev_id,
                        to_id=cid,
                        scope="project",
                        visibility=vis,
                        source_event_ids=[ev_id],
                        notes="why_trace materialized",
                    )
                except Exception:
                    continue
                written_edge_ids.append(eid)

    mind_ref = str(getattr(res, "transcript_path", "") or "").strip()
    return WhyTraceOutcome(obj=out, mind_transcript_ref=mind_ref, written_edge_ids=written_edge_ids)


def default_as_of_ts() -> str:
    return now_rfc3339()
