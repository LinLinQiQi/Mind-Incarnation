from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import GlobalPaths, ProjectPaths
from mi.core.storage import iter_jsonl, now_rfc3339
from mi.thoughtdb.context import build_decide_next_thoughtdb_context
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.values import VALUES_BASE_TAG, apply_values_claim_patch, existing_values_claims, write_values_set_event


class TestValuesAndThoughtContext(unittest.TestCase):
    def test_write_values_set_event_appends_global_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as td_home:
            home = Path(td_home)
            rec = write_values_set_event(
                home_dir=home,
                values_text="Prefer minimal questions; stop on no tests.",
                compiled_values={"values_summary": ["x"]},
                notes="t",
            )
            ev_id = str(rec.get("event_id") or "")
            self.assertTrue(ev_id.startswith("ev_"))
            self.assertEqual(str(rec.get("kind") or ""), "values_set")

            gp = GlobalPaths(home_dir=home)
            rows = [x for x in iter_jsonl(gp.global_evidence_log_path) if isinstance(x, dict)]
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0].get("event_id") or ""), ev_id)
            payload = rows[0].get("payload") if isinstance(rows[0].get("payload"), dict) else {}
            self.assertEqual(str(payload.get("values_text") or ""), "Prefer minimal questions; stop on no tests.")

    def test_values_claim_patch_writes_global_preference_claims_and_context_includes_them(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            applied = apply_values_claim_patch(
                tdb=tdb,
                values_event_id="ev_test_values_1",
                min_confidence=0.9,
                max_claims=12,
                patch_obj={
                    "claims": [
                        {
                            "local_id": "c1",
                            "claim_type": "preference",
                            "text": "Avoid repeated confirmation questions during refactors.",
                            "scope": "global",
                            "visibility": "global",
                            "valid_from": None,
                            "valid_to": None,
                            "confidence": 0.95,
                            "source_event_ids": [],
                            "tags": [],
                            "notes": "",
                        },
                        {
                            "local_id": "c2",
                            "claim_type": "fact",
                            "text": "This should not be imported as a values claim.",
                            "scope": "global",
                            "visibility": "global",
                            "valid_from": None,
                            "valid_to": None,
                            "confidence": 0.95,
                            "source_event_ids": ["ev_test_values_1"],
                            "tags": [],
                            "notes": "",
                        },
                    ],
                    "edges": [],
                    "retract_claim_ids": [],
                    "notes": "",
                },
            )
            self.assertTrue(applied.ok)

            # Exactly one preference/goal claim should be written (fact is filtered out).
            written = applied.applied.get("written") if isinstance(applied.applied, dict) else []
            self.assertEqual(len(written), 1)
            cid = str(written[0].get("claim_id") or "")
            self.assertTrue(cid.startswith("cl_"))

            v = tdb.load_view(scope="global")
            self.assertIn(cid, v.claims_by_id)
            c = v.claims_by_id[cid]
            self.assertEqual(str(c.get("scope") or ""), "global")
            self.assertEqual(str(c.get("visibility") or ""), "global")
            self.assertEqual(str(c.get("claim_type") or ""), "preference")
            tags = {str(x).strip() for x in (c.get("tags") or []) if str(x).strip()}
            self.assertIn(VALUES_BASE_TAG, tags)
            self.assertIn("values_set:ev_test_values_1", tags)

            # existing_values_claims returns compact active canonical values claims.
            vals = existing_values_claims(tdb=tdb, limit=20)
            ids = {str(x.get("claim_id") or "") for x in vals if isinstance(x, dict)}
            self.assertIn(cid, ids)

            # decide_next thought db context always includes values_claims.
            ctx = build_decide_next_thoughtdb_context(
                tdb=tdb,
                as_of_ts=now_rfc3339(),
                task="Do a refactor with minimal questions",
                hands_last_message="",
                recent_evidence=[],
            )
            v_ids = {str(x.get("claim_id") or "") for x in (ctx.values_claims or []) if isinstance(x, dict)}
            self.assertIn(cid, v_ids)


if __name__ == "__main__":
    unittest.main()
