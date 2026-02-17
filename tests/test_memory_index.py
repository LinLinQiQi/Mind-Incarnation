import tempfile
import unittest
from pathlib import Path

from mi.memory_service import MemoryService
from mi.paths import GlobalPaths, ProjectPaths
from mi.storage import append_jsonl, now_rfc3339
from mi.workflows import GlobalWorkflowStore


class TestMemoryIndex(unittest.TestCase):
    def test_ingest_prunes_disabled_global_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            gp = GlobalPaths(home_dir=home)
            gw = GlobalWorkflowStore(gp)

            wf_id = "wf_test"
            gw.write({"id": wf_id, "name": "Global Foo Workflow", "enabled": True})

            mem = MemoryService(home)
            mem.ingest_structured()
            hits = mem.search(query="Global Foo", top_k=10, kinds={"workflow"}, include_global=True, exclude_project_id="")
            self.assertTrue(any(h.kind == "workflow" and h.item_id.endswith(":" + wf_id) for h in hits))

            gw.write({"id": wf_id, "name": "Global Foo Workflow", "enabled": False})
            mem.ingest_structured()
            hits2 = mem.search(query="Global Foo", top_k=10, kinds={"workflow"}, include_global=True, exclude_project_id="")
            self.assertEqual(hits2, [])

    def test_rebuild_indexes_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            project_root.mkdir(exist_ok=True)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            pp.project_dir.mkdir(parents=True, exist_ok=True)

            snap_ev = {
                "kind": "snapshot",
                "ts": now_rfc3339(),
                "thread_id": "t1",
                "project_id": pp.project_id,
                "segment_id": "seg1",
                "batch_id": "b0.snapshot",
                "checkpoint_kind": "phase",
                "status_hint": "not_done",
                "task_hint": "hello task",
                "tags": ["snapshot", "phase"],
                "text": "- results: hello world",
                "source_refs": [{"kind": "segment_records", "segment_id": "seg1", "batch_ids": ["b0"]}],
            }
            append_jsonl(pp.evidence_log_path, snap_ev)

            mem = MemoryService(home)
            res = mem.rebuild(include_snapshots=True)
            self.assertTrue(bool(res.get("rebuilt", False)))

            hits = mem.search(query="hello world", top_k=5, kinds={"snapshot"}, include_global=True, exclude_project_id="")
            self.assertTrue(any(h.kind == "snapshot" and h.project_id == pp.project_id for h in hits))

    def test_rebuild_indexes_thoughtdb_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            project_root.mkdir(exist_ok=True)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            pp.project_dir.mkdir(parents=True, exist_ok=True)

            node_id = "nd_test"
            append_jsonl(
                pp.thoughtdb_nodes_path,
                {
                    "kind": "node",
                    "version": "v1",
                    "node_id": node_id,
                    "node_type": "summary",
                    "title": "My Summary",
                    "text": "hello node world",
                    "visibility": "project",
                    "scope": "project",
                    "project_id": pp.project_id,
                    "asserted_ts": now_rfc3339(),
                    "tags": ["auto"],
                    "source_refs": [{"kind": "evidence_event", "event_id": "ev_x"}],
                    "confidence": 1.0,
                    "notes": "",
                },
            )

            mem = MemoryService(home)
            mem.rebuild(include_snapshots=False)
            hits = mem.search(query="hello node world", top_k=5, kinds={"node"}, include_global=True, exclude_project_id="")
            self.assertTrue(any(h.kind == "node" and h.item_id.endswith(":" + node_id) for h in hits))


if __name__ == "__main__":
    unittest.main()
