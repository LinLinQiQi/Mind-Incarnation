from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.runtime.autopilot.node_materialize import NodeMaterializeDeps, materialize_nodes_from_checkpoint


class NodeMaterializeHelpersTests(unittest.TestCase):
    def test_materialize_writes_nodes_edges_and_audit(self) -> None:
        node_ids: list[str] = []
        edge_ids: list[str] = []
        indexed: list[dict[str, object]] = []
        evidences: list[dict[str, object]] = []

        def _append_node_create(**kwargs):
            nid = f"nd_{len(node_ids) + 1}"
            node_ids.append(nid)
            return nid

        def _append_edge(**kwargs):
            eid = f"ed_{len(edge_ids) + 1}"
            edge_ids.append(eid)
            return eid

        def _upsert(items):
            indexed.extend([x for x in items if isinstance(x, dict)])

        def _build_index_item(**kwargs):
            return {"node_id": kwargs.get("node_id"), "node_type": kwargs.get("node_type")}

        def _evidence_append(rec):
            if isinstance(rec, dict):
                evidences.append(rec)
            return rec

        with tempfile.TemporaryDirectory() as td:
            deps = NodeMaterializeDeps(
                append_node_create=_append_node_create,
                append_edge=_append_edge,
                upsert_memory_items=_upsert,
                build_index_item=_build_index_item,
                evidence_append=_evidence_append,
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
                project_id="p1",
                nodes_path=Path(td) / "nodes.jsonl",
                task="ship",
                thread_id="tid_1",
                segment_id="seg_1",
            )
            materialize_nodes_from_checkpoint(
                enabled=True,
                seg_evidence=[
                    {"kind": "decide_next", "event_id": "ev_decide", "status": "not_done", "next_action": "send_to_hands", "notes": "n", "seq": 2},
                    {"kind": "evidence", "event_id": "ev_e1", "actions": ["run tests", "run lint"]},
                ],
                snapshot_rec={"event_id": "ev_snap", "text": "snapshot summary", "task_hint": "task"},
                base_batch_id="b1",
                checkpoint_kind="phase_change",
                status_hint="not_done",
                planned_next_input="continue",
                note="n",
                deps=deps,
            )

        self.assertEqual(len(node_ids), 3)  # summary + decision + action
        self.assertGreaterEqual(len(edge_ids), 3)
        self.assertEqual(len(indexed), 3)
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0].get("kind"), "node_materialized")
        self.assertEqual(len(evidences[0].get("written_nodes") or []), 3)


if __name__ == "__main__":
    unittest.main()
