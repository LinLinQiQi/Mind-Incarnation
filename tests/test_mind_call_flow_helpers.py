from __future__ import annotations

import unittest
from pathlib import Path

from mi.providers.mind_errors import MindCallError
from mi.runtime.autopilot.mind_call_flow import (
    MindCallDeps,
    MindCallResult,
    MindCallState,
    run_mind_call,
)


class _Res:
    def __init__(self, obj: object, transcript_path: str = "") -> None:
        self.obj = obj
        self.transcript_path = transcript_path


class _Err(Exception):
    def __init__(self, message: str, transcript_path: object = None) -> None:
        super().__init__(message)
        self.transcript_path = transcript_path


class MindCallFlowHelpersTests(unittest.TestCase):
    def test_success_resets_consecutive_failures(self) -> None:
        events: list[dict[str, object]] = []
        win: list[dict[str, object]] = []

        out = run_mind_call(
            state=MindCallState(failures_total=3, failures_consecutive=2, circuit_open=False),
            thread_id="t1",
            batch_id="b1",
            schema_filename="decide_next.json",
            prompt="p",
            tag="decide_b1",
            threshold=2,
            evidence_window=win,
            deps=MindCallDeps(
                llm_call=lambda **_kwargs: _Res({"next_action": "stop"}, "/tmp/mind.jsonl"),
                evidence_append=lambda obj: events.append(dict(obj)) or obj,
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, _n: s,
            ),
        )

        self.assertIsInstance(out, MindCallResult)
        self.assertEqual(out.state, "ok")
        self.assertEqual(out.obj, {"next_action": "stop"})
        self.assertEqual(out.mind_transcript_ref, "/tmp/mind.jsonl")
        self.assertEqual(out.next_state.failures_total, 3)
        self.assertEqual(out.next_state.failures_consecutive, 0)
        self.assertFalse(out.next_state.circuit_open)
        self.assertEqual(events, [])
        self.assertEqual(win, [])

    def test_error_logs_event_and_opens_circuit_when_threshold_reached(self) -> None:
        events: list[dict[str, object]] = []
        win: list[dict[str, object]] = []

        out = run_mind_call(
            state=MindCallState(failures_total=1, failures_consecutive=1, circuit_open=False),
            thread_id="t1",
            batch_id="b2",
            schema_filename="decide_next.json",
            prompt="p",
            tag="decide_b2",
            threshold=2,
            evidence_window=win,
            deps=MindCallDeps(
                llm_call=lambda **_kwargs: (_ for _ in ()).throw(_Err("boom", "/tmp/e.jsonl")),
                evidence_append=lambda obj: events.append(dict(obj)) or obj,
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
            ),
        )

        self.assertEqual(out.state, "error")
        self.assertIsNone(out.obj)
        self.assertEqual(out.mind_transcript_ref, "/tmp/e.jsonl")
        self.assertEqual(out.next_state.failures_total, 2)
        self.assertEqual(out.next_state.failures_consecutive, 2)
        self.assertTrue(out.next_state.circuit_open)
        self.assertEqual([str(e.get("kind") or "") for e in events], ["mind_error", "mind_circuit"])
        self.assertEqual([str(e.get("kind") or "") for e in win], ["mind_error", "mind_circuit"])

    def test_skipped_when_circuit_open(self) -> None:
        out = run_mind_call(
            state=MindCallState(failures_total=4, failures_consecutive=4, circuit_open=True),
            thread_id="t1",
            batch_id="b3",
            schema_filename="decide_next.json",
            prompt="p",
            tag="decide_b3",
            threshold=2,
            evidence_window=[],
            deps=MindCallDeps(
                llm_call=lambda **_kwargs: _Res({"next_action": "stop"}, "/tmp/mind.jsonl"),
                evidence_append=lambda obj: obj,
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, _n: s,
            ),
        )

        self.assertEqual(out.state, "skipped")
        self.assertIsNone(out.obj)
        self.assertEqual(out.mind_transcript_ref, "")
        self.assertEqual(out.next_state.failures_total, 4)
        self.assertEqual(out.next_state.failures_consecutive, 4)
        self.assertTrue(out.next_state.circuit_open)

    def test_mindcallerror_path_resolution_supports_path_object(self) -> None:
        events: list[dict[str, object]] = []
        win: list[dict[str, object]] = []

        err = MindCallError(
            "bad",
            schema_filename="decide_next.json",
            tag="decide_b4",
            transcript_path=Path("/tmp/mind_err.jsonl"),
        )
        out = run_mind_call(
            state=MindCallState(failures_total=0, failures_consecutive=0, circuit_open=False),
            thread_id="t1",
            batch_id="b4",
            schema_filename="decide_next.json",
            prompt="p",
            tag="decide_b4",
            threshold=99,
            evidence_window=win,
            deps=MindCallDeps(
                llm_call=lambda **_kwargs: (_ for _ in ()).throw(err),
                evidence_append=lambda obj: events.append(dict(obj)) or obj,
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, _n: s,
            ),
        )

        self.assertEqual(out.state, "error")
        self.assertEqual(out.mind_transcript_ref, "/tmp/mind_err.jsonl")
        self.assertEqual((events[0].get("mind_transcript_ref") if events else ""), "/tmp/mind_err.jsonl")


if __name__ == "__main__":
    unittest.main()
