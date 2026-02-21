from __future__ import annotations

from typing import Any

from ....core.storage import now_rfc3339
from ....thoughtdb import ThoughtDbStore, claim_signature
from ....thoughtdb.pins import TESTLESS_STRATEGY_TAG


TESTLESS_STRATEGY_PREFIX = "When this project has no tests, use this verification strategy:"


def testless_strategy_claim_text(strategy: str) -> str:
    s = " ".join((strategy or "").strip().split())
    if not s:
        return ""
    return f"{TESTLESS_STRATEGY_PREFIX} {s}"


def parse_testless_strategy_from_claim_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith(TESTLESS_STRATEGY_PREFIX):
        return t[len(TESTLESS_STRATEGY_PREFIX) :].strip()
    return t


def find_testless_strategy_claim(*, tdb: ThoughtDbStore, as_of_ts: str) -> dict[str, Any] | None:
    """Return the active project-scoped testless strategy preference claim (best-effort)."""

    v = tdb.load_view(scope="project")
    for c in v.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=as_of_ts):
        if not isinstance(c, dict):
            continue
        ct = str(c.get("claim_type") or "").strip()
        if ct not in ("preference", "goal"):
            continue
        tags = c.get("tags") if isinstance(c.get("tags"), list) else []
        tagset = {str(x).strip() for x in tags if str(x).strip()}
        if TESTLESS_STRATEGY_TAG in tagset:
            return c
    return None


def upsert_testless_strategy_claim(
    *,
    tdb: ThoughtDbStore,
    project_id: str,
    strategy_text: str,
    source_event_id: str,
    source: str,
    rationale: str,
) -> str:
    """Create/update the project-scoped testless verification strategy as a preference Claim."""

    s = (strategy_text or "").strip()
    if not s:
        return ""

    text = testless_strategy_claim_text(s)
    if not text:
        return ""

    as_of = now_rfc3339()
    existing = find_testless_strategy_claim(tdb=tdb, as_of_ts=as_of)
    existing_id = str(existing.get("claim_id") or "").strip() if isinstance(existing, dict) else ""
    existing_text = str(existing.get("text") or "").strip() if isinstance(existing, dict) else ""
    if existing_id and existing_text == text:
        return existing_id

    sig = claim_signature(claim_type="preference", scope="project", project_id=project_id, text=text)
    sig_map = tdb.existing_signature_map(scope="project")
    if sig in sig_map:
        cid0 = str(sig_map[sig])
        if existing_id and existing_id != cid0 and source_event_id:
            try:
                tdb.append_edge(
                    edge_type="supersedes",
                    from_id=existing_id,
                    to_id=cid0,
                    scope="project",
                    visibility="project",
                    source_event_ids=[source_event_id],
                    notes="testless strategy dedupe",
                )
            except Exception:
                pass
        return cid0

    tags = [TESTLESS_STRATEGY_TAG, "mi:verify", "mi:testless", f"mi:source:{(source or '').strip() or 'unknown'}"]
    note = (rationale or "").strip()
    if note:
        note = f"{note} (source={source})"
    else:
        note = f"source={source}"

    try:
        cid = tdb.append_claim_create(
            claim_type="preference",
            text=text,
            scope="project",
            visibility="project",
            valid_from=None,
            valid_to=None,
            tags=tags,
            source_event_ids=([str(source_event_id).strip()] if str(source_event_id or "").strip() else []),
            confidence=1.0,
            notes=note,
        )
    except Exception:
        return ""

    if existing_id and source_event_id:
        try:
            tdb.append_edge(
                edge_type="supersedes",
                from_id=existing_id,
                to_id=cid,
                scope="project",
                visibility="project",
                source_event_ids=[source_event_id],
                notes="update testless verification strategy",
            )
        except Exception:
            pass

    return cid

