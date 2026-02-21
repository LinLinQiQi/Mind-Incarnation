from __future__ import annotations

import unittest

from mi.runtime.autopilot.ask_user_flow import (
    AskUserAutoAnswerAttemptDeps,
    DecideAskUserFlowDeps,
    ask_user_auto_answer_attempt,
    handle_decide_next_ask_user,
)


class AskUserFlowHelpersTests(unittest.TestCase):
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
            mindspec_base={},
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
            mindspec_base={},
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


if __name__ == "__main__":
    unittest.main()
