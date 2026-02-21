from __future__ import annotations

import unittest

from mi.runtime.autopilot.risk_event_flow import (
    RiskEventAppendDeps,
    append_risk_event_with_tracking,
)


class RiskEventFlowHelpersTests(unittest.TestCase):
    def test_append_risk_event_tracks_window_and_segment(self) -> None:
        evidence_window: list[dict[str, object]] = []
        evidence_written: list[dict[str, object]] = []
        segment_written: list[dict[str, object]] = []

        def _ev_append(rec: dict[str, object]):
            out = dict(rec)
            out["event_id"] = "ev_risk_1"
            evidence_written.append(out)
            return out

        def _append_window(window: list[dict[str, object]], rec: dict[str, object]) -> None:
            window.append(dict(rec))

        rec = append_risk_event_with_tracking(
            batch_idx=4,
            risk_signals=["network", "install"],
            risk_obj={"category": "network", "severity": "high", "mitigation": ["offline first"]},
            risk_mind_ref="mind_risk_1",
            evidence_window=evidence_window,
            deps=RiskEventAppendDeps(
                evidence_append=_ev_append,
                append_window=_append_window,
                segment_add=lambda item: segment_written.append(dict(item)),
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id="t_1",
            ),
        )

        self.assertEqual(rec.get("event_id"), "ev_risk_1")
        self.assertEqual(evidence_written[0].get("kind"), "risk_event")
        self.assertEqual(evidence_written[0].get("batch_id"), "b4")
        self.assertEqual(evidence_written[0].get("thread_id"), "t_1")
        self.assertEqual(evidence_written[0].get("mind_transcript_ref"), "mind_risk_1")

        self.assertEqual(len(evidence_window), 1)
        self.assertEqual(evidence_window[0].get("kind"), "risk_event")
        self.assertEqual(evidence_window[0].get("event_id"), "ev_risk_1")
        self.assertEqual(evidence_window[0].get("category"), "network")

        self.assertEqual(len(segment_written), 1)
        self.assertEqual(segment_written[0].get("kind"), "risk_event")
        self.assertEqual(segment_written[0].get("risk_signals"), ["network", "install"])
        self.assertEqual(segment_written[0].get("severity"), "high")

    def test_append_risk_event_handles_non_dict_record(self) -> None:
        evidence_window: list[dict[str, object]] = []
        segment_written: list[dict[str, object]] = []

        rec = append_risk_event_with_tracking(
            batch_idx=1,
            risk_signals=[],
            risk_obj={},
            risk_mind_ref="",
            evidence_window=evidence_window,
            deps=RiskEventAppendDeps(
                evidence_append=lambda _rec: "not-a-dict",
                append_window=lambda window, obj: window.append(dict(obj)),
                segment_add=lambda item: segment_written.append(dict(item)),
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id="t_1",
            ),
        )

        self.assertEqual(rec, {})
        self.assertEqual(evidence_window[0].get("event_id"), None)
        self.assertEqual(segment_written[0].get("event_id"), None)


if __name__ == "__main__":
    unittest.main()
