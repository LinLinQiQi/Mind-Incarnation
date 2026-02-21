from __future__ import annotations

import unittest

from mi.runtime.autopilot.interaction_record_flow import (
    InteractionRecordDeps,
    append_auto_answer_record_with_tracking,
    append_user_input_record_with_tracking,
)
from mi.runtime.autopilot.batch_effects import append_evidence_window


class InteractionRecordFlowHelpersTests(unittest.TestCase):
    def test_append_user_input_updates_window_and_segment(self) -> None:
        evidence_events: list[dict[str, object]] = []
        segment: list[dict[str, object]] = []
        window: list[dict[str, object]] = []

        def _append(rec: dict[str, object]) -> dict[str, object]:
            out = dict(rec)
            out["event_id"] = "ev_ui_1"
            evidence_events.append(out)
            return out

        out = append_user_input_record_with_tracking(
            batch_id="b1",
            question="q?",
            answer="a",
            evidence_window=window,
            deps=InteractionRecordDeps(
                evidence_append=_append,
                append_window=append_evidence_window,
                append_segment_record=lambda rec: segment.append(dict(rec)),
                now_ts=lambda: "2026-01-01T00:00:00Z",
                thread_id="t1",
            ),
        )

        self.assertEqual(out.get("event_id"), "ev_ui_1")
        self.assertEqual(len(evidence_events), 1)
        self.assertEqual(evidence_events[0].get("kind"), "user_input")
        self.assertEqual(len(window), 1)
        self.assertEqual(window[0].get("kind"), "user_input")
        self.assertEqual(window[0].get("event_id"), "ev_ui_1")
        self.assertEqual(window[0].get("question"), "q?")
        self.assertEqual(window[0].get("answer"), "a")
        self.assertEqual(len(segment), 1)
        self.assertEqual(segment[0].get("event_id"), "ev_ui_1")
        self.assertEqual(segment[0].get("kind"), "user_input")

    def test_append_auto_answer_flattens_into_window_and_segment(self) -> None:
        evidence_events: list[dict[str, object]] = []
        segment: list[dict[str, object]] = []
        window: list[dict[str, object]] = []

        def _append(rec: dict[str, object]) -> dict[str, object]:
            out = dict(rec)
            out["event_id"] = "ev_aa_1"
            evidence_events.append(out)
            return out

        out = append_auto_answer_record_with_tracking(
            batch_id="b2",
            mind_transcript_ref="mind.jsonl",
            auto_answer={"should_answer": True, "hands_answer_input": "do X"},
            evidence_window=window,
            deps=InteractionRecordDeps(
                evidence_append=_append,
                append_window=append_evidence_window,
                append_segment_record=lambda rec: segment.append(dict(rec)),
                now_ts=lambda: "2026-01-01T00:00:00Z",
                thread_id="t2",
            ),
        )

        self.assertEqual(out.get("event_id"), "ev_aa_1")
        self.assertEqual(len(evidence_events), 1)
        self.assertEqual(evidence_events[0].get("kind"), "auto_answer")
        self.assertEqual(evidence_events[0].get("mind_transcript_ref"), "mind.jsonl")
        self.assertEqual((evidence_events[0].get("auto_answer") or {}).get("should_answer"), True)
        self.assertEqual(len(window), 1)
        self.assertEqual(window[0].get("kind"), "auto_answer")
        self.assertEqual(window[0].get("event_id"), "ev_aa_1")
        self.assertEqual(window[0].get("should_answer"), True)
        self.assertEqual(window[0].get("hands_answer_input"), "do X")
        self.assertEqual(len(segment), 1)
        self.assertEqual(segment[0].get("kind"), "auto_answer")
        self.assertEqual(segment[0].get("event_id"), "ev_aa_1")
        self.assertEqual(segment[0].get("should_answer"), True)
        self.assertEqual(segment[0].get("hands_answer_input"), "do X")


if __name__ == "__main__":
    unittest.main()

