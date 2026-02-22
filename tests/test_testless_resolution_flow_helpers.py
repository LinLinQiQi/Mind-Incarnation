from __future__ import annotations

import unittest

from mi.runtime.autopilot.testless_strategy_flow import MiTestlessResolutionDeps, resolve_tls_for_checks


class TestlessResolutionFlowHelpersTests(unittest.TestCase):
    def test_returns_block_when_user_does_not_answer(self) -> None:
        evidence_window: list[dict[str, object]] = []
        sync_calls = {"n": 0}

        def _sync(_ts: str):
            sync_calls["n"] += 1
            return "", "", False

        checks, block = resolve_tls_for_checks(
            checks_obj={"needs_testless_strategy": True, "testless_strategy_question": "q"},
            hands_last_message="h",
            repo_observation={},
            user_input_batch_id="b0.ui",
            batch_id_after_testless="b0.after_testless",
            batch_id_after_tls_claim="b0.after_tls_claim",
            tag_after_testless="t1",
            tag_after_tls_claim="t2",
            notes_prefix="loop_break",
            source="user_input:testless_strategy",
            rationale="r",
            evidence_window=evidence_window,
            deps=MiTestlessResolutionDeps(
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id="t_1",
                read_user_answer=lambda _q: "",
                evidence_append=lambda _rec: {"event_id": "ev_1"},
                segment_add=lambda _item: None,
                persist_segment_state=lambda: None,
                sync_tls_overlay=_sync,
                canonicalize_tls=lambda **_kwargs: "cl_1",
                build_thought_db_context_obj=lambda _m, _e: {},
                plan_checks_and_record=lambda **_kwargs: ({}, "", "ok"),
                plan_checks_and_record2=lambda **_kwargs: ({}, "", "ok"),
                empty_check_plan=lambda: {},
            ),
        )
        self.assertEqual(block, "user did not provide required input")
        self.assertTrue(bool(checks.get("needs_testless_strategy")))
        self.assertEqual(sync_calls["n"], 1)

    def test_user_answer_canonicalizes_and_replans(self) -> None:
        evidence_window: list[dict[str, object]] = []
        persisted = {"n": 0}
        segment_items: list[dict[str, object]] = []
        canonicalized = {"n": 0}
        sync_calls = {"n": 0}

        def _sync(_ts: str):
            sync_calls["n"] += 1
            if sync_calls["n"] == 1:
                return "", "", False
            return "Run smoke + manual checks", "cl_tls_1", True

        checks, block = resolve_tls_for_checks(
            checks_obj={"needs_testless_strategy": True, "testless_strategy_question": "q"},
            hands_last_message="h",
            repo_observation={},
            user_input_batch_id="b0.ui",
            batch_id_after_testless="b0.after_testless",
            batch_id_after_tls_claim="b0.after_tls_claim",
            tag_after_testless="t1",
            tag_after_tls_claim="t2",
            notes_prefix="loop_break",
            source="user_input:testless_strategy",
            rationale="r",
            evidence_window=evidence_window,
            deps=MiTestlessResolutionDeps(
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id="t_1",
                read_user_answer=lambda _q: "Run smoke + manual checks",
                evidence_append=lambda rec: {"event_id": "ev_1", **rec},
                segment_add=lambda item: segment_items.append(dict(item)),
                persist_segment_state=lambda: persisted.__setitem__("n", persisted["n"] + 1),
                sync_tls_overlay=_sync,
                canonicalize_tls=lambda **_kwargs: canonicalized.__setitem__("n", canonicalized["n"] + 1) or "cl_tls_1",
                build_thought_db_context_obj=lambda _m, _e: {"claims": []},
                plan_checks_and_record=lambda **_kwargs: (
                    {
                        "should_run_checks": True,
                        "hands_check_input": "python -m compileall -q .",
                        "needs_testless_strategy": False,
                        "testless_strategy_question": "",
                        "notes": "replanned",
                    },
                    "mind_checks_1",
                    "ok",
                ),
                plan_checks_and_record2=lambda **_kwargs: ({}, "", "ok"),
                empty_check_plan=lambda: {},
            ),
        )
        self.assertEqual(block, "")
        self.assertFalse(bool(checks.get("needs_testless_strategy")))
        self.assertEqual(canonicalized["n"], 1)
        self.assertEqual(persisted["n"], 1)
        self.assertEqual(len(segment_items), 1)
        self.assertEqual(sync_calls["n"], 2)

    def test_tls_claim_replan_fallback_clears_needs_flag(self) -> None:
        evidence_window: list[dict[str, object]] = []

        def _plan2(**kwargs):
            post = kwargs.get("postprocess")
            base = {
                "should_run_checks": False,
                "hands_check_input": "",
                "needs_testless_strategy": True,
                "testless_strategy_question": "q",
                "notes": "need tls",
            }
            if callable(post):
                return post(base, "skipped"), "", "skipped"
            return base, "", "skipped"

        checks, block = resolve_tls_for_checks(
            checks_obj={
                "should_run_checks": False,
                "hands_check_input": "",
                "needs_testless_strategy": True,
                "testless_strategy_question": "q",
                "notes": "need tls",
            },
            hands_last_message="h",
            repo_observation={},
            user_input_batch_id="b0.ui",
            batch_id_after_testless="b0.after_testless",
            batch_id_after_tls_claim="b0.after_tls_claim",
            tag_after_testless="t1",
            tag_after_tls_claim="t2",
            notes_prefix="loop_break",
            source="user_input:testless_strategy",
            rationale="r",
            evidence_window=evidence_window,
            deps=MiTestlessResolutionDeps(
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id="t_1",
                read_user_answer=lambda _q: "unused",
                evidence_append=lambda _rec: {"event_id": "ev_1"},
                segment_add=lambda _item: None,
                persist_segment_state=lambda: None,
                sync_tls_overlay=lambda _ts: ("Run smoke + manual checks", "cl_tls_1", True),
                canonicalize_tls=lambda **_kwargs: "cl_tls_1",
                build_thought_db_context_obj=lambda _m, _e: {"claims": []},
                plan_checks_and_record=lambda **_kwargs: ({}, "", "ok"),
                plan_checks_and_record2=_plan2,
                empty_check_plan=lambda: {},
            ),
        )
        self.assertEqual(block, "")
        self.assertFalse(bool(checks.get("needs_testless_strategy")))
        self.assertIn("after_tls_claim", str(checks.get("notes") or ""))


if __name__ == "__main__":
    unittest.main()
