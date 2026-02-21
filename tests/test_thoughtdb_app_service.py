from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.app_service import ThoughtDbApplicationService


class TestThoughtDbApplicationService(unittest.TestCase):
    def test_effective_claim_lookup_and_dedup_list(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            app = ThoughtDbApplicationService(tdb=tdb, project_paths=pp)

            cid_proj = tdb.append_claim_create(
                claim_type="goal",
                text="Keep behavior unchanged",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )
            _cid_glob = tdb.append_claim_create(
                claim_type="goal",
                text="Keep behavior unchanged",
                scope="global",
                visibility="global",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )

            found_scope, obj = app.find_claim_effective(cid_proj)
            self.assertEqual(found_scope, "project")
            self.assertIsInstance(obj, dict)
            self.assertEqual(str((obj or {}).get("claim_id") or ""), cid_proj)

            items = app.list_effective_claims(
                include_inactive=False,
                include_aliases=False,
                as_of_ts="2026-01-01T00:00:00Z",
                filter_fn=None,
            )
            same_text = [c for c in items if isinstance(c, dict) and str(c.get("text") or "").strip() == "Keep behavior unchanged"]
            self.assertEqual(len(same_text), 1)

    def test_node_lookup_edges_and_subgraph(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            app = ThoughtDbApplicationService(tdb=tdb, project_paths=pp)

            n1 = tdb.append_node_create(
                node_type="decision",
                title="Pick strategy",
                text="Use subgraph helper",
                scope="project",
                visibility="project",
                tags=[],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )
            n2 = tdb.append_node_create(
                node_type="action",
                title="Do migration",
                text="Call application service",
                scope="project",
                visibility="project",
                tags=[],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )
            _eid = tdb.append_edge(
                edge_type="supports",
                from_id=n1,
                to_id=n2,
                scope="project",
                visibility="project",
                source_event_ids=[],
                notes="test",
            )

            found_scope, obj = app.find_node_effective(n1)
            self.assertEqual(found_scope, "project")
            self.assertEqual(str((obj or {}).get("node_id") or ""), n1)

            edges = app.related_edges_for_id(scope="project", item_id=n1)
            self.assertTrue(any(str(e.get("to_id") or "") == n2 for e in edges if isinstance(e, dict)))

            graph = app.build_subgraph(
                scope="project",
                root_id=n1,
                depth=1,
                direction="both",
                edge_types={"supports"},
                include_inactive=False,
                include_aliases=False,
            )
            self.assertEqual(str(graph.get("root_id") or ""), n1)
            self.assertTrue(any(str(e.get("edge_type") or "") == "supports" for e in graph.get("edges", [])))


if __name__ == "__main__":
    unittest.main()
