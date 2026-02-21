from __future__ import annotations

import unittest

from mi.runtime.autopilot import BatchRunRequest, CheckpointRequest
from mi.runtime.autopilot.services import CheckpointService, DecideBatchService, PipelineService
from mi.runtime.autopilot.batch_pipeline import PreactionDecision


class PipelineServicesTests(unittest.TestCase):
    def test_pipeline_stops_on_boolean_predecide(self) -> None:
        decide_called = {"n": 0}

        def _predecide(_req: BatchRunRequest):
            return False

        def _decide(_req: BatchRunRequest, _preaction: PreactionDecision) -> bool:
            decide_called["n"] += 1
            return True

        svc = PipelineService(run_predecide_phase=_predecide, decide_service=DecideBatchService(run_decide_phase=_decide))
        out = svc.run_batch(req=BatchRunRequest(batch_idx=0, batch_id="b0"))
        self.assertFalse(out.continue_loop)
        self.assertEqual(out.status_hint, "")
        self.assertEqual(decide_called["n"], 0)

    def test_pipeline_runs_decide_when_preact_decision_available(self) -> None:
        decide_called = {"n": 0}

        def _predecide(_req: BatchRunRequest):
            return PreactionDecision(final_continue=None, checks_obj={}, auto_answer_obj={}, repo_obs={}, hands_last="x")

        def _decide(_req: BatchRunRequest, _preaction: PreactionDecision) -> bool:
            decide_called["n"] += 1
            return True

        svc = PipelineService(run_predecide_phase=_predecide, decide_service=DecideBatchService(run_decide_phase=_decide))
        out = svc.run_batch(req=BatchRunRequest(batch_idx=1, batch_id="b1"))
        self.assertTrue(out.continue_loop)
        self.assertEqual(decide_called["n"], 1)

    def test_checkpoint_service_calls_callback_with_request(self) -> None:
        seen = {}

        def _run(req: CheckpointRequest) -> None:
            seen["batch_id"] = req.batch_id
            seen["status_hint"] = req.status_hint

        svc = CheckpointService(run_checkpoint=_run)
        svc.run(request=CheckpointRequest(batch_id="b9", planned_next_input="", status_hint="done", note="x"))
        self.assertEqual(seen.get("batch_id"), "b9")
        self.assertEqual(seen.get("status_hint"), "done")


if __name__ == "__main__":
    unittest.main()
