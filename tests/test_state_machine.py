from __future__ import annotations

import unittest

from mi.runtime.autopilot import AutopilotState, compact_transition_trace, run_state_machine_loop


class StateMachineTests(unittest.TestCase):
    def test_state_machine_stops_early_when_batch_requests_stop(self) -> None:
        calls = {"n": 0}

        def _run_single_batch(_idx: int, _bid: str) -> bool:
            calls["n"] += 1
            return calls["n"] < 2

        st, trace = run_state_machine_loop(max_batches=8, run_single_batch=_run_single_batch)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(st.last_batch_id, "b1")
        self.assertEqual(st.state, AutopilotState.DONE)
        compact = compact_transition_trace(trace)
        self.assertTrue(any(str(x.get("to")) == "done" for x in compact))

    def test_state_machine_marks_blocked_on_max_exhaustion(self) -> None:
        def _run_single_batch(_idx: int, _bid: str) -> bool:
            return True

        st, trace = run_state_machine_loop(max_batches=2, run_single_batch=_run_single_batch)
        self.assertEqual(st.last_batch_id, "b1")
        self.assertEqual(st.state, AutopilotState.BLOCKED)
        compact = compact_transition_trace(trace)
        self.assertEqual(str(compact[-1].get("to")), "blocked")


if __name__ == "__main__":
    unittest.main()

