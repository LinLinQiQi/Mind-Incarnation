from __future__ import annotations

import unittest

from mi.runtime.autopilot.evidence_flow import (
    EvidenceAppendDeps,
    append_evidence_with_tracking,
)


class EvidenceFlowHelpersTests(unittest.TestCase):
    def test_append_evidence_writes_and_tracks(self) -> None:
        evidence_window: list[dict[str, object]] = []
        evidence_written: list[dict[str, object]] = []
        segment_written: list[dict[str, object]] = []

        def _ev_append(rec: dict[str, object]):
            out = dict(rec)
            out["event_id"] = "ev_evidence_1"
            evidence_written.append(out)
            return out

        def _append_window(window: list[dict[str, object]], rec: dict[str, object]) -> None:
            window.append(dict(rec))

        rec = append_evidence_with_tracking(
            batch_id="b2",
            hands_transcript_ref="hands_1",
            mind_transcript_ref="mind_1",
            mi_input="run checks",
            transcript_observation={"last": "done"},
            repo_observation={"status": "dirty"},
            evidence_obj={"facts": [{"k": "f"}], "risk_signals": ["network"]},
            evidence_window=evidence_window,
            deps=EvidenceAppendDeps(
                evidence_append=_ev_append,
                append_window=_append_window,
                segment_add=lambda item: segment_written.append(dict(item)),
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id="t_1",
            ),
        )

        self.assertEqual(rec.get("event_id"), "ev_evidence_1")
        self.assertEqual(evidence_written[0].get("kind"), "evidence")
        self.assertEqual(evidence_written[0].get("batch_id"), "b2")
        self.assertEqual(evidence_written[0].get("hands_transcript_ref"), "hands_1")
        self.assertEqual(evidence_written[0].get("mind_transcript_ref"), "mind_1")

        self.assertEqual(len(evidence_window), 1)
        self.assertEqual(evidence_window[0].get("event_id"), "ev_evidence_1")
        self.assertEqual(evidence_window[0].get("kind"), "evidence")

        self.assertEqual(len(segment_written), 1)
        self.assertEqual(segment_written[0].get("event_id"), "ev_evidence_1")
        self.assertEqual(segment_written[0].get("kind"), "evidence")

    def test_append_evidence_handles_non_dict_writer_output(self) -> None:
        evidence_window: list[dict[str, object]] = []
        segment_written: list[dict[str, object]] = []

        rec = append_evidence_with_tracking(
            batch_id="b1",
            hands_transcript_ref="",
            mind_transcript_ref="",
            mi_input="",
            transcript_observation={},
            repo_observation={},
            evidence_obj={},
            evidence_window=evidence_window,
            deps=EvidenceAppendDeps(
                evidence_append=lambda _rec: "not-a-dict",
                append_window=lambda window, obj: window.append(dict(obj)),
                segment_add=lambda item: segment_written.append(dict(item)),
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id="t_1",
            ),
        )

        self.assertEqual(rec, {})
        self.assertEqual(evidence_window, [{}])
        self.assertEqual(segment_written, [{}])


if __name__ == "__main__":
    unittest.main()
