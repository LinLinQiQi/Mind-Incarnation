from __future__ import annotations

import unittest

from mi.runtime.autopilot.batch_effects import append_evidence_window, segment_add_and_persist
from mi.runtime.autopilot.batch_phases import build_risk_fallback, extract_evidence_counts, should_prompt_risk_user
from mi.runtime.autopilot.batch_pipeline import compose_auto_answer_log, compose_check_plan_log, join_hands_inputs, PreactionDecision


class BatchPhaseHelpersTests(unittest.TestCase):
    def test_extract_evidence_counts_handles_missing_and_lists(self) -> None:
        counts = extract_evidence_counts({"facts": [1, 2], "actions": ["a"], "results": [], "unknowns": ["u"], "risk_signals": ["x", "y", "z"]})
        self.assertEqual(counts["facts"], 2)
        self.assertEqual(counts["actions"], 1)
        self.assertEqual(counts["results"], 0)
        self.assertEqual(counts["unknowns"], 1)
        self.assertEqual(counts["risk_signals"], 3)

        self.assertEqual(extract_evidence_counts(None)["facts"], 0)

    def test_build_risk_fallback_uses_signal_prefix_and_state(self) -> None:
        out = build_risk_fallback(["delete: rm -rf"], state="error")
        self.assertEqual(out["category"], "delete")
        self.assertEqual(out["severity"], "critical")
        self.assertTrue(out["should_ask_user"])
        self.assertIn("mind_error", out["mitigation"][0])

    def test_should_prompt_risk_user_obeys_policy(self) -> None:
        risk = {"category": "network", "severity": "high", "should_ask_user": True}
        cfg = {
            "ask_user_on_high_risk": True,
            "ask_user_risk_severities": ["high", "critical"],
            "ask_user_risk_categories": ["network"],
            "ask_user_respect_should_ask_user": True,
        }
        self.assertTrue(should_prompt_risk_user(risk_obj=risk, violation_response_cfg=cfg))

        risk2 = {"category": "network", "severity": "high", "should_ask_user": False}
        self.assertFalse(should_prompt_risk_user(risk_obj=risk2, violation_response_cfg=cfg))

    def test_append_evidence_window_keeps_recent_entries(self) -> None:
        items: list[dict[str, object]] = []
        for i in range(12):
            append_evidence_window(items, {"idx": i}, limit=8)
        self.assertEqual(len(items), 8)
        self.assertEqual(items[0].get("idx"), 4)
        self.assertEqual(items[-1].get("idx"), 11)

    def test_segment_add_and_persist_invokes_both_callbacks(self) -> None:
        seen: list[dict[str, object]] = []
        called = {"persist": 0}

        def _add(x: dict[str, object]) -> None:
            seen.append(x)

        def _persist() -> None:
            called["persist"] += 1

        segment_add_and_persist(segment_add=_add, persist_segment_state=_persist, item={"kind": "evidence"})
        self.assertEqual(len(seen), 1)
        self.assertEqual(called["persist"], 1)

    def test_batch_pipeline_log_helpers(self) -> None:
        ck = compose_check_plan_log({"should_run_checks": True, "needs_testless_strategy": False})
        self.assertIn("should_run_checks=True", ck)
        self.assertIn("needs_testless_strategy=False", ck)

        aa = compose_auto_answer_log(
            state="ok",
            auto_answer_obj={"should_answer": True, "needs_user_input": False, "confidence": 0.91},
        )
        self.assertIn("state=ok", aa)
        self.assertIn("should_answer=True", aa)
        self.assertIn("confidence=0.91", aa)

    def test_join_hands_inputs_and_preact_decision_shape(self) -> None:
        self.assertEqual(join_hands_inputs("a", "", "b"), "a\n\nb")
        d = PreactionDecision(final_continue=None, checks_obj={}, auto_answer_obj={}, repo_obs={"x": 1}, hands_last="h")
        self.assertIsNone(d.final_continue)
        self.assertEqual(d.hands_last, "h")


if __name__ == "__main__":
    unittest.main()
