from __future__ import annotations

import unittest

from mi.runtime.autopilot import (
    ChecksService,
    DecideService,
    EvidenceService,
    LearnService,
    MemoryRecallService,
    RiskService,
    RunEngineDeps,
    RunMutableState,
    WorkflowService,
    run_autopilot_engine,
)


class RunEngineTests(unittest.TestCase):
    def test_engine_runs_batches_and_finalize_callbacks(self) -> None:
        called = {"batches": 0, "checkpoint": 0, "learn": 0, "why": 0, "flush": 0, "warn": 0}

        def _run_single_batch(_idx: int, _bid: str) -> bool:
            called["batches"] += 1
            return called["batches"] < 2

        def _checkpoint(**kwargs) -> None:
            called["checkpoint"] += 1
            self.assertEqual(kwargs.get("batch_id"), "b1")

        def _learn() -> None:
            called["learn"] += 1

        def _why() -> None:
            called["why"] += 1

        def _flush() -> None:
            called["flush"] += 1

        def _warn() -> None:
            called["warn"] += 1

        executed = {"n": 0}

        st = run_autopilot_engine(
            max_batches=8,
            state=RunMutableState(status="not_done", notes="", last_batch_id=""),
            deps=RunEngineDeps(
                run_single_batch=_run_single_batch,
                executed_batches_getter=lambda: executed["n"],
                checkpoint_enabled=True,
                checkpoint_runner=_checkpoint,
                learn_runner=_learn,
                why_runner=_why,
                snapshot_flusher=_flush,
                state_warning_flusher=_warn,
            ),
        )
        self.assertEqual(called["batches"], 2)
        self.assertEqual(st.last_batch_id, "b1")
        self.assertEqual(st.status, "not_done")
        self.assertFalse(st.max_batches_exhausted)
        # executed=0 -> checkpoint should not run
        self.assertEqual(called["checkpoint"], 0)
        self.assertEqual(called["learn"], 1)
        self.assertEqual(called["why"], 1)
        self.assertEqual(called["flush"], 1)
        self.assertEqual(called["warn"], 1)

        # rerun with executed > 0 to cover checkpoint path
        called = {k: 0 for k in called}
        executed["n"] = 3
        st2 = run_autopilot_engine(
            max_batches=8,
            state=RunMutableState(status="not_done", notes="", last_batch_id=""),
            deps=RunEngineDeps(
                run_single_batch=_run_single_batch,
                executed_batches_getter=lambda: executed["n"],
                checkpoint_enabled=True,
                checkpoint_runner=_checkpoint,
                learn_runner=_learn,
                why_runner=_why,
                snapshot_flusher=_flush,
                state_warning_flusher=_warn,
            ),
        )
        self.assertEqual(st2.last_batch_id, "b1")
        self.assertEqual(called["checkpoint"], 1)

    def test_engine_marks_blocked_on_max_batches_exhausted(self) -> None:
        called = {"learn": 0, "why": 0, "flush": 0, "warn": 0}

        def _run_single_batch(_idx: int, _bid: str) -> bool:
            return True

        def _checkpoint(**_kwargs) -> None:
            return None

        def _learn() -> None:
            called["learn"] += 1

        def _why() -> None:
            called["why"] += 1

        def _flush() -> None:
            called["flush"] += 1

        def _warn() -> None:
            called["warn"] += 1

        st = run_autopilot_engine(
            max_batches=2,
            state=RunMutableState(status="not_done", notes="", last_batch_id=""),
            deps=RunEngineDeps(
                run_single_batch=_run_single_batch,
                executed_batches_getter=lambda: 2,
                checkpoint_enabled=True,
                checkpoint_runner=_checkpoint,
                learn_runner=_learn,
                why_runner=_why,
                snapshot_flusher=_flush,
                state_warning_flusher=_warn,
            ),
        )
        self.assertTrue(st.max_batches_exhausted)
        self.assertEqual(st.status, "blocked")
        self.assertIn("reached max_batches", st.notes)
        self.assertEqual(called["learn"], 1)
        self.assertEqual(called["why"], 1)
        self.assertEqual(called["flush"], 1)
        self.assertEqual(called["warn"], 1)

    def test_engine_invokes_state_machine_services(self) -> None:
        hooks = {"chk": 0, "risk": 0, "wf_start": 0, "wf_batch": 0, "wf_end": 0, "mem_start": 0, "decide": 0, "ev": 0, "learn": 0}

        def _run_single_batch(_idx: int, _bid: str) -> bool:
            return False

        st = run_autopilot_engine(
            max_batches=4,
            state=RunMutableState(status="not_done", notes="", last_batch_id=""),
            deps=RunEngineDeps(
                run_single_batch=_run_single_batch,
                executed_batches_getter=lambda: 0,
                checkpoint_enabled=False,
                checkpoint_runner=lambda **_kwargs: None,
                learn_runner=lambda: None,
                why_runner=lambda: None,
                snapshot_flusher=lambda: None,
                state_warning_flusher=lambda: None,
                checks_service=ChecksService(on_checkpoint_cb=lambda **_kwargs: hooks.__setitem__("chk", hooks["chk"] + 1)),
                risk_service=RiskService(on_post_batch_cb=lambda **_kwargs: hooks.__setitem__("risk", hooks["risk"] + 1)),
                workflow_service=WorkflowService(
                    on_run_start_cb=lambda: hooks.__setitem__("wf_start", hooks["wf_start"] + 1),
                    on_post_batch_cb=lambda **_kwargs: hooks.__setitem__("wf_batch", hooks["wf_batch"] + 1),
                    on_run_end_cb=lambda: hooks.__setitem__("wf_end", hooks["wf_end"] + 1),
                ),
                learn_service=LearnService(on_run_end_cb=lambda: hooks.__setitem__("learn", hooks["learn"] + 1)),
                memory_recall_service=MemoryRecallService(on_run_start_cb=lambda: hooks.__setitem__("mem_start", hooks["mem_start"] + 1)),
                decide_service=DecideService(on_post_batch_cb=lambda **_kwargs: hooks.__setitem__("decide", hooks["decide"] + 1)),
                evidence_service=EvidenceService(
                    on_transition_cb=lambda **_kwargs: hooks.__setitem__("ev", hooks["ev"] + 1),
                    on_run_end_cb=lambda: hooks.__setitem__("ev", hooks["ev"] + 1),
                ),
            ),
        )
        self.assertEqual(st.last_batch_id, "b0")
        self.assertEqual(hooks["wf_start"], 1)
        self.assertEqual(hooks["mem_start"], 1)
        self.assertEqual(hooks["risk"], 1)
        self.assertEqual(hooks["decide"], 1)
        self.assertEqual(hooks["wf_batch"], 1)
        self.assertEqual(hooks["chk"], 0)
        self.assertEqual(hooks["wf_end"], 1)
        self.assertEqual(hooks["learn"], 1)
        self.assertGreaterEqual(hooks["ev"], 2)


if __name__ == "__main__":
    unittest.main()
