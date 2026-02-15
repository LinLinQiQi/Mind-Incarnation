import tempfile
import unittest
from pathlib import Path

from mi.memory import MemoryIndex, ingest_learned_and_workflows, rebuild_memory_index
from mi.mindspec import MindSpecStore
from mi.paths import GlobalPaths, ProjectPaths
from mi.storage import append_jsonl, now_rfc3339
from mi.workflows import GlobalWorkflowStore


class TestMemoryIndex(unittest.TestCase):
    def test_ingest_prunes_disabled_global_learned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            project_root = home / "proj"
            project_root.mkdir()
            store = MindSpecStore(home_dir=str(home))

            learned_id = store.append_learned(project_root=project_root, scope="global", text="PREFER_X", rationale="r")
            index = MemoryIndex(home)
            ingest_learned_and_workflows(home_dir=home, index=index)

            hits = index.search(query="PREFER_X", top_k=10, kinds={"learned"}, include_global=True, exclude_project_id="")
            self.assertTrue(any(h.kind == "learned" for h in hits))

            store.disable_learned(project_root=project_root, scope="global", target_id=learned_id, rationale="nope")
            ingest_learned_and_workflows(home_dir=home, index=index)

            hits2 = index.search(query="PREFER_X", top_k=10, kinds={"learned"}, include_global=True, exclude_project_id="")
            self.assertEqual(hits2, [])

    def test_ingest_prunes_disabled_global_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            gp = GlobalPaths(home_dir=home)
            gw = GlobalWorkflowStore(gp)

            wf_id = "wf_test"
            gw.write({"id": wf_id, "name": "Global Foo Workflow", "enabled": True})

            index = MemoryIndex(home)
            ingest_learned_and_workflows(home_dir=home, index=index)
            hits = index.search(query="Global Foo", top_k=10, kinds={"workflow"}, include_global=True, exclude_project_id="")
            self.assertTrue(any(h.kind == "workflow" and h.item_id.endswith(":" + wf_id) for h in hits))

            gw.write({"id": wf_id, "name": "Global Foo Workflow", "enabled": False})
            ingest_learned_and_workflows(home_dir=home, index=index)
            hits2 = index.search(query="Global Foo", top_k=10, kinds={"workflow"}, include_global=True, exclude_project_id="")
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

            res = rebuild_memory_index(home_dir=home, include_snapshots=True)
            self.assertTrue(bool(res.get("rebuilt", False)))

            index = MemoryIndex(home)
            hits = index.search(query="hello world", top_k=5, kinds={"snapshot"}, include_global=True, exclude_project_id="")
            self.assertTrue(any(h.kind == "snapshot" and h.project_id == pp.project_id for h in hits))


if __name__ == "__main__":
    unittest.main()
