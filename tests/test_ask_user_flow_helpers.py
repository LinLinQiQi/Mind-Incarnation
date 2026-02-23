from __future__ import annotations

import unittest

from mi.runtime.autopilot.ask_user_flow import (
    AskUserAutoAnswerAttemptDeps,
    AskUserRedecideDeps,
    DecideAskUserFlowDeps,
    ask_user_redecide_with_input,
    ask_user_auto_answer_attempt,
    handle_decide_next_ask_user,
)


class AskUserFlowHelpersTests(unittest.TestCase):
    class _Ctx:
        def __init__(self, obj: dict[str, object]) -> None:
            self._obj = dict(obj)

        def to_prompt_obj(self) -> dict[str, object]:
            return dict(self._obj)

    def test_auto_answer_attempt_can_queue_and_short_circuit(self) -> None:
        records: list[dict[str, object]] = []
        queued: list[dict[str, object]] = []

        out, q = ask_user_auto_answer_attempt(
            batch_idx=2,
            q="Need info?",
            hands_last="h",
            repo_obs={},
            checks_obj={"should_run_checks": True, "hands_check_input": "pytest -q"},
            tdb_ctx_obj={"claims": []},
            batch_suffix="from_decide",
            tag_suffix="autoanswer_from_decide",
            queue_reason="reason",
            note_skipped="skipped note",
            note_error="error note",
            task="task",
            hands_provider="codex",
            runtime_cfg={},
            project_overlay={},
            recent_evidence=[],
            deps=AskUserAutoAnswerAttemptDeps(
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
                build_thought_db_context_obj=lambda _hlm, _recs: {"claims": []},
                auto_answer_prompt_builder=lambda **_kwargs: "prompt",
                mind_call=lambda **_kwargs: (
                    {"should_answer": True, "hands_answer_input": "answer", "needs_user_input": False},
                    "mind_ref",
                    "ok",
                ),
                append_auto_answer_record=lambda **kwargs: records.append(dict(kwargs)) or {},
                get_check_input=lambda obj: str((obj or {}).get("hands_check_input") or ""),
                join_hands_inputs=lambda a, b: ((a + "\n" + b).strip() if (a or b) else ""),
                queue_next_input=lambda **kwargs: queued.append(dict(kwargs)) or True,
            ),
        )
        self.assertTrue(out)
        self.assertEqual(q, "Need info?")
        self.assertEqual(len(records), 1)
        self.assertEqual(len(queued), 1)

    def test_auto_answer_attempt_can_update_question_from_model(self) -> None:
        out, q = ask_user_auto_answer_attempt(
            batch_idx=1,
            q="Old q",
            hands_last="h",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            batch_suffix="x",
            tag_suffix="y",
            queue_reason="r",
            note_skipped="skipped note",
            note_error="error note",
            task="task",
            hands_provider="codex",
            runtime_cfg={},
            project_overlay={},
            recent_evidence=[],
            deps=AskUserAutoAnswerAttemptDeps(
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
                build_thought_db_context_obj=lambda _hlm, _recs: {"claims": []},
                auto_answer_prompt_builder=lambda **_kwargs: "prompt",
                mind_call=lambda **_kwargs: (
                    {"should_answer": False, "needs_user_input": True, "ask_user_question": "New q"},
                    "mind_ref",
                    "ok",
                ),
                append_auto_answer_record=lambda **_kwargs: {},
                get_check_input=lambda _obj: "",
                join_hands_inputs=lambda _a, _b: "",
                queue_next_input=lambda **_kwargs: False,
            ),
        )
        self.assertIsNone(out)
        self.assertEqual(q, "New q")

    def test_handle_decide_next_ask_user_empty_user_answer_blocks(self) -> None:
        blocked: list[str] = []
        attempts = {"n": 0}

        def _attempt(**kwargs):
            attempts["n"] += 1
            return None, str(kwargs.get("q") or "")

        out = handle_decide_next_ask_user(
            batch_idx=5,
            task="task",
            hands_last="hands",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            decision_obj={"ask_user_question": "Need details?"},
            deps=DecideAskUserFlowDeps(
                run_auto_answer_attempt=_attempt,
                maybe_cross_project_recall=lambda **_kwargs: None,
                read_user_answer=lambda _q: "",
                append_user_input_record=lambda **_kwargs: {},
                redecide_with_input=lambda **_kwargs: True,
                set_blocked=lambda note: blocked.append(str(note)),
            ),
        )
        self.assertFalse(out)
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(blocked, ["user did not provide required input"])

    def test_handle_decide_next_ask_user_redecides_after_answer(self) -> None:
        calls = {"redecide": 0}

        out = handle_decide_next_ask_user(
            batch_idx=7,
            task="task",
            hands_last="hands",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            decision_obj={"ask_user_question": "Need details?"},
            deps=DecideAskUserFlowDeps(
                run_auto_answer_attempt=lambda **kwargs: (None, str(kwargs.get("q") or "")),
                maybe_cross_project_recall=lambda **_kwargs: None,
                read_user_answer=lambda _q: "yes",
                append_user_input_record=lambda **_kwargs: {},
                redecide_with_input=lambda **_kwargs: calls.__setitem__("redecide", calls["redecide"] + 1) or True,
                set_blocked=lambda _note: None,
            ),
        )
        self.assertTrue(out)
        self.assertEqual(calls["redecide"], 1)

    def test_ask_user_redecide_with_input_fallbacks_to_queue(self) -> None:
        queued: list[dict[str, object]] = []
        statuses: list[str] = []
        notes: list[str] = []

        cont, rec = ask_user_redecide_with_input(
            batch_idx=2,
            task="task",
            hands_provider="codex",
            runtime_cfg={},
            project_overlay={},
            workflow_run={},
            workflow_load_effective=lambda: [],
            recent_evidence=[],
            hands_last="hands asks",
            repo_obs={},
            checks_obj={"hands_check_input": "pytest -q"},
            answer="user answer",
            deps=AskUserRedecideDeps(
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
                build_decide_context=lambda **_kwargs: self._Ctx({"claims": []}),
                summarize_thought_db_context=lambda _ctx: {"summary": "ok"},
                decide_next_prompt_builder=lambda **_kwargs: "prompt",
                load_active_workflow=lambda **_kwargs: {},
                mind_call=lambda **_kwargs: (None, "mind_ref", "skipped"),
                log_decide_next=lambda **_kwargs: None,
                append_decide_record=lambda _rec: None,
                apply_set_testless_strategy_overlay_update=lambda **_kwargs: None,
                handle_learn_suggested=lambda **_kwargs: None,
                get_check_input=lambda obj: str((obj or {}).get("hands_check_input") or ""),
                join_hands_inputs=lambda a, b: (a + "\n" + b).strip(),
                queue_next_input=lambda **kwargs: queued.append(dict(kwargs)) or True,
                set_status=lambda s: statuses.append(str(s)),
                set_notes=lambda n: notes.append(str(n)),
            ),
        )

        self.assertTrue(cont)
        self.assertIsNone(rec)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].get("batch_id"), "b2.after_user")
        self.assertEqual(queued[0].get("reason"), "mind_circuit_open(decide_next after user): send user answer")
        self.assertEqual(statuses, [])
        self.assertEqual(notes, [])

    def test_ask_user_redecide_with_input_send_to_hands(self) -> None:
        queued: list[dict[str, object]] = []
        statuses: list[str] = []
        notes: list[str] = []
        appended: list[dict[str, object]] = []
        overlay_calls: list[dict[str, object]] = []
        learn_calls: list[dict[str, object]] = []

        cont, rec = ask_user_redecide_with_input(
            batch_idx=4,
            task="task",
            hands_provider="codex",
            runtime_cfg={},
            project_overlay={},
            workflow_run={},
            workflow_load_effective=lambda: [],
            recent_evidence=[{"kind": "hands_result"}],
            hands_last="hands asks",
            repo_obs={},
            checks_obj={},
            answer="user answer",
            deps=AskUserRedecideDeps(
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
                build_decide_context=lambda **_kwargs: self._Ctx({"claims": []}),
                summarize_thought_db_context=lambda _ctx: {"summary": "ok"},
                decide_next_prompt_builder=lambda **_kwargs: "prompt",
                load_active_workflow=lambda **_kwargs: {"id": "wf_1"},
                mind_call=lambda **_kwargs: (
                    {
                        "next_action": "send_to_hands",
                        "next_hands_input": "continue",
                        "status": "not_done",
                        "notes": "planned",
                        "update_project_overlay": {"set_testless_strategy": "manual_review"},
                        "learn_suggested": [{"kind": "x"}],
                    },
                    "mind_ref",
                    "ok",
                ),
                log_decide_next=lambda **_kwargs: {"event_id": "ev_1"},
                append_decide_record=lambda rec_obj: appended.append(dict(rec_obj)),
                apply_set_testless_strategy_overlay_update=lambda **kwargs: overlay_calls.append(dict(kwargs)),
                handle_learn_suggested=lambda **kwargs: learn_calls.append(dict(kwargs)),
                get_check_input=lambda _obj: "",
                join_hands_inputs=lambda a, b: (a + "\n" + b).strip(),
                queue_next_input=lambda **kwargs: queued.append(dict(kwargs)) or True,
                set_status=lambda s: statuses.append(str(s)),
                set_notes=lambda n: notes.append(str(n)),
            ),
        )

        self.assertTrue(cont)
        self.assertEqual((rec or {}).get("event_id"), "ev_1")
        self.assertEqual(statuses[-1], "not_done")
        self.assertEqual(notes[-1], "planned")
        self.assertEqual(len(appended), 1)
        self.assertEqual(len(overlay_calls), 1)
        self.assertEqual(len(learn_calls), 1)
        self.assertEqual(queued[0].get("reason"), "send_to_hands after user input")

    def test_ask_user_redecide_with_input_unexpected_action_blocks(self) -> None:
        statuses: list[str] = []
        notes: list[str] = []

        cont, rec = ask_user_redecide_with_input(
            batch_idx=5,
            task="task",
            hands_provider="codex",
            runtime_cfg={},
            project_overlay={},
            workflow_run={},
            workflow_load_effective=lambda: [],
            recent_evidence=[],
            hands_last="hands asks",
            repo_obs={},
            checks_obj={},
            answer="user answer",
            deps=AskUserRedecideDeps(
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
                build_decide_context=lambda **_kwargs: self._Ctx({"claims": []}),
                summarize_thought_db_context=lambda _ctx: {"summary": "ok"},
                decide_next_prompt_builder=lambda **_kwargs: "prompt",
                load_active_workflow=lambda **_kwargs: {},
                mind_call=lambda **_kwargs: (
                    {"next_action": "ask_user", "status": "not_done", "notes": "x"},
                    "mind_ref",
                    "ok",
                ),
                log_decide_next=lambda **_kwargs: {"event_id": "ev_2"},
                append_decide_record=lambda _rec_obj: None,
                apply_set_testless_strategy_overlay_update=lambda **_kwargs: None,
                handle_learn_suggested=lambda **_kwargs: None,
                get_check_input=lambda _obj: "",
                join_hands_inputs=lambda a, b: (a + "\n" + b).strip(),
                queue_next_input=lambda **_kwargs: True,
                set_status=lambda s: statuses.append(str(s)),
                set_notes=lambda n: notes.append(str(n)),
            ),
        )

        self.assertFalse(cont)
        self.assertEqual((rec or {}).get("event_id"), "ev_2")
        self.assertEqual(statuses[-1], "blocked")
        self.assertEqual(notes[-1], "unexpected next_action=ask_user after user input")


if __name__ == "__main__":
    unittest.main()
