from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .memory_text import tokenize_query, truncate
from .thoughtdb import ThoughtDbStore, ThoughtDbView
from .values import VALUES_BASE_TAG
from .pins import PINNED_PREF_GOAL_TAGS


def _safe_list_str(items: Any, *, limit: int) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for x in items:
        if len(out) >= limit:
            break
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def _collect_query_text(*, task: str, hands_last_message: str, recent_evidence: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if str(task or "").strip():
        parts.append(str(task).strip())
    if str(hands_last_message or "").strip():
        parts.append(str(hands_last_message).strip())

    # Pull a small amount of signal from evidence (unknowns/risk/facts/results).
    for rec in recent_evidence[-6:]:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("kind") or "").strip() != "evidence":
            continue
        parts.extend(_safe_list_str(rec.get("unknowns"), limit=6))
        parts.extend(_safe_list_str(rec.get("risk_signals"), limit=6))
        parts.extend(_safe_list_str(rec.get("facts"), limit=6))
        parts.extend(_safe_list_str(rec.get("results"), limit=4))

    return "\n".join([p for p in parts if p]).strip()


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _score_tokens(tokens: list[str], *, text: str) -> int:
    if not tokens:
        return 0
    t = _norm(text)
    score = 0
    for tok in tokens:
        if tok and tok in t:
            score += 1
    return score


def _compact_claim(c: dict[str, Any], *, view: ThoughtDbView) -> dict[str, Any]:
    cid = str(c.get("claim_id") or "").strip()
    refs = c.get("source_refs") if isinstance(c.get("source_refs"), list) else []
    ev_ids: list[str] = []
    for r in refs:
        if isinstance(r, dict) and r.get("event_id"):
            ev_ids.append(str(r.get("event_id")))
    ev_ids = [x for x in ev_ids if x.strip()][:6]
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
        "source_event_ids": ev_ids,
    }


def _compact_edge(e: dict[str, Any], *, scope: str) -> dict[str, Any]:
    return {
        "edge_type": str(e.get("edge_type") or "").strip(),
        "from_id": str(e.get("from_id") or "").strip(),
        "to_id": str(e.get("to_id") or "").strip(),
        "scope": scope,
        "notes": truncate(str(e.get("notes") or "").strip(), 160),
    }


@dataclass(frozen=True)
class ThoughtDbContext:
    """Compact Thought DB subgraph context for Mind prompts (deterministic retrieval)."""

    as_of_ts: str
    query: str
    values_claims: list[dict[str, Any]]
    pref_goal_claims: list[dict[str, Any]]
    query_claims: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    notes: str

    def to_prompt_obj(self) -> dict[str, Any]:
        return {
            "as_of_ts": self.as_of_ts,
            "query": truncate(self.query, 1200),
            "values_claims": self.values_claims,
            "pref_goal_claims": self.pref_goal_claims,
            "query_claims": self.query_claims,
            "edges": self.edges,
            "notes": self.notes,
        }


