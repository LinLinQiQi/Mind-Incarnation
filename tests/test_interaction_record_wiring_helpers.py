from __future__ import annotations

import unittest

from mi.runtime.autopilot.batch_effects import append_evidence_window
from mi.runtime.wiring.interaction_record import (
    InteractionRecordWiringDeps,
    append_auto_answer_record_wired,
    append_user_input_record_wired,
)


class InteractionRecordWiringHelpersTests(unittest.TestCase):
    def test_append_records_wired_uses_thread_id_getter(self) -> None:
        evidence_events: list[dict[str, object]] = []
        segment: list[dict[str, object]] = []
        window: list[dict[str, object]] = []
        persisted = {"n": 0}
        thread_id = "t1"

        def _append(rec: dict[str, object]) -> dict[str, object]:
            out = dict(rec)
            out["event_id"] = f"ev_{len(evidence_events) + 1}"
            evidence_events.append(out)
            return out

        deps = InteractionRecordWiringDeps(
            evidence_window=window,
            evidence_append=_append,
            append_window=append_evidence_window,
            segment_add=lambda rec: segment.append(dict(rec)),
            persist_segment_state=lambda: persisted.__setitem__("n", int(persisted["n"]) + 1),
            now_ts=lambda: "2026-01-01T00:00:00Z",
            thread_id_getter=lambda: thread_id,
        )

        out_ui = append_user_input_record_wired(batch_id="b1", question="q?", answer="a", deps=deps)
        self.assertEqual(out_ui.get("event_id"), "ev_1")
        self.assertEqual((evidence_events[0] or {}).get("thread_id"), "t1")

        thread_id = "t2"
        out_aa = append_auto_answer_record_wired(
            batch_id="b2",
            mind_transcript_ref="mind.jsonl",
            auto_answer={"should_answer": True, "hands_answer_input": "do X"},
            deps=deps,
        )
        self.assertEqual(out_aa.get("event_id"), "ev_2")
        self.assertEqual((evidence_events[1] or {}).get("thread_id"), "t2")

        self.assertEqual(len(window), 2)
        self.assertEqual(persisted["n"], 2)
        self.assertEqual(len(segment), 2)


if __name__ == "__main__":
    unittest.main()

