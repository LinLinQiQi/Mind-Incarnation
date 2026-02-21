from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mi.core.paths import GlobalPaths, ProjectPaths
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.app_service import ThoughtDbApplicationService


class TestThoughtDbApplicationServiceShowRefs(unittest.TestCase):
    def test_find_evidence_event_prefers_project_then_global(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            gp = GlobalPaths(home_dir=home)

            eid = "ev_show_ref_0001"
            p_obj = {"event_id": eid, "kind": "evidence", "batch_id": "b1", "facts": ["project"]}
            g_obj = {"event_id": eid, "kind": "evidence", "batch_id": "bg", "facts": ["global"]}

            pp.evidence_log_path.parent.mkdir(parents=True, exist_ok=True)
            gp.global_evidence_log_path.parent.mkdir(parents=True, exist_ok=True)
            pp.evidence_log_path.write_text(json.dumps(p_obj) + "\n", encoding="utf-8")
            gp.global_evidence_log_path.write_text(json.dumps(g_obj) + "\n", encoding="utf-8")

            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            app = ThoughtDbApplicationService(tdb=tdb, project_paths=pp)

            scope1, obj1 = app.find_evidence_event_prefer_project(home_dir=home, event_id=eid, global_only=False)
            self.assertEqual(scope1, "project")
            self.assertEqual(str((obj1 or {}).get("batch_id") or ""), "b1")

            scope2, obj2 = app.find_evidence_event_prefer_project(home_dir=home, event_id=eid, global_only=True)
            self.assertEqual(scope2, "global")
            self.assertEqual(str((obj2 or {}).get("batch_id") or ""), "bg")


if __name__ == "__main__":
    unittest.main()
