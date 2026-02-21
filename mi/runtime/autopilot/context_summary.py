from __future__ import annotations

from typing import Any


def summarize_thought_db_context(ctx: Any) -> dict[str, Any]:
    """Build a compact, stable summary payload for decide_next audit records."""

    return {
        "as_of_ts": str(getattr(ctx, "as_of_ts", "") or ""),
        "node_ids": [
            str(n.get("node_id") or "")
            for n in (getattr(ctx, "nodes", None) or [])
            if isinstance(n, dict) and str(n.get("node_id") or "").strip()
        ],
        "values_claim_ids": [
            str(c.get("claim_id") or "")
            for c in (getattr(ctx, "values_claims", None) or [])
            if isinstance(c, dict) and str(c.get("claim_id") or "").strip()
        ],
        "pref_goal_claim_ids": [
            str(c.get("claim_id") or "")
            for c in (getattr(ctx, "pref_goal_claims", None) or [])
            if isinstance(c, dict) and str(c.get("claim_id") or "").strip()
        ],
        "query_claim_ids": [
            str(c.get("claim_id") or "")
            for c in (getattr(ctx, "query_claims", None) or [])
            if isinstance(c, dict) and str(c.get("claim_id") or "").strip()
        ],
        "edges_n": len(getattr(ctx, "edges", None) or []),
        "notes": str(getattr(ctx, "notes", "") or "").strip(),
    }
