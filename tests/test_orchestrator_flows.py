from __future__ import annotations

import unittest
from pathlib import Path

from mi.runtime.autopilot import (
    DecidePhaseDeps,
    PlanChecksAutoAnswerDeps,
    WorkflowRiskPhaseDeps,
    run_decide_next_phase,
    run_plan_checks_and_auto_answer,
    run_workflow_and_risk_phase,
    BatchExecutionContext,
    RunState,
)


class OrchestratorFlowsTests(unittest.TestCase):
    def test_decide_flow_routes_missing_and_normal_paths(self) -> None:
        called = {"missing": 0, "record": 0, "route": 0}

        def _query_missing(**_kwargs):
            return None, "", "skipped", {"ctx": 1}, {"sum": 1}

        def _missing(**_kwargs):
            called["missing"] += 1
            return True

        def _record(**_kwargs):
            called["record"] += 1
            return "stop", None

        def _route(**_kwargs):
            called["route"] += 1
            return False

        out1 = run_decide_next_phase(
            batch_idx=1,
            batch_id="b1",
            hands_last="x",
            repo_obs={},
            checks_obj={},
            auto_answer_obj={},
            deps=DecidePhaseDeps(query=_query_missing, handle_missing=_missing, record_effects=_record, route_action=_route),
        )
        self.assertTrue(out1)
        self.assertEqual(called["missing"], 1)
        self.assertEqual(called["record"], 0)
        self.assertEqual(called["route"], 0)

        def _query_ok(**_kwargs):
            return {"next_action": "stop"}, "ref", "ok", {"ctx": 2}, {"sum": 2}

        out2 = run_decide_next_phase(
            batch_idx=2,
            batch_id="b2",
            hands_last="y",
            repo_obs={},
            checks_obj={},
            auto_answer_obj={},
            deps=DecidePhaseDeps(query=_query_ok, handle_missing=_missing, record_effects=_record, route_action=_route),
        )
        self.assertFalse(out2)
        self.assertEqual(called["record"], 1)
        self.assertEqual(called["route"], 1)

    def test_checks_flow_calls_plan_then_autoanswer(self) -> None:
        order: list[str] = []

        def _plan(**_kwargs):
            order.append("plan")
            return {"should_run_checks": True}

        def _auto(**kwargs):
            order.append("auto")
            self.assertTrue(kwargs.get("checks_obj", {}).get("should_run_checks"))
            return {"should_answer": False}

        checks_obj, auto_obj = run_plan_checks_and_auto_answer(
            batch_idx=0,
            batch_id="b0",
            summary={},
            evidence_obj={},
            repo_obs={},
            hands_last="q?",
            tdb_ctx_batch_obj={},
            deps=PlanChecksAutoAnswerDeps(plan_checks=_plan, maybe_auto_answer=_auto),
        )
        self.assertEqual(order, ["plan", "auto"])
        self.assertIn("should_run_checks", checks_obj)
        self.assertIn("should_answer", auto_obj)

    def test_risk_flow_skips_or_judges_based_on_signals(self) -> None:
        called = {"apply": 0, "detect": 0, "judge": 0}
        ctx = BatchExecutionContext(
            batch_idx=0,
            batch_id="b0",
            batch_ts="t",
            hands_transcript=Path("dummy.jsonl"),
            batch_input="i",
            hands_prompt="p",
            light_injection="l",
            sent_ts="s",
            prompt_sha256="h",
            use_resume=False,
            attempted_overlay_resume=False,
        )

        def _apply(**_kwargs):
            called["apply"] += 1

        def _detect_none(**_kwargs):
            called["detect"] += 1
            return []

        def _judge(**_kwargs):
            called["judge"] += 1
            return False

        out1 = run_workflow_and_risk_phase(
            batch_idx=0,
            batch_id="b0",
            result=object(),
            summary={},
            evidence_obj={},
            repo_obs={},
            hands_last="x",
            tdb_ctx_batch_obj={},
            ctx=ctx,
            deps=WorkflowRiskPhaseDeps(
                apply_workflow_progress=_apply,
                detect_risk_signals=_detect_none,
                judge_and_handle_risk=_judge,
            ),
        )
        self.assertIsNone(out1)
        self.assertEqual(called["apply"], 1)
        self.assertEqual(called["detect"], 1)
        self.assertEqual(called["judge"], 0)

        def _detect_one(**_kwargs):
            called["detect"] += 1
            return ["network:curl"]

        out2 = run_workflow_and_risk_phase(
            batch_idx=1,
            batch_id="b1",
            result=object(),
            summary={},
            evidence_obj={},
            repo_obs={},
            hands_last="y",
            tdb_ctx_batch_obj={},
            ctx=ctx,
            deps=WorkflowRiskPhaseDeps(
                apply_workflow_progress=_apply,
                detect_risk_signals=_detect_one,
                judge_and_handle_risk=_judge,
            ),
        )
        self.assertFalse(out2)
        self.assertEqual(called["judge"], 1)

    def test_run_state_defaults(self) -> None:
        st = RunState()
        self.assertEqual(st.status, "not_done")
        self.assertEqual(st.notes, "")
        self.assertEqual(st.next_input, "")
        self.assertEqual(st.evidence_window, [])
        self.assertIsNone(st.last_evidence_rec)
        self.assertIsNone(st.last_decide_next_rec)


if __name__ == "__main__":
    unittest.main()
