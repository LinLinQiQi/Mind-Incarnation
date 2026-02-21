from __future__ import annotations

import unittest

from mi.runtime.autopilot import (
    PreactionDecision,
    RunLoopOrchestrator,
    RunLoopOrchestratorDeps,
)


class RunLoopOrchestratorTests(unittest.TestCase):
    def test_orchestrator_runs_pipeline_and_finalize_callbacks(self) -> None:
        state = {
            "next_input": "do work",
            "thread_id": "th_1",
            "status": "not_done",
            "notes": "",
            "last_batch_id": "",
            "executed_batches": 3,
        }
        calls = {
            "predecide": 0,
            "decide": 0,
            "checkpoint": 0,
            "learn": 0,
            "why": 0,
            "flush": 0,
            "warn": 0,
        }

        def _run_predecide(req):
            calls["predecide"] += 1
            self.assertEqual(str(req.next_input), "do work")
            self.assertEqual(str(req.thread_id), "th_1")
            return PreactionDecision(
                final_continue=None,
                checks_obj={},
                auto_answer_obj={},
                repo_obs={"repo": "ok"},
                hands_last="done",
            )

        def _run_decide(req, preaction):
            calls["decide"] += 1
            self.assertEqual(str(req.batch_id), "b0")
            self.assertEqual(str(preaction.hands_last), "done")
            return False

        def _checkpoint(req):
            calls["checkpoint"] += 1
            self.assertEqual(str(req.batch_id), "b0")

        orchestrator = RunLoopOrchestrator(
            deps=RunLoopOrchestratorDeps(
                max_batches=6,
                run_predecide_phase=_run_predecide,
                run_decide_phase=_run_decide,
                next_input_getter=lambda: str(state["next_input"]),
                thread_id_getter=lambda: str(state["thread_id"]),
                status_getter=lambda: str(state["status"]),
                status_setter=lambda v: state.__setitem__("status", str(v or "")),
                notes_getter=lambda: str(state["notes"]),
                notes_setter=lambda v: state.__setitem__("notes", str(v or "")),
                last_batch_id_getter=lambda: str(state["last_batch_id"]),
                last_batch_id_setter=lambda v: state.__setitem__("last_batch_id", str(v or "")),
                executed_batches_getter=lambda: int(state["executed_batches"]),
                checkpoint_enabled=True,
                checkpoint_runner=_checkpoint,
                learn_runner=lambda: calls.__setitem__("learn", calls["learn"] + 1),
                why_runner=lambda: calls.__setitem__("why", calls["why"] + 1),
                snapshot_flusher=lambda: calls.__setitem__("flush", calls["flush"] + 1),
                state_warning_flusher=lambda: calls.__setitem__("warn", calls["warn"] + 1),
            )
        )

        out = orchestrator.run()
        self.assertFalse(bool(out.max_batches_exhausted))
        self.assertEqual(str(state["last_batch_id"]), "b0")
        self.assertEqual(calls["predecide"], 1)
        self.assertEqual(calls["decide"], 1)
        self.assertEqual(calls["checkpoint"], 1)
        self.assertEqual(calls["learn"], 1)
        self.assertEqual(calls["why"], 1)
        self.assertEqual(calls["flush"], 1)
        self.assertEqual(calls["warn"], 1)

    def test_orchestrator_sets_blocked_status_when_batches_exhausted(self) -> None:
        state = {
            "next_input": "continue",
            "thread_id": "th_2",
            "status": "not_done",
            "notes": "",
            "last_batch_id": "",
            "executed_batches": 2,
        }

        orchestrator = RunLoopOrchestrator(
            deps=RunLoopOrchestratorDeps(
                max_batches=2,
                run_predecide_phase=lambda req: PreactionDecision(
                    final_continue=None,
                    checks_obj={},
                    auto_answer_obj={},
                    repo_obs={},
                    hands_last=str(req.batch_id),
                ),
                run_decide_phase=lambda _req, _pre: True,
                next_input_getter=lambda: str(state["next_input"]),
                thread_id_getter=lambda: str(state["thread_id"]),
                status_getter=lambda: str(state["status"]),
                status_setter=lambda v: state.__setitem__("status", str(v or "")),
                notes_getter=lambda: str(state["notes"]),
                notes_setter=lambda v: state.__setitem__("notes", str(v or "")),
                last_batch_id_getter=lambda: str(state["last_batch_id"]),
                last_batch_id_setter=lambda v: state.__setitem__("last_batch_id", str(v or "")),
                executed_batches_getter=lambda: int(state["executed_batches"]),
                checkpoint_enabled=False,
                checkpoint_runner=lambda _req: None,
                learn_runner=lambda: None,
                why_runner=lambda: None,
                snapshot_flusher=lambda: None,
                state_warning_flusher=lambda: None,
            )
        )

        out = orchestrator.run()
        self.assertTrue(bool(out.max_batches_exhausted))
        self.assertEqual(str(state["status"]), "blocked")
        self.assertIn("reached max_batches", str(state["notes"]))


if __name__ == "__main__":
    unittest.main()
