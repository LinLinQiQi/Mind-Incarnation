from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class NodeMaterializeDeps:
    append_node_create: Callable[..., str]
    append_edge: Callable[..., str]
    upsert_memory_items: Callable[[list[Any]], None]
    build_index_item: Callable[..., Any]
    evidence_append: Callable[[dict[str, Any]], Any]
    now_ts: Callable[[], str]
    truncate: Callable[[str, int], str]
    project_id: str
    nodes_path: Path
    task: str
    thread_id: str
    segment_id: str


def materialize_nodes_from_checkpoint(
    *,
    enabled: bool,
    seg_evidence: list[dict[str, Any]],
    snapshot_rec: dict[str, Any] | None,
    base_batch_id: str,
    checkpoint_kind: str,
    status_hint: str,
    planned_next_input: str,
    note: str,
    deps: NodeMaterializeDeps,
) -> None:
    """Materialize Decision/Action/Summary nodes at checkpoint (deterministic; best-effort)."""

    if not bool(enabled):
        return

    src_ids: list[str] = []
    seen_src: set[str] = set()

    def add_src(eid: str) -> None:
        s = str(eid or "").strip()
        if not s or s in seen_src:
            return
        seen_src.add(s)
        src_ids.append(s)

    snap_event_id = ""
    snap_text = ""
    snap_task = ""
    snap_tags: list[str] = []
    if isinstance(snapshot_rec, dict):
        snap_event_id = str(snapshot_rec.get("event_id") or "").strip()
        snap_text = str(snapshot_rec.get("text") or "").strip()
        snap_task = str(snapshot_rec.get("task_hint") or "").strip()
        tags = snapshot_rec.get("tags") if isinstance(snapshot_rec.get("tags"), list) else []
        snap_tags = [str(x).strip() for x in tags if str(x).strip()][:12]
    if snap_event_id:
        add_src(snap_event_id)

    last_decide: dict[str, Any] | None = None
    last_seq = -1
    for rec in seg_evidence or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("kind") or "").strip() != "decide_next":
            continue
        seq = rec.get("seq")
        try:
            seq_i = int(seq) if seq is not None else -1
        except Exception:
            seq_i = -1
        if seq_i >= last_seq:
            last_seq = seq_i
            last_decide = rec
    if last_decide is None:
        for rec in reversed(seg_evidence or []):
            if isinstance(rec, dict) and str(rec.get("kind") or "").strip() == "decide_next":
                last_decide = rec
                break

    decide_event_id = ""
    decide_status = ""
    decide_next_action = ""
    decide_notes = ""
    if isinstance(last_decide, dict):
        decide_event_id = str(last_decide.get("event_id") or "").strip()
        decide_status = str(last_decide.get("status") or "").strip()
        decide_next_action = str(last_decide.get("next_action") or "").strip()
        decide_notes = str(last_decide.get("notes") or "").strip()
    if decide_event_id:
        add_src(decide_event_id)

    action_lines: list[str] = []
    action_src_event_ids: list[str] = []
    seen_actions: set[str] = set()
    for rec in seg_evidence or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("kind") or "").strip() != "evidence":
            continue
        eid = str(rec.get("event_id") or "").strip()
        acts = rec.get("actions") if isinstance(rec.get("actions"), list) else []
        for a in acts[:20]:
            s = str(a or "").strip()
            if not s or s in seen_actions:
                continue
            seen_actions.add(s)
            action_lines.append(s)
            if eid:
                action_src_event_ids.append(eid)
    for eid in action_src_event_ids[:12]:
        add_src(eid)

    written_nodes: list[dict[str, str]] = []
    written_edges: list[dict[str, str]] = []
    index_items: list[Any] = []
    base_node_refs = [{"kind": "evidence_event", "event_id": x} for x in src_ids[:12] if str(x).strip()]

    def write_edge(*, edge_type: str, frm: str, to: str, source_eids: list[str], notes: str) -> None:
        if not frm or not to:
            return
        try:
            eid = deps.append_edge(
                edge_type=edge_type,
                from_id=frm,
                to_id=to,
                scope="project",
                visibility="project",
                source_event_ids=[x for x in source_eids if str(x).strip()][:8],
                notes=notes,
            )
            written_edges.append({"edge_id": eid, "edge_type": edge_type, "from_id": frm, "to_id": to})
        except Exception:
            return

    ok = True
    err = ""
    try:
        if snap_text:
            title = f"Summary ({checkpoint_kind or 'checkpoint'}): {snap_task or deps.task}".strip()
            text = "\n".join(
                [
                    f"checkpoint_kind: {checkpoint_kind or ''}".strip(),
                    f"status_hint: {status_hint or ''}".strip(),
                    f"batch_id: {base_batch_id}".strip(),
                    "",
                    snap_text.strip(),
                ]
            ).strip()
            tags = ["auto", "checkpoint", "node:summary"]
            if checkpoint_kind:
                tags.append("checkpoint_kind:" + str(checkpoint_kind))
            if status_hint:
                tags.append("status:" + str(status_hint))
            tags.extend([f"snapshot_tag:{t}" for t in snap_tags[:6] if t])
            nid = deps.append_node_create(
                node_type="summary",
                title=title,
                text=text,
                scope="project",
                visibility="project",
                tags=tags,
                source_event_ids=src_ids[:12],
                confidence=1.0,
                notes="auto materialize (snapshot)",
            )
            written_nodes.append({"node_id": nid, "node_type": "summary"})
            if snap_event_id:
                write_edge(
                    edge_type="derived_from",
                    frm=nid,
                    to=snap_event_id,
                    source_eids=[snap_event_id],
                    notes="auto derived_from snapshot",
                )
            try:
                index_items.append(
                    deps.build_index_item(
                        node_id=nid,
                        node_type="summary",
                        title=title,
                        text=text,
                        scope="project",
                        project_id=deps.project_id,
                        ts=deps.now_ts(),
                        visibility="project",
                        tags=tags,
                        nodes_path=deps.nodes_path,
                        source_refs=base_node_refs,
                    )
                )
            except Exception:
                pass

        if decide_next_action or decide_notes or decide_status:
            title = f"Decision: {decide_next_action or 'unknown'} ({decide_status or 'unknown'})".strip()
            text = "\n".join(
                [
                    f"status: {decide_status}".strip(),
                    f"next_action: {decide_next_action}".strip(),
                    (f"planned_next_input: {deps.truncate(planned_next_input or '', 1200)}" if planned_next_input else "").strip(),
                    (f"notes: {decide_notes}" if decide_notes else "").strip(),
                ]
            ).strip()
            tags = ["auto", "checkpoint", "node:decision"]
            if decide_next_action:
                tags.append("next_action:" + decide_next_action)
            if decide_status:
                tags.append("status:" + decide_status)
            nid = deps.append_node_create(
                node_type="decision",
                title=title,
                text=text,
                scope="project",
                visibility="project",
                tags=tags,
                source_event_ids=src_ids[:12],
                confidence=1.0,
                notes="auto materialize (decide_next)",
            )
            written_nodes.append({"node_id": nid, "node_type": "decision"})
            if decide_event_id:
                write_edge(
                    edge_type="derived_from",
                    frm=nid,
                    to=decide_event_id,
                    source_eids=[decide_event_id],
                    notes="auto derived_from decide_next",
                )
            try:
                index_items.append(
                    deps.build_index_item(
                        node_id=nid,
                        node_type="decision",
                        title=title,
                        text=text,
                        scope="project",
                        project_id=deps.project_id,
                        ts=deps.now_ts(),
                        visibility="project",
                        tags=tags,
                        nodes_path=deps.nodes_path,
                        source_refs=base_node_refs,
                    )
                )
            except Exception:
                pass

        if action_lines:
            head = action_lines[0] if action_lines else ""
            title = f"Actions: {deps.truncate(head, 120)}".strip()
            body = "\n".join([f"- {a}" for a in action_lines[:24] if str(a).strip()]).strip()
            text = "\n".join(
                [
                    f"batch_id: {base_batch_id}".strip(),
                    "",
                    body,
                ]
            ).strip()
            tags = ["auto", "checkpoint", "node:action"]
            nid = deps.append_node_create(
                node_type="action",
                title=title,
                text=text,
                scope="project",
                visibility="project",
                tags=tags,
                source_event_ids=src_ids[:12],
                confidence=1.0,
                notes="auto materialize (segment actions)",
            )
            written_nodes.append({"node_id": nid, "node_type": "action"})
            for eid in action_src_event_ids[:12]:
                if eid:
                    write_edge(
                        edge_type="derived_from",
                        frm=nid,
                        to=eid,
                        source_eids=[eid],
                        notes="auto derived_from evidence(actions)",
                    )
            try:
                index_items.append(
                    deps.build_index_item(
                        node_id=nid,
                        node_type="action",
                        title=title,
                        text=text,
                        scope="project",
                        project_id=deps.project_id,
                        ts=deps.now_ts(),
                        visibility="project",
                        tags=tags,
                        nodes_path=deps.nodes_path,
                        source_refs=base_node_refs,
                    )
                )
            except Exception:
                pass
    except Exception as e:
        ok = False
        err = f"{type(e).__name__}: {e}"

    if index_items:
        try:
            deps.upsert_memory_items([x for x in index_items if x])
        except Exception:
            pass

    try:
        deps.evidence_append(
            {
                "kind": "node_materialized",
                "batch_id": f"{base_batch_id}.node_materialized",
                "ts": deps.now_ts(),
                "thread_id": str(deps.thread_id or ""),
                "segment_id": str(deps.segment_id or ""),
                "checkpoint_kind": str(checkpoint_kind or ""),
                "status_hint": str(status_hint or ""),
                "note": (note or "").strip(),
                "ok": bool(ok),
                "error": deps.truncate(err, 400),
                "snapshot_event_id": snap_event_id,
                "decide_next_event_id": decide_event_id,
                "source_event_ids": src_ids[:20],
                "written_nodes": written_nodes,
                "written_edges": written_edges,
            }
        )
    except Exception:
        return