def build_decide_next_thoughtdb_context(
    *,
    tdb: ThoughtDbStore,
    as_of_ts: str,
    task: str,
    hands_last_message: str,
    recent_evidence: list[dict[str, Any]],
    max_values_claims: int = 8,
    max_pref_goal_claims: int = 8,
    max_query_claims: int = 10,
    max_edges: int = 20,
) -> ThoughtDbContext:
    """Build a compact Thought DB context for decide_next (always-on, small budget)."""

    q = _collect_query_text(task=task, hands_last_message=hands_last_message, recent_evidence=recent_evidence)
    tokens = tokenize_query(q, max_tokens=18)

    v_proj = tdb.load_view(scope="project")
    v_glob = tdb.load_view(scope="global")

    # Values claims: always include a small set of active global preference/goal claims tagged as values:base.
    values: list[dict[str, Any]] = []
    values_raw: list[dict[str, Any]] = []
    for c in v_glob.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=as_of_ts):
        if not isinstance(c, dict):
            continue
        tags = c.get("tags") if isinstance(c.get("tags"), list) else []
        tagset = {str(x).strip() for x in tags if str(x).strip()}
        if VALUES_BASE_TAG not in tagset:
            continue
        ct = str(c.get("claim_type") or "").strip()
        if ct not in ("preference", "goal"):
            continue
        values_raw.append(c)

    # Sort by asserted_ts descending (RFC3339 string compare is ok for Zulu timestamps).
    values_raw.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)
    for c in values_raw[: max(0, int(max_values_claims))]:
        values.append(_compact_claim(c, view=v_glob))

    values_ids = {str(c.get("claim_id") or "").strip() for c in values if isinstance(c, dict)}

    # Canonical preference/goal claims beyond values:base: always include a small set so decisions
    # do not depend on free-form learned text or query token luck.
    pinned_raw: list[tuple[int, str, dict[str, Any], ThoughtDbView]] = []
    pinned_ids: set[str] = set()
    if PINNED_PREF_GOAL_TAGS:
        for view, scope_rank in ((v_proj, 0), (v_glob, 1)):
            for c in view.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=as_of_ts):
                if not isinstance(c, dict):
                    continue
                ct = str(c.get("claim_type") or "").strip()
                if ct not in ("preference", "goal"):
                    continue
                tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                tagset = {str(x).strip() for x in tags if str(x).strip()}
                if not (tagset & PINNED_PREF_GOAL_TAGS):
                    continue
                cid = str(c.get("claim_id") or "").strip()
                if not cid or cid in values_ids or cid in pinned_ids:
                    continue
                pinned_ids.add(cid)
                pinned_raw.append((scope_rank, str(c.get("asserted_ts") or ""), c, view))

    # Prefer project scope over global; newest-first within scope.
    pinned_raw.sort(key=lambda x: str(x[1] or ""), reverse=True)
    pinned_raw.sort(key=lambda x: int(x[0]), reverse=False)

    pref_goal_raw: list[tuple[int, str, dict[str, Any], ThoughtDbView]] = []
    for view, scope_rank in ((v_proj, 0), (v_glob, 1)):
        for c in view.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=as_of_ts):
            if not isinstance(c, dict):
                continue
            ct = str(c.get("claim_type") or "").strip()
            if ct not in ("preference", "goal"):
                continue
            cid = str(c.get("claim_id") or "").strip()
            if not cid or cid in values_ids or cid in pinned_ids:
                continue
            tags = c.get("tags") if isinstance(c.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            if VALUES_BASE_TAG in tagset:
                continue
            pref_goal_raw.append((scope_rank, str(c.get("asserted_ts") or ""), c, view))

    # Sort newest-first within scope; prefer project scope over global.
    pref_goal_raw.sort(key=lambda x: str(x[1] or ""), reverse=True)
    pref_goal_raw.sort(key=lambda x: int(x[0]), reverse=False)
    pref_goal_claims: list[dict[str, Any]] = []
    for _rank, _ts, c, view in pinned_raw:
        if len(pref_goal_claims) >= max(0, int(max_pref_goal_claims)):
            break
        pref_goal_claims.append(_compact_claim(c, view=view))
    for _rank, _ts, c, view in pref_goal_raw:
        if len(pref_goal_claims) >= max(0, int(max_pref_goal_claims)):
            break
        pref_goal_claims.append(_compact_claim(c, view=view))

    pref_goal_ids = {str(c.get("claim_id") or "").strip() for c in pref_goal_claims if isinstance(c, dict)}

    # Query-ranked claims from project+global (excluding values already included).
    scored: list[tuple[int, str, dict[str, Any], ThoughtDbView]] = []
    for view, scope in ((v_proj, "project"), (v_glob, "global")):
        for c in view.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=as_of_ts):
            if not isinstance(c, dict):
                continue
            cid = str(c.get("claim_id") or "").strip()
            if not cid or cid in values_ids or cid in pref_goal_ids:
                continue
            text = str(c.get("text") or "").strip()
            if not text:
                continue
            score = _score_tokens(tokens, text=text)
            if score <= 0:
                continue
            # Small boost for preferences/goals to keep the context value-driven.
            ct = str(c.get("claim_type") or "").strip()
            if ct in ("preference", "goal"):
                score += 1
            scored.append((score, scope, c, view))

    # Sort by score desc, then scope preference (project before global), then asserted_ts desc.
    scored.sort(
        key=lambda x: (
            -int(x[0]),
            0 if x[1] == "project" else 1,
            str(x[2].get("asserted_ts") or ""),
        ),
        reverse=False,
    )

    query_claims: list[dict[str, Any]] = []
    included_ids: set[str] = set(values_ids) | set(pref_goal_ids)
    for score, _scope, c, view in scored[: max(0, int(max_query_claims)) * 3]:
        cid = str(c.get("claim_id") or "").strip()
        if not cid or cid in included_ids:
            continue
        included_ids.add(cid)
        query_claims.append(_compact_claim(c, view=view))
        if len(query_claims) >= max(0, int(max_query_claims)):
            break

    # Edges among included claims (small budget). Prefer "reasoning" edges.
    edge_types = {"depends_on", "supports", "contradicts", "supersedes", "same_as"}
    edges: list[dict[str, Any]] = []
    seen_edges: set[str] = set()

    def add_edges_from_view(view: ThoughtDbView, *, scope: str) -> None:
        nonlocal edges
        for e in view.edges:
            if len(edges) >= max(0, int(max_edges)):
                break
            if not isinstance(e, dict):
                continue
            if str(e.get("kind") or "").strip() != "edge":
                continue
            et = str(e.get("edge_type") or "").strip()
            if et not in edge_types:
                continue
            frm = str(e.get("from_id") or "").strip()
            to = str(e.get("to_id") or "").strip()
            if not frm or not to:
                continue
            # Keep only edges among included claim ids.
            if frm not in included_ids or to not in included_ids:
                continue
            key = f"{scope}:{et}:{frm}->{to}"
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(_compact_edge(e, scope=scope))

    # Prefer project edges first, then global.
    add_edges_from_view(v_proj, scope="project")
    add_edges_from_view(v_glob, scope="global")

    notes = (
        f"tokens={len(tokens)} values_claims={len(values)} pref_goal_claims={len(pref_goal_claims)} "
        f"query_claims={len(query_claims)} edges={len(edges)} budgets(values={max_values_claims} pref_goal={max_pref_goal_claims} "
        f"query={max_query_claims} edges={max_edges})"
    )
    return ThoughtDbContext(
        as_of_ts=as_of_ts,
        query=q,
        values_claims=values,
        pref_goal_claims=pref_goal_claims,
        query_claims=query_claims,
        edges=edges,
        notes=notes,
    )
