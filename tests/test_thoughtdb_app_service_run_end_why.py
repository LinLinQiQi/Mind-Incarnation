from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.memory.service import MemoryService
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.app_service import ThoughtDbApplicationService


class TestThoughtDbApplicationServiceRunEndWhy(unittest.TestCase):
    def test_run_end_why_candidates_prefers_hints(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            mem = MemoryService(home)
            app = ThoughtDbApplicationService(tdb=tdb, project_paths=pp, mem=mem)

            cid = tdb.append_claim_create(
                claim_type="goal",
                text="Prefer deterministic run-end why trace",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["test"],
                source_event_ids=["ev_hint_0002"],
                confidence=1.0,
                notes="",
            )

            target_obj = {
                "kind": "decide_next",
                "event_id": "ev_decide_0002",
                "batch_id": "b9",
                "status": "not_done",
                "next_action": "send_to_hands",
                "thought_db": {
                    "values_claim_ids": [],
                    "pref_goal_claim_ids": [cid],
                    "query_claim_ids": [],
                    "node_ids": [],
                },
            }

            query, candidates, candidate_ids = app.run_end_why_candidates(
                target_obj=target_obj,
                target_event_id="ev_decide_0002",
                top_k=12,
                as_of_ts="2026-01-01T00:00:00Z",
            )
            self.assertTrue(query.strip())
            self.assertTrue(any(str(c.get("claim_id") or "") == cid for c in candidates if isinstance(c, dict)))
            self.assertIn(cid, candidate_ids)


if __name__ == "__main__":
    unittest.main()
