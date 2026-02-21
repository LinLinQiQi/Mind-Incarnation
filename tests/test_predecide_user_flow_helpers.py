from __future__ import annotations

import unittest

from mi.runtime.autopilot.predecide_user_flow import (
    PredecideNeedsUserDeps,
    PredecidePromptUserDeps,
    PredecideQueueWithChecksDeps,
    PredecideRetryAutoAnswerDeps,
    handle_auto_answer_needs_user,
    prompt_user_then_queue,
    retry_auto_answer_after_recall,
    try_queue_answer_with_checks,
)


class PredecideUserFlowHelpersTests(unittest.TestCase):
    def test_retry_auto_answer_after_recall_updates_question(self) -> None:
        records: list[dict[str, object]] = []

        aa, q = retry_auto_answer_after_recall(
            batch_idx=3,
            question="old question",
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            tdb_ctx_batch_obj={},
            repo_obs={},
            checks_obj={},
            recent_evidence=[],
            deps=PredecideRetryAutoAnswerDeps(
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
                maybe_cross_project_recall=lambda **_kwargs: None,
                auto_answer_prompt_builder=lambda **_kwargs: "prompt",
                mind_call=lambda **_kwargs: (
                    {"should_answer": False, "needs_user_input": True, "ask_user_question": "new question"},
                    "mind_ref",
                    "ok",
                ),
                append_auto_answer_record=lambda **kwargs: records.append(dict(kwargs)) or {},
            ),
        )
        self.assertEqual(q, "new question")
        self.assertTrue(bool(aa.get("needs_user_input")))
        self.assertEqual(len(records), 1)

    def test_try_queue_answer_with_checks_variants(self) -> None:
        out_none = try_queue_answer_with_checks(
            batch_id="b1",
            queue_reason="r",
            answer_text="",
            hands_last="h",
            repo_obs={},
            checks_obj={},
            tdb_ctx_batch_obj={},
            deps=PredecideQueueWithChecksDeps(
                get_check_input=lambda _obj: "",
                join_hands_inputs=lambda _a, _b: "",
                queue_next_input=lambda **_kwargs: True,
            ),
        )
        self.assertIsNone(out_none)

        out_true = try_queue_answer_with_checks(
            batch_id="b1",
            queue_reason="r",
            answer_text="answer",
            hands_last="h",
            repo_obs={},
            checks_obj={"hands_check_input": "pytest -q"},
            tdb_ctx_batch_obj={},
            deps=PredecideQueueWithChecksDeps(
                get_check_input=lambda obj: str((obj or {}).get("hands_check_input") or ""),
                join_hands_inputs=lambda a, b: ((a + "\n" + b).strip() if (a or b) else ""),
                queue_next_input=lambda **_kwargs: True,
            ),
        )
        self.assertTrue(out_true)

    def test_prompt_user_then_queue_blocks_on_empty(self) -> None:
        blocked: list[str] = []
        out = prompt_user_then_queue(
            batch_idx=9,
            question="Need input?",
            hands_last="h",
            repo_obs={},
            checks_obj={},
            tdb_ctx_batch_obj={},
            deps=PredecidePromptUserDeps(
                read_user_answer=lambda _q: "",
                append_user_input_record=lambda **_kwargs: {},
                set_blocked=lambda note: blocked.append(str(note)),
                try_queue_answer_with_checks=lambda **_kwargs: True,
            ),
        )
        self.assertFalse(out)
        self.assertEqual(blocked, ["user did not provide required input"])

    def test_handle_auto_answer_needs_user_prompt_path(self) -> None:
        asked = {"n": 0}

        out, checks = handle_auto_answer_needs_user(
            batch_idx=2,
            hands_last="hands last",
            repo_obs={},
            tdb_ctx_batch_obj={},
            checks_obj={"x": 1},
            auto_answer_obj={"needs_user_input": True, "ask_user_question": "q"},
            deps=PredecideNeedsUserDeps(
                retry_auto_answer_after_recall=lambda **_kwargs: (
                    {"should_answer": False, "needs_user_input": True, "ask_user_question": "q2"},
                    "q2",
                ),
                try_queue_answer_with_checks=lambda **_kwargs: None,
                prompt_user_then_queue=lambda **_kwargs: asked.__setitem__("n", asked["n"] + 1) or True,
            ),
        )
        self.assertTrue(out)
        self.assertEqual(asked["n"], 1)
        self.assertEqual(checks, {"x": 1})


if __name__ == "__main__":
    unittest.main()
