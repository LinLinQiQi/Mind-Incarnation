from __future__ import annotations

import unittest

from mi.runtime.wiring.decide_next import (
    DecideNextQueryWiringDeps,
    DecideRecordEffectsWiringDeps,
    query_decide_next_wired,
    record_decide_next_effects_wired,
)


class _FakeContext:
    def __init__(self, obj: dict[str, object]) -> None:
        self._obj = dict(obj)

    def to_prompt_obj(self) -> dict[str, object]:
        return dict(self._obj)


class DecideNextWiringHelpersTests(unittest.TestCase):
    def test_query_decide_next_wired_plumbs_runner_context(self) -> None:
        calls: dict[str, object] = {}

        def _build_prompt(**kwargs: object) -> str:
            calls["prompt_kwargs"] = dict(kwargs)
            return "prompt"

        def _mind_call(**kwargs: object):
            calls["mind_kwargs"] = dict(kwargs)
            return {"next_action": "stop", "status": "done"}, "mind_ref", "ok"

        deps = DecideNextQueryWiringDeps(
            task="fix tests",
            hands_provider="codex",
            runtime_cfg_getter=lambda: {"runtime": True},
            project_overlay={"k": "v"},
            workflow_run={"active": True},
            workflow_load_effective=lambda: [{"id": "wf_1"}],
            recent_evidence=[{"kind": "hands_result"}],
            build_decide_context=lambda **_kwargs: _FakeContext({"claims": [{"id": "c1"}]}),
            summarize_thought_db_context=lambda _ctx: {"summary": "ok"},
            decide_next_prompt_builder=_build_prompt,
            load_active_workflow=lambda **_kwargs: {"id": "wf_1"},
            mind_call=_mind_call,
        )

        decision_obj, mind_ref, state, tdb_obj, tdb_summary = query_decide_next_wired(
            batch_idx=3,
            batch_id="b3",
            hands_last="last",
            repo_obs={"changes": True},
            checks_obj={"should_run_checks": False},
            auto_answer_obj={"should_answer": False},
            deps=deps,
        )

        self.assertEqual(decision_obj, {"next_action": "stop", "status": "done"})
        self.assertEqual(mind_ref, "mind_ref")
        self.assertEqual(state, "ok")
        self.assertEqual(tdb_obj, {"claims": [{"id": "c1"}]})
        self.assertEqual(tdb_summary, {"summary": "ok"})

        prompt_kwargs = calls.get("prompt_kwargs")
        self.assertIsInstance(prompt_kwargs, dict)
        self.assertEqual(prompt_kwargs.get("task"), "fix tests")
        self.assertEqual(prompt_kwargs.get("hands_provider"), "codex")
        self.assertEqual(prompt_kwargs.get("runtime_cfg"), {"runtime": True})
        self.assertEqual(prompt_kwargs.get("project_overlay"), {"k": "v"})

        mind_kwargs = calls.get("mind_kwargs")
        self.assertIsInstance(mind_kwargs, dict)
        self.assertEqual(mind_kwargs.get("schema_filename"), "decide_next.json")
        self.assertEqual(mind_kwargs.get("tag"), "decide_b3")
        self.assertEqual(mind_kwargs.get("batch_id"), "b3")

    def test_record_decide_next_effects_wired_plumbs_effect_deps(self) -> None:
        calls: dict[str, object] = {"segment": [], "persist": 0}

        out = record_decide_next_effects_wired(
            batch_idx=4,
            decision_obj={"next_action": "stop", "status": "done", "notes": "ok"},
            decision_mind_ref="mind_decide",
            tdb_ctx_summary={},
            deps=DecideRecordEffectsWiringDeps(
                log_decide_next=lambda **_kwargs: {"event_id": "ev_1", "kind": "decide_next"},
                segment_add=lambda item: calls["segment"].append(dict(item)),
                persist_segment_state=lambda: calls.__setitem__("persist", int(calls["persist"]) + 1),
                apply_set_testless_strategy_overlay_update=lambda **_kwargs: None,
                handle_learn_suggested=lambda **_kwargs: None,
                emit_prefixed=lambda _p, _t: None,
            ),
        )

        self.assertEqual(out.status, "done")
        self.assertEqual(out.next_action, "stop")
        self.assertEqual(len(calls["segment"]), 1)
        self.assertEqual(calls["persist"], 1)


if __name__ == "__main__":
    unittest.main()

