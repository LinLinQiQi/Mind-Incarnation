from __future__ import annotations

import unittest
from dataclasses import dataclass

from mi.runtime.autopilot.recall_flow import RecallDeps, maybe_cross_project_recall_write_through


@dataclass(frozen=True)
class _Outcome:
    evidence_event: dict[str, object]
    window_entry: dict[str, object]


class RecallFlowHelpersTests(unittest.TestCase):
    def test_no_recall_no_side_effects(self) -> None:
        calls = {"evidence": 0, "segment": 0, "persist": 0}
        win: list[dict[str, object]] = [{"kind": "x"}]

        out = maybe_cross_project_recall_write_through(
            batch_id="b1",
            reason="run_start",
            query="q",
            thread_id="t1",
            evidence_window=win,
            deps=RecallDeps(
                mem_recall=lambda **_kwargs: None,
                evidence_append=lambda _obj: calls.__setitem__("evidence", calls["evidence"] + 1) or {},
                segment_add=lambda _rec: calls.__setitem__("segment", calls["segment"] + 1),
                persist_segment_state=lambda: calls.__setitem__("persist", calls["persist"] + 1),
            ),
        )

        self.assertIsNone(out)
        self.assertEqual(win, [{"kind": "x"}])
        self.assertEqual(calls, {"evidence": 0, "segment": 0, "persist": 0})

    def test_recall_appends_window_and_segment(self) -> None:
        calls = {"evidence": 0, "segment": 0, "persist": 0}
        win: list[dict[str, object]] = [{"kind": f"i{i}"} for i in range(8)]

        out = maybe_cross_project_recall_write_through(
            batch_id="b2",
            reason="before_ask_user",
            query="q2",
            thread_id="t2",
            evidence_window=win,
            deps=RecallDeps(
                mem_recall=lambda **_kwargs: _Outcome(
                    evidence_event={"kind": "cross_project_recall"},
                    window_entry={"kind": "cross_project_recall", "query": "q2"},
                ),
                evidence_append=lambda obj: calls.__setitem__("evidence", calls["evidence"] + 1)
                or {"event_id": "ev_1", **(obj if isinstance(obj, dict) else {})},
                segment_add=lambda _rec: calls.__setitem__("segment", calls["segment"] + 1),
                persist_segment_state=lambda: calls.__setitem__("persist", calls["persist"] + 1),
            ),
        )

        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("event_id"), "ev_1")
        self.assertEqual(len(win), 8)  # truncated
        self.assertEqual(win[-1].get("event_id"), "ev_1")
        self.assertEqual(win[-1].get("kind"), "cross_project_recall")
        self.assertEqual(calls, {"evidence": 1, "segment": 1, "persist": 1})


if __name__ == "__main__":
    unittest.main()

