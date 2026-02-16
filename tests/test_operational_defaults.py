import tempfile
import unittest
from pathlib import Path

from mi.global_ledger import append_global_event
from mi.mindspec_runtime import sanitize_mindspec_base_for_runtime
from mi.operational_defaults import (
    DEFAULTS_EVENT_KIND,
    ensure_operational_defaults_claims_current,
    resolve_operational_defaults,
    ask_when_uncertain_claim_text,
    refactor_intent_claim_text,
)
from mi.paths import GlobalPaths, ProjectPaths
from mi.storage import iter_jsonl, now_rfc3339
from mi.thoughtdb import ThoughtDbStore
from mi.pins import ASK_WHEN_UNCERTAIN_TAG, REFACTOR_INTENT_TAG


class TestOperationalDefaults(unittest.TestCase):
    def test_resolve_operational_defaults_prefers_project_over_global(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            # Global defaults.
            g_ask = tdb.append_claim_create(
                claim_type="preference",
                text=ask_when_uncertain_claim_text(True),
                scope="global",
                visibility="global",
                valid_from=None,
                valid_to=None,
                tags=[ASK_WHEN_UNCERTAIN_TAG],
                source_event_ids=["ev_test_g1"],
                confidence=1.0,
                notes="t",
            )
            g_ref = tdb.append_claim_create(
                claim_type="preference",
                text=refactor_intent_claim_text("behavior_preserving"),
                scope="global",
                visibility="global",
                valid_from=None,
                valid_to=None,
                tags=[REFACTOR_INTENT_TAG],
                source_event_ids=["ev_test_g2"],
                confidence=1.0,
                notes="t",
            )

            # Project override for ask_when_uncertain.
            p_ask = tdb.append_claim_create(
                claim_type="preference",
                text=ask_when_uncertain_claim_text(False),
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[ASK_WHEN_UNCERTAIN_TAG],
                source_event_ids=["ev_test_p1"],
                confidence=1.0,
                notes="t",
            )

            op = resolve_operational_defaults(tdb=tdb, mindspec_base={}, as_of_ts=now_rfc3339())
            self.assertFalse(op.ask_when_uncertain)
            self.assertEqual(op.ask_when_uncertain_source.get("scope"), "project")
            self.assertEqual(op.ask_when_uncertain_source.get("claim_id"), p_ask)

            self.assertEqual(op.refactor_intent, "behavior_preserving")
            self.assertEqual(op.refactor_intent_source.get("scope"), "global")
            self.assertEqual(op.refactor_intent_source.get("claim_id"), g_ref)
            self.assertTrue(bool(g_ask))

    def test_ensure_defaults_claims_reuses_last_event_id(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)

            desired = {"refactor_intent": "behavior_preserving", "ask_when_uncertain": True}
            rec = append_global_event(home_dir=home, kind=DEFAULTS_EVENT_KIND, payload={"defaults": desired, "notes": "t"})
            ev_id = str(rec.get("event_id") or "").strip()
            self.assertTrue(ev_id.startswith("ev_"))

            base = {"defaults": {"refactor_intent": "behavior_preserving", "ask_when_uncertain": True}}
            out = ensure_operational_defaults_claims_current(home_dir=home, tdb=tdb, mindspec_base=base, mode="sync")
            self.assertTrue(bool(out.get("ok", False)))
            self.assertTrue(bool(out.get("changed", False)))
            self.assertEqual(str(out.get("event_id") or "").strip(), ev_id)

            # No duplicate mi_defaults_set events should be appended.
            gp = GlobalPaths(home_dir=home)
            rows = [x for x in iter_jsonl(gp.global_evidence_log_path) if isinstance(x, dict) and x.get("kind") == DEFAULTS_EVENT_KIND]
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0].get("event_id") or "").strip(), ev_id)

            # Claims exist in global Thought DB and cite the reused event_id.
            v = tdb.load_view(scope="global")
            tagged = []
            for c in v.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=now_rfc3339()):
                if not isinstance(c, dict):
                    continue
                tags = set(str(x).strip() for x in (c.get("tags") or []) if str(x).strip())
                if ASK_WHEN_UNCERTAIN_TAG in tags or REFACTOR_INTENT_TAG in tags:
                    tagged.append(c)
            self.assertEqual(len(tagged), 2)
            for c in tagged:
                refs = c.get("source_refs") if isinstance(c.get("source_refs"), list) else []
                evs = [str(r.get("event_id") or "") for r in refs if isinstance(r, dict)]
                self.assertIn(ev_id, evs)

            # Second call should be a no-op (idempotent).
            out2 = ensure_operational_defaults_claims_current(home_dir=home, tdb=tdb, mindspec_base=base, mode="sync")
            self.assertTrue(bool(out2.get("ok", False)))
            self.assertFalse(bool(out2.get("changed", True)))

    def test_sanitize_runtime_clears_defaults(self) -> None:
        base = {"values_text": "x", "values_summary": ["y"], "defaults": {"refactor_intent": "behavior_preserving", "ask_when_uncertain": True}}
        out = sanitize_mindspec_base_for_runtime(base)
        self.assertEqual(out.get("values_text"), "")
        self.assertEqual(out.get("values_summary"), [])
        self.assertEqual(out.get("defaults"), {})


if __name__ == "__main__":
    unittest.main()

