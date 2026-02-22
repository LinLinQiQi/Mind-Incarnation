from __future__ import annotations

import unittest

from mi.runtime.wiring.ask_user import (
    AskUserAutoAnswerAttemptWiringDeps,
    DecideAskUserWiringDeps,
    ask_user_auto_answer_attempt_wired,
    handle_decide_next_ask_user_wired,
)


class AskUserWiringHelpersTests(unittest.TestCase):
    def test_auto_answer_attempt_wired_plumbs_context(self) -> None:
        calls: dict[str, object] = {}
        queued: list[dict[str, object]] = []
        records: list[dict[str, object]] = []

        def _prompt_builder(**kwargs):
            calls["prompt_kwargs"] = dict(kwargs)
            return "prompt"

        def _mind_call(**kwargs):
            calls["mind_kwargs"] = dict(kwargs)
            return (
                {"should_answer": True, "hands_answer_input": "answer", "needs_user_input": False},
                "mind_ref_1",
                "ok",
            )

        deps = AskUserAutoAnswerAttemptWiringDeps(
            task="task1",
            hands_provider="codex",
            mindspec_base_getter=lambda: {"x": 1},
            project_overlay={"o": 1},
            recent_evidence=[{"kind": "e"}],
            empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
            build_thought_db_context_obj=lambda _hlm, _recs: {"claims": []},
            auto_answer_prompt_builder=_prompt_builder,
            mind_call=_mind_call,
            append_auto_answer_record=lambda **kwargs: records.append(dict(kwargs)) or {},
            get_check_input=lambda obj: str((obj or {}).get("hands_check_input") or "").strip(),
            join_hands_inputs=lambda a, b: ((a + "\n" + b).strip() if (a or b) else ""),
            queue_next_input=lambda **kwargs: queued.append(dict(kwargs)) or True,
        )

        out, q = ask_user_auto_answer_attempt_wired(
            batch_idx=2,
            q="Need info?",
            hands_last="hands",
            repo_obs={"r": 1},
            checks_obj={"should_run_checks": True, "hands_check_input": "pytest -q"},
            tdb_ctx_obj={"claims": []},
            batch_suffix="from_decide",
            tag_suffix="autoanswer_from_decide",
            queue_reason="reason",
            note_skipped="skipped",
            note_error="error",
            deps=deps,
        )

        self.assertTrue(out)
        self.assertEqual(q, "Need info?")
        self.assertEqual(len(records), 1)
        self.assertEqual(len(queued), 1)

        prompt_kwargs = calls.get("prompt_kwargs")
        self.assertIsInstance(prompt_kwargs, dict)
        self.assertEqual(prompt_kwargs.get("task"), "task1")
        self.assertEqual(prompt_kwargs.get("hands_provider"), "codex")
        self.assertEqual(prompt_kwargs.get("mindspec_base"), {"x": 1})
        self.assertEqual(prompt_kwargs.get("project_overlay"), {"o": 1})

        mind_kwargs = calls.get("mind_kwargs")
        self.assertIsInstance(mind_kwargs, dict)
        self.assertEqual(mind_kwargs.get("schema_filename"), "auto_answer_to_hands.json")
        self.assertEqual(mind_kwargs.get("tag"), "autoanswer_from_decide_b2")
        self.assertEqual(mind_kwargs.get("batch_id"), "b2.from_decide")

    def test_handle_decide_next_ask_user_wired_passes_through(self) -> None:
        blocked: list[str] = []
        attempts = {"n": 0}

        def _attempt(**kwargs):
            attempts["n"] += 1
            return None, str(kwargs.get("q") or "")

        out = handle_decide_next_ask_user_wired(
            batch_idx=5,
            task="task",
            hands_last="hands",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            decision_obj={"ask_user_question": "Need details?"},
            deps=DecideAskUserWiringDeps(
                maybe_cross_project_recall=lambda **_kwargs: None,
                read_user_answer=lambda _q: "",
                append_user_input_record=lambda **_kwargs: {},
                set_blocked=lambda note: blocked.append(str(note)),
                run_auto_answer_attempt=_attempt,
                redecide_with_input=lambda **_kwargs: True,
            ),
        )

        self.assertFalse(out)
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(blocked, ["user did not provide required input"])


if __name__ == "__main__":
    unittest.main()

