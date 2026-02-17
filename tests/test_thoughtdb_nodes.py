from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.thoughtdb import ThoughtDbStore


class TestThoughtDbNodes(unittest.TestCase):
    def test_nodes_create_retract_and_redirects(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            ev = "ev_test_node_000001"
            n1 = tdb.append_node_create(
                node_type="decision",
                title="Pick approach A",
                text="Decision: use approach A",
                scope="project",
                visibility="project",
                tags=["decision"],
                source_event_ids=[ev],
                confidence=0.9,
                notes="",
            )
            n2 = tdb.append_node_create(
                node_type="decision",
                title="Pick approach A (duplicate)",
                text="Decision: use approach A",
                scope="project",
                visibility="project",
                tags=["decision"],
                source_event_ids=[ev],
                confidence=0.9,
                notes="",
            )

            # same_as redirects should apply to nodes as well (edge is generic).
            tdb.append_edge(
                edge_type="same_as",
                from_id=n2,
                to_id=n1,
                scope="project",
                visibility="project",
                source_event_ids=[ev],
                notes="dedupe",
            )

            v = tdb.load_view(scope="project")
            self.assertIn(n1, v.nodes_by_id)
            self.assertIn(n2, v.nodes_by_id)
            self.assertEqual(v.resolve_id(n2), n1)

            # iter_nodes hides aliases by default.
            ids = [str(x.get("node_id") or "") for x in v.iter_nodes(include_inactive=True, include_aliases=False)]
            self.assertIn(n1, ids)
            self.assertNotIn(n2, ids)

            # Retract should change status.
            tdb.append_node_retract(node_id=n1, scope="project", rationale="no longer valid", source_event_ids=[ev])
            v2 = tdb.load_view(scope="project")
            self.assertEqual(v2.node_status(n1), "retracted")

            # Supersedes should change status (edge is generic).
            n3 = tdb.append_node_create(
                node_type="decision",
                title="Pick approach B",
                text="Decision: switch to approach B",
                scope="project",
                visibility="project",
                tags=["decision"],
                source_event_ids=[ev],
                confidence=0.8,
                notes="",
            )
            tdb.append_edge(
                edge_type="supersedes",
                from_id=n2,
                to_id=n3,
                scope="project",
                visibility="project",
                source_event_ids=[ev],
                notes="update decision",
            )
            v3 = tdb.load_view(scope="project")
            self.assertEqual(v3.node_status(n2), "superseded")


if __name__ == "__main__":
    unittest.main()
