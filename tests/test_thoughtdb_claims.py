from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.memory.service import MemoryService
from mi.thoughtdb import ThoughtDbStore


class TestThoughtDbClaims(unittest.TestCase):
    def test_apply_mined_output_writes_and_indexes_active_claims(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            applied = tdb.apply_mined_output(
                output={
                    "claims": [
                        {
                            "local_id": "c1",
                            "claim_type": "fact",
                            "text": "alpha beta is supported",
                            "scope": "project",
                            "visibility": "project",
                            "valid_from": None,
                            "valid_to": None,
                            "confidence": 0.95,
                            "source_event_ids": ["ev_test_1"],
                            "tags": ["t1"],
                            "notes": "n",
                        }
                    ],
                    "edges": [],
                    "notes": "",
                },
                allowed_event_ids={"ev_test_1"},
                min_confidence=0.9,
                max_claims=6,
            )
            written = applied.get("written") if isinstance(applied, dict) else []
            self.assertTrue(isinstance(written, list) and len(written) == 1)
            cid = str(written[0].get("claim_id") or "")
            self.assertTrue(cid.startswith("cl_"))

            v = tdb.load_view(scope="project")
            self.assertIn(cid, v.claims_by_id)
            self.assertEqual(v.claim_status(cid), "active")

            mem = MemoryService(home)
            mem.ingest_structured()
            hits = mem.search(query="alpha beta", top_k=5, kinds={"claim"}, include_global=True, exclude_project_id="")
            self.assertTrue(any(h.kind == "claim" and h.project_id == pp.project_id for h in hits))

    def test_apply_mined_output_writes_edges(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            out = {
                "claims": [
                    {
                        "local_id": "c1",
                        "claim_type": "goal",
                        "text": "Ship v1 with minimal user burden",
                        "scope": "project",
                        "visibility": "project",
                        "valid_from": None,
                        "valid_to": None,
                        "confidence": 0.95,
                        "source_event_ids": ["ev_test_a"],
                        "tags": [],
                        "notes": "",
                    },
                    {
                        "local_id": "c2",
                        "claim_type": "preference",
                        "text": "Avoid asking the user repeatedly during refactors",
                        "scope": "project",
                        "visibility": "project",
                        "valid_from": None,
                        "valid_to": None,
                        "confidence": 0.95,
                        "source_event_ids": ["ev_test_b"],
                        "tags": [],
                        "notes": "",
                    },
                ],
                "edges": [
                    {
                        "edge_type": "depends_on",
                        "from_claim_id": "c1",
                        "to_claim_id": "c2",
                        "confidence": 0.95,
                        "source_event_ids": ["ev_test_b"],
                        "notes": "",
                    }
                ],
                "notes": "ok",
            }

            applied = tdb.apply_mined_output(
                output=out,
                allowed_event_ids={"ev_test_a", "ev_test_b"},
                min_confidence=0.9,
                max_claims=6,
            )
            self.assertTrue(isinstance(applied, dict))
            self.assertEqual(len(applied.get("written_edges") or []), 1)

            mapping = {}
            for it in applied.get("written") or []:
                if isinstance(it, dict) and it.get("local_id") and it.get("claim_id"):
                    mapping[str(it["local_id"])] = str(it["claim_id"])
            self.assertIn("c1", mapping)
            self.assertIn("c2", mapping)

            v = tdb.load_view(scope="project")
            # There should be at least one depends_on edge linking the two new claims.
            edges = [e for e in v.edges if isinstance(e, dict) and e.get("edge_type") == "depends_on"]
            self.assertTrue(bool(edges))
            self.assertTrue(any(str(e.get("from_id") or "") == mapping["c1"] and str(e.get("to_id") or "") == mapping["c2"] for e in edges))

    def test_supersedes_and_same_as_affect_view_status(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            a = tdb.append_claim_create(
                claim_type="fact",
                text="alpha beta is supported",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=["ev_test_2"],
                confidence=1.0,
                notes="",
            )
            b = tdb.append_claim_create(
                claim_type="fact",
                text="alpha beta is supported (v2)",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=["ev_test_3"],
                confidence=1.0,
                notes="",
            )
            tdb.append_edge(
                edge_type="supersedes",
                from_id=a,
                to_id=b,
                scope="project",
                visibility="project",
                source_event_ids=["ev_test_3"],
                notes="",
            )

            v1 = tdb.load_view(scope="project")
            self.assertEqual(v1.claim_status(a), "superseded")
            self.assertEqual(v1.claim_status(b), "active")

            dup = tdb.append_claim_create(
                claim_type="fact",
                text="gamma delta is supported",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=["ev_test_4"],
                confidence=1.0,
                notes="",
            )
            canon = tdb.append_claim_create(
                claim_type="fact",
                text="gamma delta is supported (canonical)",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=["ev_test_5"],
                confidence=1.0,
                notes="",
            )
            tdb.append_edge(
                edge_type="same_as",
                from_id=dup,
                to_id=canon,
                scope="project",
                visibility="project",
                source_event_ids=["ev_test_5"],
                notes="",
            )

            v2 = tdb.load_view(scope="project")
            self.assertEqual(v2.resolve_id(dup), canon)
            # Alias is hidden by default when iterating canonical claims.
            ids = {str(c.get("claim_id") or "") for c in v2.iter_claims(include_inactive=True, include_aliases=False)}
            self.assertNotIn(dup, ids)


if __name__ == "__main__":
    unittest.main()
