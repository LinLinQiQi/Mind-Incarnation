from __future__ import annotations

import unittest
from pathlib import Path

from mi.runtime.autopilot import (
    BatchExecutionContext,
    BatchPredecideDeps,
    ExtractEvidenceDeps,
    PlanChecksAutoAnswerDeps,
    PreactionDecision,
    PreactionPhaseDeps,
    WorkflowRiskPhaseDeps,
    run_batch_predecide,
)


class PredecideFlowTests(unittest.TestCase):
    def _ctx(self, idx: int = 0) -> BatchExecutionContext:
        return BatchExecutionContext(
            batch_idx=idx,
            batch_id=f"b{idx}",
            batch_ts="t",
            hands_transcript=Path(f"b{idx}.jsonl"),
            batch_input="in",
            hands_prompt="prompt",
            light_injection="light",
            sent_ts="ts",
            prompt_sha256="sha",
            use_resume=False,
            attempted_overlay_resume=False,
        )

    def test_predecide_short_circuits_on_risk_bool(self) -> None:
        calls = {"checks": 0, "pre": 0}

        def _extract_context(**_kwargs):
            return {"s": 1}, {"e": 1}, "hands last", {"tdb": 1}

        def _risk(**_kwargs):
            return False

        def _checks(**_kwargs):
            calls["checks"] += 1
            return {}, {}

        def _preactions(**_kwargs):
            calls["pre"] += 1
            return None, {}

        out = run_batch_predecide(
            batch_idx=0,
            deps=BatchPredecideDeps(
                build_context=lambda _idx: self._ctx(0),
                run_hands=lambda **_kwargs: object(),
                observe_repo=lambda: {"repo": 1},
                dict_or_empty=lambda o: o if isinstance(o, dict) else {},
                extract_deps=ExtractEvidenceDeps(extract_context=_extract_context),
                workflow_risk_deps=WorkflowRiskPhaseDeps(
                    apply_workflow_progress=lambda **_kwargs: None,
                    detect_risk_signals=lambda **_kwargs: ["network:x"],
                    judge_and_handle_risk=_risk,
                ),
                checks_deps=PlanChecksAutoAnswerDeps(plan_checks=_checks, maybe_auto_answer=_checks),
                preaction_deps=PreactionPhaseDeps(apply_preactions=_preactions, empty_auto_answer=lambda: {}),
            ),
        )
        self.assertEqual(out.batch_id, "b0")
        self.assertIs(out.out, False)
        self.assertEqual(calls["checks"], 0)
        self.assertEqual(calls["pre"], 0)

    def test_predecide_returns_preact_decision_when_no_short_circuit(self) -> None:
        def _extract_context(**_kwargs):
            return {"s": 1}, {"e": 1}, "hands last", {"tdb": 1}

        def _risk(**_kwargs):
            return None

        def _plan_checks(**_kwargs):
            return {"should_run_checks": True}

        def _auto_answer(**_kwargs):
            return {"should_answer": False}

        def _preactions(**_kwargs):
            return None, {"should_run_checks": True}

        out = run_batch_predecide(
            batch_idx=2,
            deps=BatchPredecideDeps(
                build_context=lambda _idx: self._ctx(2),
                run_hands=lambda **_kwargs: object(),
                observe_repo=lambda: {"repo": 1},
                dict_or_empty=lambda o: o if isinstance(o, dict) else {},
                extract_deps=ExtractEvidenceDeps(extract_context=_extract_context),
                workflow_risk_deps=WorkflowRiskPhaseDeps(
                    apply_workflow_progress=lambda **_kwargs: None,
                    detect_risk_signals=lambda **_kwargs: [],
                    judge_and_handle_risk=_risk,
                ),
                checks_deps=PlanChecksAutoAnswerDeps(plan_checks=_plan_checks, maybe_auto_answer=_auto_answer),
                preaction_deps=PreactionPhaseDeps(apply_preactions=_preactions, empty_auto_answer=lambda: {}),
            ),
        )
        self.assertEqual(out.batch_id, "b2")
        self.assertIsInstance(out.out, PreactionDecision)
        d = out.out
        assert isinstance(d, PreactionDecision)
        self.assertEqual(d.hands_last, "hands last")
        self.assertTrue(d.checks_obj.get("should_run_checks"))


if __name__ == "__main__":
    unittest.main()
