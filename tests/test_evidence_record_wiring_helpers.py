from __future__ import annotations

import unittest

from mi.runtime.autopilot.batch_effects import append_evidence_window
from mi.runtime.wiring.evidence_record import (
    EvidenceRecordWiringDeps,
    append_evidence_with_tracking_wired,
)


class EvidenceRecordWiringHelpersTests(unittest.TestCase):
    def test_append_evidence_with_tracking_wired_plumbs_thread_id_and_persists(self) -> None:
        evidence_window: list[dict[str, object]] = []
        evidence_events: list[dict[str, object]] = []
        segment_written: list[dict[str, object]] = []
        persisted = {"n": 0}
        thread_id = "t1"

        def _ev_append(rec: dict[str, object]):
            out = dict(rec)
            out["event_id"] = f"ev_{len(evidence_events) + 1}"
            evidence_events.append(out)
            return out

        deps = EvidenceRecordWiringDeps(
            evidence_window=evidence_window,
            evidence_append=_ev_append,
            append_window=append_evidence_window,
            segment_add=lambda item: segment_written.append(dict(item)),
            persist_segment_state=lambda: persisted.__setitem__("n", int(persisted["n"]) + 1),
            now_ts=lambda: "2026-02-01T00:00:00Z",
            thread_id_getter=lambda: thread_id,
        )

        out = append_evidence_with_tracking_wired(
            batch_id="b1",
            hands_transcript_ref="hands.jsonl",
            mind_transcript_ref="mind.jsonl",
            mi_input="input",
            transcript_observation={"o": 1},
            repo_observation={"r": 2},
            evidence_obj={"facts": ["f1"], "unknowns": [], "risk_signals": []},
            deps=deps,
        )

        self.assertEqual(out.get("event_id"), "ev_1")
        self.assertEqual(len(evidence_events), 1)
        self.assertEqual(evidence_events[0].get("kind"), "evidence")
        self.assertEqual(evidence_events[0].get("thread_id"), "t1")
        self.assertEqual(len(evidence_window), 1)
        self.assertEqual(evidence_window[0].get("event_id"), "ev_1")
        self.assertEqual(len(segment_written), 1)
        self.assertEqual(segment_written[0].get("event_id"), "ev_1")
        self.assertEqual(persisted["n"], 1)

        thread_id = "t2"
        append_evidence_with_tracking_wired(
            batch_id="b2",
            hands_transcript_ref="hands2.jsonl",
            mind_transcript_ref="mind2.jsonl",
            mi_input="input2",
            transcript_observation={},
            repo_observation={},
            evidence_obj={},
            deps=deps,
        )
        self.assertEqual(evidence_events[1].get("thread_id"), "t2")


if __name__ == "__main__":
    unittest.main()

