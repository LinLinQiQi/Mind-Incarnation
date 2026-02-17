from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.core.storage import now_rfc3339
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.context import build_decide_next_thoughtdb_context


class TestThoughtDbContextRetrieval(unittest.TestCase):
    def test_one_hop_expansion_includes_neighbor_claim(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            nid = tdb.append_node_create(
                node_type="decision",
                title="Decision: foo",
                text="We decided on the foo approach.",
                scope="project",
                visibility="project",
                tags=["test"],
                source_event_ids=["ev_test_1"],
                confidence=1.0,
                notes="t",
            )
            cid = tdb.append_claim_create(
                claim_type="fact",
                text="This claim text does not contain the keyword.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["test"],
                source_event_ids=["ev_test_2"],
                confidence=1.0,
                notes="t",
            )
            tdb.append_edge(
                edge_type="depends_on",
                from_id=nid,
                to_id=cid,
                scope="project",
                visibility="project",
                source_event_ids=["ev_test_edge_1"],
                notes="decision depends on claim",
            )

            # The query mentions "foo" (matches the node), but NOT the claim text.
            ctx = build_decide_next_thoughtdb_context(
                tdb=tdb,
                as_of_ts=now_rfc3339(),
                task="foo",
                hands_last_message="",
                recent_evidence=[],
            )
            q_ids = {str(x.get("claim_id") or "") for x in (ctx.query_claims or []) if isinstance(x, dict)}
            self.assertIn(cid, q_ids)

    def test_one_hop_expansion_respects_valid_from(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            nid = tdb.append_node_create(
                node_type="decision",
                title="Decision: foo",
                text="We decided on the foo approach.",
                scope="project",
                visibility="project",
                tags=["test"],
                source_event_ids=["ev_test_1"],
                confidence=1.0,
                notes="t",
            )
            cid = tdb.append_claim_create(
                claim_type="fact",
                text="This claim should not be active yet.",
                scope="project",
                visibility="project",
                valid_from="2999-01-01T00:00:00Z",
                valid_to=None,
                tags=["test"],
                source_event_ids=["ev_test_2"],
                confidence=1.0,
                notes="t",
            )
            tdb.append_edge(
                edge_type="depends_on",
                from_id=nid,
                to_id=cid,
                scope="project",
                visibility="project",
                source_event_ids=["ev_test_edge_1"],
                notes="decision depends on claim",
            )

            ctx = build_decide_next_thoughtdb_context(
                tdb=tdb,
                as_of_ts=now_rfc3339(),
                task="foo",
                hands_last_message="",
                recent_evidence=[],
            )
            q_ids = {str(x.get("claim_id") or "") for x in (ctx.query_claims or []) if isinstance(x, dict)}
            self.assertNotIn(cid, q_ids)


if __name__ == "__main__":
    unittest.main()

