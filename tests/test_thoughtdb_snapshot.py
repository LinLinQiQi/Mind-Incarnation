from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mi.core.paths import ProjectPaths
from mi.thoughtdb import ThoughtDbStore


class TestThoughtDbSnapshot(unittest.TestCase):
    def test_load_view_uses_snapshot_when_metas_match(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            tdb = ThoughtDbStore(home_dir=Path(home), project_paths=pp)

            tdb.append_claim_create(
                claim_type="preference",
                text="Prefer behavior-preserving refactors by default.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["test"],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )

            # First load builds the view and writes a persisted snapshot.
            _v1 = tdb.load_view(scope="project")
            snap = pp.thoughtdb_dir / "view.snapshot.json"
            self.assertTrue(snap.exists())

            # New store instance should load from the snapshot without touching JSONL readers.
            tdb2 = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            with mock.patch("mi.thoughtdb.store.iter_jsonl", side_effect=AssertionError("iter_jsonl should not be called")):
                v2 = tdb2.load_view(scope="project")
            self.assertTrue(v2.claims_by_id)

            # Snapshot must be invalidated when source metas change (append).
            tdb.append_claim_create(
                claim_type="preference",
                text="Stop and ask when there are no tests.",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=["test"],
                source_event_ids=[],
                confidence=1.0,
                notes="",
            )
            tdb3 = ThoughtDbStore(home_dir=Path(home), project_paths=pp)
            with self.assertRaises(AssertionError):
                with mock.patch("mi.thoughtdb.store.iter_jsonl", side_effect=AssertionError("iter_jsonl should be called")):
                    _ = tdb3.load_view(scope="project")


if __name__ == "__main__":
    unittest.main()

