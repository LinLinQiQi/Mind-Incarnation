from __future__ import annotations

from typing import Any

from .thoughtdb import ThoughtDbStore, ThoughtDbView
from .values import VALUES_BASE_TAG


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _compact_claim(c: dict[str, Any], *, view: ThoughtDbView) -> dict[str, Any]:
    cid = str(c.get("claim_id") or "").strip()
    ct = str(c.get("claim_type") or "").strip()
    text = str(c.get("text") or "").strip()
    tags = c.get("tags") if isinstance(c.get("tags"), list) else []
    tagset = [str(x).strip() for x in tags if str(x).strip()]
    return {
        "claim_id": cid,
        "canonical_id": view.resolve_id(cid),
        "status": view.claim_status(cid),
        "claim_type": ct,
        "scope": str(c.get("scope") or "").strip(),
        "visibility": str(c.get("visibility") or "").strip(),
        "asserted_ts": str(c.get("asserted_ts") or "").strip(),
        "text": _truncate(text, 420),
        "tags": tagset[:16],
    }


def _iter_pref_goal_claims(view: ThoughtDbView, *, as_of_ts: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in view.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=as_of_ts):
        if not isinstance(c, dict):
            continue
        ct = str(c.get("claim_type") or "").strip()
        if ct not in ("preference", "goal"):
            continue
        out.append(c)
    out.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)
    return out


def collect_canonical_pref_goal_claims(
    *,
    tdb: ThoughtDbStore,
    as_of_ts: str,
    max_values: int = 8,
    max_other: int = 10,
) -> list[dict[str, Any]]:
    """Collect canonical preference/goal claims for Hands "light injection".

    Order:
    1) Global values claims tagged `values:base` (preference/goal)
    2) Other recent preference/goal claims (project first, then global)
    """

    v_proj = tdb.load_view(scope="project")
    v_glob = tdb.load_view(scope="global")

    values_raw: list[dict[str, Any]] = []
    for c in _iter_pref_goal_claims(v_glob, as_of_ts=as_of_ts):
        tags = c.get("tags") if isinstance(c.get("tags"), list) else []
        tagset = {str(x).strip() for x in tags if str(x).strip()}
        if VALUES_BASE_TAG in tagset:
            values_raw.append(c)
    values_raw = values_raw[: max(0, int(max_values))]
    values = [_compact_claim(c, view=v_glob) for c in values_raw]
    seen_ids = {str(x.get("claim_id") or "").strip() for x in values if isinstance(x, dict)}

    other: list[dict[str, Any]] = []
    for view, scope in ((v_proj, "project"), (v_glob, "global")):
        for c in _iter_pref_goal_claims(view, as_of_ts=as_of_ts):
            cid = str(c.get("claim_id") or "").strip()
            if not cid or cid in seen_ids:
                continue
            # Skip values:base duplicates; they are already in `values`.
            tags = c.get("tags") if isinstance(c.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            if VALUES_BASE_TAG in tagset:
                continue
            other.append(_compact_claim(c, view=view))
            seen_ids.add(cid)
            if len(other) >= max(0, int(max_other)):
                break
        if len(other) >= max(0, int(max_other)):
            break

    return values + other


def build_light_injection(
    *,
    mindspec_base: dict[str, Any],
    tdb: ThoughtDbStore,
    as_of_ts: str,
) -> str:
    """Build MI "light injection" for Hands from canonical Thought DB claims."""

    defaults = mindspec_base.get("defaults") if isinstance(mindspec_base.get("defaults"), dict) else {}
    refactor_intent = str(defaults.get("refactor_intent", "behavior_preserving"))
    ask_when_uncertain = bool(defaults.get("ask_when_uncertain", True))

    claims = collect_canonical_pref_goal_claims(tdb=tdb, as_of_ts=as_of_ts)

    parts: list[str] = []
    parts.append("[MI Light Injection]")

    parts.append("Canonical values/preferences (Thought DB Claims):")
    if claims:
        for c in claims[:18]:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("claim_id") or "").strip()
            ct = str(c.get("claim_type") or "").strip()
            sc = str(c.get("scope") or "").strip()
            text = str(c.get("text") or "").strip()
            if not text:
                continue
            head = f"- [{ct or 'claim'}] {text}"
            if cid and sc:
                head = f"{head} ({sc}:{cid})"
            elif cid:
                head = f"{head} ({cid})"
            parts.append(head)
    else:
        parts.append("- (none found). If this is unexpected, run `mi init --values ...` to set canonical values/preferences.")

    parts.append("")
    parts.append("Defaults:")
    parts.append(f"- Refactor intent: {refactor_intent} (unless explicitly requested otherwise)")
    parts.append(f"- When uncertain: {'ask' if ask_when_uncertain else 'proceed'}")
    parts.append("- If a potentially external action (network/install/push/publish) is NOT clearly covered, pause and ask.")

    return "\n".join([p for p in parts if p is not None]).strip() + "\n"

