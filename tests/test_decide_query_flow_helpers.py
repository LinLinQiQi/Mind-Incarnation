from __future__ import annotations

import unittest

from mi.runtime.autopilot.decide_query_flow import (
    DecideNextQueryDeps,
    DecideRecordEffectsDeps,
    query_decide_next,
    record_decide_next_effects,
)


class _FakeContext:
    def __init__(self, obj: dict[str, object]) -> None:
        self._obj = obj

    def to_prompt_obj(self) -> dict[str, object]:
        return dict(self._obj)


class DecideQueryFlowHelpersTests(unittest.TestCase):
    def test_query_decide_next_builds_prompt_and_returns_context(self) -> None:
        calls: dict[str, object] = {}

        def _build_prompt(**kwargs: object) -> str:
            calls["prompt_kwargs"] = dict(kwargs)
            return "prompt"

        def _mind_call(**kwargs: object) -> tuple[object, str, str]:
            calls["mind_kwargs"] = dict(kwargs)
            return {"next_action": "stop", "status": "done"}, "mind_ref", "ok"

        out = query_decide_next(
            batch_idx=3,
            batch_id="b3",
            task="fix tests",
            hands_provider="codex",
            mindspec_base={"runtime": True},
            project_overlay={"k": "v"},
            workflow_run={"active": "wf_1"},
            workflow_load_effective=lambda: [{"id": "wf_1"}],
            recent_evidence=[{"kind": "hands_result"}],
            hands_last="last",
            repo_obs={"changes": True},
            checks_obj={"should_run_checks": False},
            auto_answer_obj={"should_answer": False},
            deps=DecideNextQueryDeps(
                build_decide_context=lambda **_kwargs: _FakeContext({"claims": [{"id": "c1"}]}),
                summarize_thought_db_context=lambda _ctx: {"summary": "ok"},
                decide_next_prompt_builder=_build_prompt,
                load_active_workflow=lambda **_kwargs: {"id": "wf_1"},
                mind_call=_mind_call,
            ),
        )

        decision_obj, mind_ref, state, tdb_obj, tdb_summary = out
        self.assertEqual(decision_obj, {"next_action": "stop", "status": "done"})
        self.assertEqual(mind_ref, "mind_ref")
        self.assertEqual(state, "ok")
        self.assertEqual(tdb_obj, {"claims": [{"id": "c1"}]})
        self.assertEqual(tdb_summary, {"summary": "ok"})
        prompt_kwargs = calls.get("prompt_kwargs")
        self.assertIsInstance(prompt_kwargs, dict)
        self.assertEqual(prompt_kwargs.get("active_workflow"), {"id": "wf_1"})
        mind_kwargs = calls.get("mind_kwargs")
        self.assertIsInstance(mind_kwargs, dict)
        self.assertEqual(mind_kwargs.get("schema_filename"), "decide_next.json")

    def test_query_decide_next_non_dict_decision_becomes_none(self) -> None:
        decision_obj, _ref, _state, tdb_obj, tdb_summary = query_decide_next(
            batch_idx=1,
            batch_id="b1",
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            workflow_run={},
            workflow_load_effective=lambda: [],
            recent_evidence=[],
            hands_last="last",
            repo_obs={},
            checks_obj={},
            auto_answer_obj={},
            deps=DecideNextQueryDeps(
                build_decide_context=lambda **_kwargs: _FakeContext({"claims": []}),
                summarize_thought_db_context=lambda _ctx: {"summary": "ok"},
                decide_next_prompt_builder=lambda **_kwargs: "prompt",
                load_active_workflow=lambda **_kwargs: {},
                mind_call=lambda **_kwargs: ("bad", "mind_ref", "ok"),
            ),
        )
        self.assertIsNone(decision_obj)
        self.assertEqual(tdb_obj, {"claims": []})
        self.assertEqual(tdb_summary, {"summary": "ok"})

    def test_record_decide_next_effects_applies_overlay_and_learn(self) -> None:
        calls: dict[str, object] = {
            "segment": [],
            "overlay": [],
            "learn": [],
            "emit": [],
            "persist": 0,
        }

        out = record_decide_next_effects(
            batch_idx=4,
            decision_obj={
                "next_action": "send_to_hands",
                "status": "not_done",
                "notes": "continue",
                "confidence": 0.734,
                "update_project_overlay": {"set_testless_strategy": "manual_review"},
                "learn_suggested": [{"kind": "claim"}],
            },
            decision_mind_ref="mind_decide",
            tdb_ctx_summary={"summary": "ok"},
            deps=DecideRecordEffectsDeps(
                log_decide_next=lambda **_kwargs: {"event_id": "ev_1", "kind": "decide_next"},
                segment_add=lambda item: calls["segment"].append(dict(item)),
                persist_segment_state=lambda: calls.__setitem__("persist", int(calls["persist"]) + 1),
                apply_set_testless_strategy_overlay_update=lambda **kwargs: calls["overlay"].append(dict(kwargs)),
                handle_learn_suggested=lambda **kwargs: calls["learn"].append(dict(kwargs)),
                emit_prefixed=lambda prefix, text: calls["emit"].append((prefix, text)),
            ),
        )

        self.assertEqual(out.next_action, "send_to_hands")
        self.assertEqual(out.status, "not_done")
        self.assertEqual(out.notes, "continue")
        self.assertEqual((out.decide_rec or {}).get("event_id"), "ev_1")
        self.assertEqual(len(calls["segment"]), 1)
        self.assertEqual(len(calls["overlay"]), 1)
        self.assertEqual(len(calls["learn"]), 1)
        self.assertEqual(calls["persist"], 1)
        self.assertIn("confidence=0.73", (calls["emit"][0][1] if calls["emit"] else ""))
        self.assertEqual(calls["learn"][0].get("source_event_ids"), ["ev_1"])

    def test_record_decide_next_effects_falls_back_when_no_log_record(self) -> None:
        segments: list[dict[str, object]] = []
        emits: list[tuple[str, str]] = []

        out = record_decide_next_effects(
            batch_idx=2,
            decision_obj={"next_action": "stop", "status": "done", "notes": "ok", "confidence": "high"},
            decision_mind_ref="mind_decide",
            tdb_ctx_summary={},
            deps=DecideRecordEffectsDeps(
                log_decide_next=lambda **_kwargs: None,
                segment_add=lambda item: segments.append(dict(item)),
                persist_segment_state=lambda: None,
                apply_set_testless_strategy_overlay_update=lambda **_kwargs: None,
                handle_learn_suggested=lambda **_kwargs: None,
                emit_prefixed=lambda prefix, text: emits.append((prefix, text)),
            ),
        )

        self.assertEqual(out.next_action, "stop")
        self.assertIsNone(out.decide_rec)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].get("kind"), "decide_next")
        self.assertTrue(any("confidence=high" in t for _p, t in emits))


if __name__ == "__main__":
    unittest.main()
