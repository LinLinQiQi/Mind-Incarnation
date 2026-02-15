from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory_service import MemoryService
from .paths import ProjectPaths
from .prompts import why_trace_prompt
from .storage import iter_jsonl, now_rfc3339
from .thoughtdb import ThoughtDbStore


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
    if kind == "evidence":
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
            str(obj.get("next_codex_input") or "").strip(),
        ]
        return " ".join([x for x in parts if x]).strip()
    if kind in ("hands_input", "codex_input"):
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
