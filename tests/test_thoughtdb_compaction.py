from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.compaction import compact_thoughtdb_dir


class TestThoughtDbCompaction(unittest.TestCase):
    def test_compaction_preserves_effective_view(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)

            c1 = tdb.append_claim_create(
                claim_type="preference",
                text="Prefer behavior-preserving refactors by default.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["t"],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )
            c2 = tdb.append_claim_create(
                claim_type="preference",
                text="Stop and ask when there are no tests.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["t"],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )

            # Duplicate edge records (same key) to ensure compaction drops earlier duplicates.
            tdb.append_edge(
                edge_type="depends_on",
                from_id=c2,
                to_id=c1,
                scope="project",
                visibility="project",
                source_event_ids=[],
                notes="first",
            )
            tdb.append_edge(
                edge_type="depends_on",
                from_id=c2,
                to_id=c1,
                scope="project",
                visibility="project",
                source_event_ids=[],
                notes="second",
            )

            # Duplicate retracts; compaction should keep only the last per id.
            tdb.append_claim_retract(claim_id=c1, scope="project", rationale="no longer applies", source_event_ids=[])
            tdb.append_claim_retract(claim_id=c1, scope="project", rationale="still no longer applies", source_event_ids=[])

            _n1 = tdb.append_node_create(
                node_type="summary",
                title="Test summary",
                text="Summary node for compaction test.",
                scope="project",
                visibility="project",
                tags=["t"],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )

            snap = pp.thoughtdb_dir / "view.snapshot.json"
            v_before = tdb.load_view(scope="project")
            before = {
                "claims_by_id": dict(v_before.claims_by_id),
                "nodes_by_id": dict(v_before.nodes_by_id),
                "redirects": dict(v_before.redirects_same_as),
                "superseded": set(v_before.superseded_ids),
                "retracted": set(v_before.retracted_ids),
                "retracted_nodes": set(v_before.retracted_node_ids),
                "edge_keys": {
                    (str(e.get("edge_type") or ""), str(e.get("from_id") or ""), str(e.get("to_id") or ""))
                    for e in v_before.edges
                    if isinstance(e, dict)
                },
            }
            self.assertTrue(snap.exists())

            # Dry-run must not create archive directories or modify files.
            res_dry = compact_thoughtdb_dir(thoughtdb_dir=pp.thoughtdb_dir, snapshot_path=snap, dry_run=True)
            self.assertTrue(res_dry.get("ok"))
            archive_dir = Path(str(res_dry.get("archive_dir") or ""))
            self.assertFalse(archive_dir.exists())
            self.assertTrue(snap.exists())

            # Apply compaction.
            res = compact_thoughtdb_dir(thoughtdb_dir=pp.thoughtdb_dir, snapshot_path=snap, dry_run=False)
            self.assertTrue(res.get("ok"))
            archive_dir2 = Path(str(res.get("archive_dir") or ""))
            self.assertTrue((archive_dir2 / "claims.jsonl.gz").exists())
            self.assertTrue((archive_dir2 / "edges.jsonl.gz").exists())
            self.assertTrue((archive_dir2 / "nodes.jsonl.gz").exists())

            # Loading the view after compaction should preserve effective semantics.
            tdb2 = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            v_after = tdb2.load_view(scope="project")
            after = {
                "claims_by_id": dict(v_after.claims_by_id),
                "nodes_by_id": dict(v_after.nodes_by_id),
                "redirects": dict(v_after.redirects_same_as),
                "superseded": set(v_after.superseded_ids),
                "retracted": set(v_after.retracted_ids),
                "retracted_nodes": set(v_after.retracted_node_ids),
                "edge_keys": {
                    (str(e.get("edge_type") or ""), str(e.get("from_id") or ""), str(e.get("to_id") or ""))
                    for e in v_after.edges
                    if isinstance(e, dict)
                },
            }
            self.assertEqual(before["claims_by_id"], after["claims_by_id"])
            self.assertEqual(before["nodes_by_id"], after["nodes_by_id"])
            self.assertEqual(before["redirects"], after["redirects"])
            self.assertEqual(before["superseded"], after["superseded"])
            self.assertEqual(before["retracted"], after["retracted"])
            self.assertEqual(before["retracted_nodes"], after["retracted_nodes"])
            self.assertEqual(before["edge_keys"], after["edge_keys"])

            # Edge JSONL should now be deduped (output lines should be <= input lines).
            files = res.get("files") if isinstance(res.get("files"), dict) else {}
            edges = files.get("edges") if isinstance(files.get("edges"), dict) else {}
            cs = edges.get("compact_stats") if isinstance(edges.get("compact_stats"), dict) else {}
            self.assertLessEqual(int(cs.get("output_lines") or 0), int(cs.get("input_lines") or 0))


if __name__ == "__main__":
    unittest.main()
