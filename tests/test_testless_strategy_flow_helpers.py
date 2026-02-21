from __future__ import annotations

import unittest

from mi.runtime.autopilot.testless_strategy_flow import (
    TestlessStrategyFlowDeps,
    apply_set_testless_strategy_overlay_update,
    canonicalize_tls_and_update_overlay,
    sync_tls_overlay_from_thoughtdb,
)


class TestlessStrategyFlowHelpersTests(unittest.TestCase):
    def _mk_deps(
        self,
        *,
        find_claim=lambda _ts: None,
        upsert=lambda **_kwargs: "cl_tls_1",
    ):
        written: list[dict[str, object]] = []
        refreshed = {"n": 0}
        evidence: list[dict[str, object]] = []

        deps = TestlessStrategyFlowDeps(
            now_ts=lambda: "2026-02-01T00:00:00Z",
            thread_id="t_1",
            evidence_append=lambda rec: evidence.append(dict(rec)) or {"event_id": "ev_tls_1"},
            find_testless_strategy_claim=find_claim,
            parse_testless_strategy_from_claim_text=lambda text: text.replace("MI setting: testless_verification_strategy =", "").strip(),
            upsert_testless_strategy_claim=upsert,
            write_overlay=lambda obj: written.append(dict(obj)),
            refresh_overlay_refs=lambda: refreshed.__setitem__("n", refreshed["n"] + 1),
        )
        return deps, written, refreshed, evidence

    def test_sync_tls_overlay_aligns_pointer(self) -> None:
        overlay: dict[str, object] = {}
        deps, written, refreshed, _evidence = self._mk_deps(
            find_claim=lambda _ts: {
                "claim_id": "cl_tls_seed",
                "text": "MI setting: testless_verification_strategy = Run smoke + manual checks",
            }
        )

        strategy, claim_id, chosen = sync_tls_overlay_from_thoughtdb(
            overlay=overlay,
            as_of_ts="2026-02-01T00:00:00Z",
            deps=deps,
        )
        self.assertTrue(chosen)
        self.assertEqual(claim_id, "cl_tls_seed")
        self.assertEqual(strategy, "Run smoke + manual checks")
        tls = overlay.get("testless_verification_strategy")
        self.assertIsInstance(tls, dict)
        self.assertEqual((tls or {}).get("claim_id"), "cl_tls_seed")
        self.assertEqual(len(written), 1)
        self.assertEqual(refreshed["n"], 1)

    def test_canonicalize_tls_writes_fallback_event_and_overlay(self) -> None:
        overlay: dict[str, object] = {}
        deps, written, refreshed, evidence = self._mk_deps()

        cid = canonicalize_tls_and_update_overlay(
            overlay=overlay,
            strategy_text="Run smoke + manual checks",
            source_event_id="",
            fallback_batch_id="b0.testless",
            overlay_rationale="user provided",
            overlay_rationale_default="default rationale",
            claim_rationale="claim rationale",
            default_rationale="claim rationale",
            source="user_input:testless_strategy",
            deps=deps,
        )
        self.assertEqual(cid, "cl_tls_1")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].get("kind"), "testless_strategy_set")
        tls = overlay.get("testless_verification_strategy")
        self.assertEqual((tls or {}).get("claim_id"), "cl_tls_1")
        self.assertIn("canonical claim", str((tls or {}).get("rationale") or ""))
        self.assertEqual(len(written), 1)
        self.assertEqual(refreshed["n"], 1)

    def test_apply_set_testless_strategy_overlay_update_ignores_invalid(self) -> None:
        overlay: dict[str, object] = {}
        deps, written, refreshed, _evidence = self._mk_deps()

        apply_set_testless_strategy_overlay_update(
            overlay=overlay,
            set_tls={"strategy": "", "rationale": "r"},
            decide_event_id="ev_1",
            fallback_batch_id="b1",
            default_rationale="def",
            source="decide_next:set_testless_strategy",
            deps=deps,
        )
        self.assertEqual(overlay, {})
        self.assertEqual(written, [])
        self.assertEqual(refreshed["n"], 0)


if __name__ == "__main__":
    unittest.main()
