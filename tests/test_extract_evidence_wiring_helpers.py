from __future__ import annotations

import unittest
from types import SimpleNamespace

from mi.runtime.autopilot.batch_effects import append_evidence_window
from mi.runtime.wiring import EvidenceRecordWiringDeps
from mi.runtime.wiring.extract_evidence import (
    ExtractEvidenceContextWiringDeps,
    extract_evidence_and_context_wired,
)


class _FakeDecideCtx:
    def __init__(self, obj: dict[str, object]) -> None:
        self._obj = dict(obj)

    def to_prompt_obj(self) -> dict[str, object]:
        return dict(self._obj)


class ExtractEvidenceWiringHelpersTests(unittest.TestCase):
    def test_extract_evidence_and_context_wired_records_evidence_and_builds_ctx(self) -> None:
        evidence_events: list[dict[str, object]] = []
        segment_written: list[dict[str, object]] = []
        evidence_window: list[dict[str, object]] = []
        persisted = {"n": 0}
        messages: list[tuple[str, str]] = []
        captured: dict[str, object] = {}

        def _ev_append(rec: dict[str, object]) -> dict[str, object]:
            out = dict(rec)
            out["event_id"] = f"ev_{len(evidence_events) + 1}"
            evidence_events.append(out)
            return out

        def _prompt_builder(**kwargs: object) -> str:
            captured.update(kwargs)
            return "PROMPT"

        mind_calls: list[dict[str, object]] = []

        def _mind_call(**kwargs: object):
            mind_calls.append(dict(kwargs))
            return (
                {"facts": [{"k": "f"}], "actions": [], "results": [], "unknowns": [], "risk_signals": []},
                "mind_ref_1",
                "ok",
            )

        deps = ExtractEvidenceContextWiringDeps(
            task="t",
            hands_provider="codex",
            batch_summary_fn=lambda _result: {"transcript_observation": {"last": "ok"}},
            extract_evidence_prompt_builder=_prompt_builder,
            mind_call=_mind_call,
            empty_evidence_obj=lambda note="": {
                "facts": [],
                "actions": [],
                "results": [],
                "unknowns": [],
                "risk_signals": [],
                "note": str(note or ""),
            },
            extract_evidence_counts=lambda _obj: {
                "facts": 1,
                "actions": 0,
                "results": 0,
                "unknowns": 0,
                "risk_signals": 0,
            },
            emit_prefixed=lambda prefix, msg: messages.append((str(prefix), str(msg))),
            evidence_record_deps=EvidenceRecordWiringDeps(
                evidence_window=evidence_window,
                evidence_append=_ev_append,
                append_window=append_evidence_window,
                segment_add=lambda item: segment_written.append(dict(item)),
                persist_segment_state=lambda: persisted.__setitem__("n", int(persisted["n"]) + 1),
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id_getter=lambda: "t1",
            ),
            build_decide_context=lambda **_kwargs: _FakeDecideCtx({"ctx": 1}),
        )

        ctx = SimpleNamespace(light_injection="LI", batch_input="do X", hands_transcript="hands.jsonl")
        result = SimpleNamespace(last_agent_message=lambda: "done")

        out = extract_evidence_and_context_wired(
            batch_idx=1,
            batch_id="b1",
            ctx=ctx,
            result=result,
            repo_obs={"dirty": False},
            deps=deps,
        )

        self.assertEqual(captured.get("task"), "t")
        self.assertEqual(captured.get("hands_provider"), "codex")
        self.assertEqual(captured.get("light_injection"), "LI")
        self.assertEqual(captured.get("batch_input"), "do X")
        self.assertEqual(captured.get("repo_observation"), {"dirty": False})

        self.assertEqual(mind_calls[0].get("schema_filename"), "extract_evidence.json")
        self.assertEqual(mind_calls[0].get("prompt"), "PROMPT")
        self.assertEqual(mind_calls[0].get("tag"), "extract_b1")
        self.assertEqual(mind_calls[0].get("batch_id"), "b1")

        self.assertEqual(out.hands_last, "done")
        self.assertEqual(out.tdb_ctx_batch_obj, {"ctx": 1})

        self.assertEqual(len(evidence_window), 1)
        self.assertEqual(out.evidence_rec.get("event_id"), "ev_1")
        self.assertEqual(evidence_events[0].get("thread_id"), "t1")
        self.assertEqual(persisted["n"], 1)
        self.assertEqual(len(segment_written), 1)

        self.assertTrue(messages)
        self.assertIn("extract_evidence state=ok", messages[0][1])

    def test_extract_evidence_and_context_wired_handles_skipped(self) -> None:
        evidence_events: list[dict[str, object]] = []
        evidence_window: list[dict[str, object]] = []
        notes: list[str] = []

        def _ev_append(rec: dict[str, object]) -> dict[str, object]:
            out = dict(rec)
            out["event_id"] = f"ev_{len(evidence_events) + 1}"
            evidence_events.append(out)
            return out

        deps = ExtractEvidenceContextWiringDeps(
            task="t",
            hands_provider="codex",
            batch_summary_fn=lambda _result: {"transcript_observation": {}},
            extract_evidence_prompt_builder=lambda **_kwargs: "PROMPT",
            mind_call=lambda **_kwargs: (None, "", "skipped"),
            empty_evidence_obj=lambda note="": {
                "facts": [],
                "actions": [],
                "results": [],
                "unknowns": [],
                "risk_signals": [],
                "note": str(note or ""),
            },
            extract_evidence_counts=lambda _obj: {
                "facts": 0,
                "actions": 0,
                "results": 0,
                "unknowns": 0,
                "risk_signals": 0,
            },
            emit_prefixed=lambda _p, msg: notes.append(str(msg)),
            evidence_record_deps=EvidenceRecordWiringDeps(
                evidence_window=evidence_window,
                evidence_append=_ev_append,
                append_window=append_evidence_window,
                segment_add=lambda _item: None,
                persist_segment_state=lambda: None,
                now_ts=lambda: "2026-02-01T00:00:00Z",
                thread_id_getter=lambda: "t1",
            ),
            build_decide_context=lambda **_kwargs: _FakeDecideCtx({}),
        )

        ctx = SimpleNamespace(light_injection="", batch_input="", hands_transcript="hands.jsonl")
        result = SimpleNamespace(last_agent_message=lambda: "")

        out = extract_evidence_and_context_wired(
            batch_idx=2,
            batch_id="b2",
            ctx=ctx,
            result=result,
            repo_obs={},
            deps=deps,
        )

        self.assertEqual(out.evidence_obj.get("note"), "mind_circuit_open: extract_evidence skipped")
        self.assertIn("state=skipped", notes[0])


if __name__ == "__main__":
    unittest.main()

