from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.core.storage import now_rfc3339
from mi.runtime.autopilot.services import (
    find_testless_strategy_claim,
    parse_testless_strategy_from_claim_text,
    mk_testless_strategy_claim_text,
    upsert_testless_strategy_claim,
)
from mi.thoughtdb import ThoughtDbStore


class TestTestlessStrategyService(unittest.TestCase):
    def test_claim_text_roundtrip(self) -> None:
        text = mk_testless_strategy_claim_text("Run smoke + manual checks")
        self.assertIn("verification strategy", text)
        parsed = parse_testless_strategy_from_claim_text(text)
        self.assertEqual(parsed, "Run smoke + manual checks")

    def test_upsert_and_find_claim(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            cid = upsert_testless_strategy_claim(
                tdb=tdb,
                project_id=pp.project_id,
                strategy_text="Run smoke + diff review",
                source_event_id="ev_test_tls_1",
                source="unit_test",
                rationale="seed",
            )
            self.assertTrue(cid)

            found = find_testless_strategy_claim(tdb=tdb, as_of_ts=now_rfc3339())
            self.assertIsNotNone(found)
            assert isinstance(found, dict)
            self.assertEqual(str(found.get("claim_id") or ""), cid)


if __name__ == "__main__":
    unittest.main()
