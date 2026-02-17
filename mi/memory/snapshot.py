from __future__ import annotations

import secrets
import time
from typing import Any

from .text import truncate
from .types import MemoryItem
from ..core.storage import now_rfc3339


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


def build_snapshot_item(
    *,
    project_id: str,
    segment_id: str,
    thread_id: str,
    batch_id: str,
    task_hint: str,
    checkpoint_kind: str,
    status_hint: str,
    checkpoint_notes: str,
    segment_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], MemoryItem]:
    """Build a snapshot event + a MemoryItem for indexing (deterministic; no extra model call)."""

    facts: list[str] = []
    results: list[str] = []
    actions: list[str] = []
    unknowns: list[str] = []
    risk: list[str] = []
    recall: list[str] = []
    workflows: list[str] = []

    batch_ids: list[str] = []
    seen_batch: set[str] = set()
    event_ids: list[str] = []
    seen_event: set[str] = set()
    for rec in segment_records or []:
        if not isinstance(rec, dict):
            continue
        bid = str(rec.get("batch_id") or "").strip()
        if bid and bid not in seen_batch:
            seen_batch.add(bid)
            batch_ids.append(bid)
        eid = str(rec.get("event_id") or "").strip()
        if eid and eid not in seen_event:
            seen_event.add(eid)
            event_ids.append(eid)

        k = str(rec.get("kind") or "").strip()
        if k == "evidence":
            facts.extend(_safe_list_str(rec.get("facts"), limit=12))
            actions.extend(_safe_list_str(rec.get("actions"), limit=12))
            results.extend(_safe_list_str(rec.get("results"), limit=12))
            unknowns.extend(_safe_list_str(rec.get("unknowns"), limit=12))
            risk.extend(_safe_list_str(rec.get("risk_signals"), limit=8))
        elif k == "risk_event":
            cat = str(rec.get("category") or "").strip()
            sev = str(rec.get("severity") or "").strip()
            rs = _safe_list_str(rec.get("risk_signals"), limit=8)
            if cat or sev:
                risk.append(f"risk_event: {cat} severity={sev}".strip())
            for x in rs:
                risk.append(x)
        elif k == "cross_project_recall":
            recall.extend(_safe_list_str(rec.get("items"), limit=6))
        elif k == "workflow_trigger":
            wid = str(rec.get("workflow_id") or "").strip()
            name = str(rec.get("workflow_name") or "").strip()
            workflows.append(f"{wid} {name}".strip())

    # Dedup while preserving order.
    def dedup(xs: list[str], limit: int) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for x in xs:
            if len(out) >= limit:
                break
            s = str(x or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    facts = dedup(facts, 12)
    actions = dedup(actions, 12)
    results = dedup(results, 12)
    unknowns = dedup(unknowns, 10)
    risk = dedup(risk, 10)
    workflows = dedup(workflows, 6)

    tags = ["snapshot", checkpoint_kind or "other"]
    if status_hint:
        tags.append("status:" + status_hint)
    if workflows:
        tags.append("workflow")
    if risk:
        tags.append("risk")

    bullets: list[str] = []
    if workflows:
        bullets.append("workflows: " + "; ".join(workflows))
    if facts:
        bullets.append("facts: " + "; ".join(facts))
    if actions:
        bullets.append("actions: " + "; ".join(actions))
    if results:
        bullets.append("results: " + "; ".join(results))
    if unknowns:
        bullets.append("unknowns: " + "; ".join(unknowns))
    if risk:
        bullets.append("risk: " + "; ".join(risk))
    if recall:
        bullets.append("recall: " + "; ".join(dedup(recall, 6)))
    if checkpoint_notes:
        bullets.append("checkpoint_notes: " + truncate(checkpoint_notes.strip(), 240))

    title = truncate(task_hint.strip() or "segment snapshot", 140)
    body = "\n".join([f"- {b}" for b in bullets if b.strip()]).strip()
    if not body:
        body = "- (no salient events captured)"

    snapshot_id = f"snap_{time.time_ns()}_{secrets.token_hex(4)}"

    snap_event: dict[str, Any] = {
        "kind": "snapshot",
        "ts": now_rfc3339(),
        "snapshot_id": snapshot_id,
        "thread_id": thread_id,
        "project_id": project_id,
        "segment_id": segment_id,
        "batch_id": batch_id,
        "checkpoint_kind": checkpoint_kind,
        "status_hint": status_hint,
        "task_hint": truncate(task_hint, 200),
        "tags": tags,
        "text": truncate(body, 8000),
        "source_refs": [
            {
                "kind": "segment_records",
                "segment_id": segment_id,
                "batch_ids": batch_ids[:40],
                "event_ids": event_ids[:80],
            }
        ],
    }

    item = MemoryItem(
        item_id=f"snapshot:project:{project_id}:{snapshot_id}",
        kind="snapshot",
        scope="project",
        project_id=project_id,
        ts=snap_event["ts"],
        title=title,
        body=truncate(body, 8000),
        tags=tags,
        source_refs=snap_event["source_refs"],
    )
    return snap_event, item


def snapshot_item_from_event(ev: dict[str, Any]) -> MemoryItem | None:
    """Convert an EvidenceLog snapshot record into a recallable MemoryItem."""

    if not isinstance(ev, dict) or ev.get("kind") != "snapshot":
        return None
    project_id = str(ev.get("project_id") or "").strip()
    if not project_id:
        return None
    snapshot_id = str(ev.get("snapshot_id") or "").strip()
    segment_id = str(ev.get("segment_id") or "").strip() or "unknown_segment"
    batch_id = str(ev.get("batch_id") or "").strip() or "unknown_batch"
    ts = str(ev.get("ts") or "").strip() or now_rfc3339()
    task_hint = str(ev.get("task_hint") or "").strip()
    text = str(ev.get("text") or "").strip()
    tags = ev.get("tags") if isinstance(ev.get("tags"), list) else []
    refs = ev.get("source_refs") if isinstance(ev.get("source_refs"), list) else []
    title = truncate(task_hint or text or "snapshot", 140)
    body = truncate(text or "(empty snapshot)", 8000)
    item_id = f"snapshot:project:{project_id}:{snapshot_id}" if snapshot_id else f"snapshot:project:{project_id}:{segment_id}:{batch_id}"
    return MemoryItem(
        item_id=item_id,
        kind="snapshot",
        scope="project",
        project_id=project_id,
        ts=ts,
        title=title,
        body=body,
        tags=[str(x) for x in tags if str(x).strip()] if isinstance(tags, list) else ["snapshot"],
        source_refs=[x for x in refs if isinstance(x, dict)] if isinstance(refs, list) else [],
    )
