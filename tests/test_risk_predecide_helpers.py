from __future__ import annotations

import unittest

from mi.runtime.autopilot.risk_predecide import (
    RiskPredecideDeps,
    maybe_prompt_risk_continue,
    query_risk_judge,
    run_risk_predecide,
)


class RiskPredecideHelpersTests(unittest.TestCase):
    def test_query_risk_judge_falls_back_when_mind_missing(self) -> None:
        calls = {"recall": 0}

        def _recall(**kwargs):
            calls["recall"] += 1

        out, ref = query_risk_judge(
            batch_idx=1,
            batch_id="b1",
            risk_signals=["network: curl"],
            hands_last="done",
            tdb_ctx_batch_obj={},
            task="ship",
            hands_provider="codex",
            runtime_cfg={},
            project_overlay={},
            maybe_cross_project_recall=_recall,
            risk_judge_prompt_builder=lambda **kwargs: "prompt",
            mind_call=lambda **kwargs: (None, "", "error"),
            build_risk_fallback=lambda sig, state: {"category": "network", "severity": "high", "state": state, "signals": sig},
        )
        self.assertEqual(calls["recall"], 1)
        self.assertEqual(out["category"], "network")
        self.assertEqual(out["state"], "error")
        self.assertEqual(ref, "")

    def test_maybe_prompt_risk_continue_policy_and_answer(self) -> None:
        # policy says do not ask
        out = maybe_prompt_risk_continue(
            risk_obj={"category": "network", "severity": "high"},
            should_prompt_risk_user=lambda **kwargs: False,
            violation_response_cfg={},
            read_user_answer=lambda q: "n",
        )
        self.assertIsNone(out)

        # asked and user blocks
        out2 = maybe_prompt_risk_continue(
            risk_obj={"category": "network", "severity": "high", "mitigation": ["m1"]},
            should_prompt_risk_user=lambda **kwargs: True,
            violation_response_cfg={},
            read_user_answer=lambda q: "n",
        )
        self.assertFalse(out2)

    def test_run_risk_predecide_orchestrates_steps(self) -> None:
        seen = {"applied": "", "eid": ""}

        def _query(**kwargs):
            return {"category": "network", "severity": "high"}, "mind_ref"

        def _record(**kwargs):
            return {"event_id": "ev_1"}

        def _apply(**kwargs):
            seen["applied"] = str(kwargs.get("risk_mind_ref") or "")
            seen["eid"] = str(kwargs.get("risk_event_id") or "")

        out = run_risk_predecide(
            batch_idx=2,
            batch_id="b2",
            risk_signals=["network:x"],
            hands_last="h",
            tdb_ctx_batch_obj={},
            deps=RiskPredecideDeps(
                query_risk=_query,
                record_risk=_record,
                apply_learn_suggested=_apply,
                maybe_prompt_continue=lambda **kwargs: None,
            ),
        )
        self.assertIsNone(out)
        self.assertEqual(seen["applied"], "mind_ref")
        self.assertEqual(seen["eid"], "ev_1")


if __name__ == "__main__":
    unittest.main()
