from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .global_ledger import append_global_event
from .storage import now_rfc3339
from .thoughtdb import ThoughtDbStore, ThoughtDbView


VALUES_BASE_TAG = "values:base"


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def write_values_set_event(
    *,
    home_dir: Path,
    values_text: str,
    compiled_mindspec: dict[str, Any] | None,
    notes: str = "",
) -> dict[str, Any]:
    """Append a global ledger event for a values update and return the record (with event_id)."""

    payload: dict[str, Any] = {
        "values_text": str(values_text or ""),
        "compiled_mindspec": compiled_mindspec if isinstance(compiled_mindspec, dict) else {},
        "notes": str(notes or "").strip(),
    }
    return append_global_event(home_dir=home_dir, kind="values_set", payload=payload)


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
        "asserted_ts": str(c.get("asserted_ts") or "").strip(),
        "valid_from": c.get("valid_from"),
        "valid_to": c.get("valid_to"),
        "text": _truncate(str(c.get("text") or "").strip(), 420),
        "tags": [str(x) for x in (c.get("tags") or []) if str(x).strip()][:16] if isinstance(c.get("tags"), list) else [],
        "source_event_ids": ev_ids,
    }


def existing_values_claims(*, tdb: ThoughtDbStore, limit: int = 40) -> list[dict[str, Any]]:
    """Return compact active canonical value claims (global scope) for prompting."""

    try:
        lim = int(limit)
    except Exception:
        lim = 40
    lim = max(1, min(200, lim))

    v = tdb.load_view(scope="global")
    out: list[dict[str, Any]] = []
    for c in v.iter_claims(include_inactive=False, include_aliases=False):
        if not isinstance(c, dict):
            continue
        tags = c.get("tags") if isinstance(c.get("tags"), list) else []
        if VALUES_BASE_TAG not in {str(x).strip() for x in tags if str(x).strip()}:
            continue
        out.append(_compact_claim(c, view=v))
        if len(out) >= lim:
            break
    return out


@dataclass(frozen=True)
class ValuesPatchApplyResult:
    ok: bool
    values_event_id: str
    applied: dict[str, Any]
    retracted: list[str]
    notes: str


def apply_values_claim_patch(
    *,
    tdb: ThoughtDbStore,
    patch_obj: dict[str, Any],
    values_event_id: str,
    min_confidence: float,
    max_claims: int,
) -> ValuesPatchApplyResult:
    """Apply a values claim patch into the global Thought DB (append-only; best-effort).

    The patch object is expected to contain:
    - claims: mine_claims-compatible claim list (local_id + claim fields)
    - edges: mine_claims-compatible edges list (may reference existing claim_id or local_id)
    - retract_claim_ids: list of existing value claim_ids to retract (optional)
    """

    ev_id = str(values_event_id or "").strip()
    if not ev_id:
        return ValuesPatchApplyResult(ok=False, values_event_id="", applied={}, retracted=[], notes="missing values_event_id")

    patch = patch_obj if isinstance(patch_obj, dict) else {}
    raw_claims = patch.get("claims") if isinstance(patch.get("claims"), list) else []
    raw_edges = patch.get("edges") if isinstance(patch.get("edges"), list) else []
    notes = str(patch.get("notes") or "").strip()

    # Ensure all created value claims cite the values_set event and carry stable tags.
    claims2: list[dict[str, Any]] = []
    for ch in raw_claims:
        if not isinstance(ch, dict):
            continue
        ct = str(ch.get("claim_type") or "").strip()
        if ct not in ("preference", "goal"):
            # Values migration focuses on preference/goal claims.
            continue
        d = dict(ch)
        d["scope"] = "global"
        d["visibility"] = "global"

        ev_ids = d.get("source_event_ids") if isinstance(d.get("source_event_ids"), list) else []
        ev_ids2 = [str(x).strip() for x in ev_ids if str(x).strip()]
        if ev_id not in ev_ids2:
            ev_ids2.insert(0, ev_id)
        d["source_event_ids"] = ev_ids2[:5]

        tags = d.get("tags") if isinstance(d.get("tags"), list) else []
        tags2 = [str(x).strip() for x in tags if str(x).strip()]
        if VALUES_BASE_TAG not in tags2:
            tags2.insert(0, VALUES_BASE_TAG)
        tag_ev = f"values_set:{ev_id}"
        if tag_ev not in tags2:
            tags2.append(tag_ev)
        d["tags"] = tags2[:20]
        if "notes" not in d:
            d["notes"] = ""
        claims2.append(d)

    # Ensure edges cite the values_set event (required by Thought DB provenance constraints).
    edges2: list[dict[str, Any]] = []
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        d = dict(e)
        ev_ids = d.get("source_event_ids") if isinstance(d.get("source_event_ids"), list) else []
        ev_ids2 = [str(x).strip() for x in ev_ids if str(x).strip()]
        if ev_id not in ev_ids2:
            ev_ids2.insert(0, ev_id)
        d["source_event_ids"] = ev_ids2[:5]
        edges2.append(d)

    applied = tdb.apply_mined_output(
        output={"claims": claims2, "edges": edges2, "notes": notes},
        allowed_event_ids={ev_id},
        min_confidence=min_confidence,
        max_claims=max_claims,
    )

    # Retract removed/invalidated value claims (append-only).
    retract_ids = patch.get("retract_claim_ids") if isinstance(patch.get("retract_claim_ids"), list) else []
    retract2 = [str(x).strip() for x in retract_ids if isinstance(x, str) and str(x).strip()]

    existing_ids = {str(c.get("claim_id") or "").strip() for c in existing_values_claims(tdb=tdb, limit=500)}
    retracted: list[str] = []
    for cid in retract2[:80]:
        if cid not in existing_ids:
            continue
        try:
            tdb.append_claim_retract(
                claim_id=cid,
                scope="global",
                rationale=f"values_update at {now_rfc3339()}",
                source_event_ids=[ev_id],
            )
            retracted.append(cid)
        except Exception:
            continue

    return ValuesPatchApplyResult(
        ok=True,
        values_event_id=ev_id,
        applied=applied if isinstance(applied, dict) else {},
        retracted=retracted,
        notes=notes,
    )

