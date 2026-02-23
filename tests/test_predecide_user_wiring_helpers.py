from __future__ import annotations

import unittest

from mi.runtime.wiring.predecide_user import (
    PredecideUserWiringDeps,
    handle_auto_answer_needs_user_wired,
    prompt_user_then_queue_wired,
)


class PredecideUserWiringHelpersTests(unittest.TestCase):
    def test_handle_auto_answer_needs_user_wired_plumbs_context_and_queues(self) -> None:
        calls: dict[str, object] = {}
        queued: list[dict[str, object]] = []
        aa_records: list[dict[str, object]] = []

        def _maybe_recall(**kwargs: object) -> None:
            calls["recall_kwargs"] = dict(kwargs)

        def _prompt_builder(**kwargs: object) -> str:
            calls["prompt_kwargs"] = dict(kwargs)
            return "prompt"

        def _mind_call(**kwargs: object):
            calls["mind_kwargs"] = dict(kwargs)
            return (
                {"should_answer": True, "needs_user_input": False, "hands_answer_input": "answer"},
                "mind_ref_1",
                "ok",
            )

        deps = PredecideUserWiringDeps(
            task="task",
            hands_provider="codex",
            runtime_cfg_getter=lambda: {"runtime": True},
            project_overlay={"k": "v"},
            recent_evidence=[{"kind": "e"}],
            empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
            maybe_cross_project_recall=_maybe_recall,
            auto_answer_prompt_builder=_prompt_builder,
            mind_call=_mind_call,
            append_auto_answer_record=lambda **kwargs: aa_records.append(dict(kwargs)) or {},
            get_check_input=lambda obj: str((obj or {}).get("hands_check_input") or "").strip(),
            join_hands_inputs=lambda a, b: ((a + "\n" + b).strip() if (a or b) else ""),
            queue_next_input=lambda **kwargs: queued.append(dict(kwargs)) or True,
            read_user_answer=lambda _q: (_ for _ in ()).throw(AssertionError("should not prompt user when queued")),
            append_user_input_record=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not record user input when queued")),
            set_blocked=lambda _note: (_ for _ in ()).throw(AssertionError("should not block when queued")),
        )

        out, checks_out = handle_auto_answer_needs_user_wired(
            batch_idx=3,
            hands_last="hands last",
            repo_obs={"r": 1},
            tdb_ctx_batch_obj={"claims": []},
            checks_obj={"should_run_checks": True, "hands_check_input": "pytest -q"},
            auto_answer_obj={"needs_user_input": True, "ask_user_question": "Need input?"},
            deps=deps,
        )

        self.assertTrue(out)
        self.assertEqual(checks_out, {"should_run_checks": True, "hands_check_input": "pytest -q"})
        self.assertEqual(len(aa_records), 1)
        self.assertEqual(len(queued), 1)

        recall_kwargs = calls.get("recall_kwargs")
        self.assertIsInstance(recall_kwargs, dict)
        self.assertEqual(recall_kwargs.get("batch_id"), "b3.before_user_recall")
        self.assertEqual(recall_kwargs.get("reason"), "before_ask_user")
        self.assertEqual(recall_kwargs.get("query"), "Need input?\ntask")

        prompt_kwargs = calls.get("prompt_kwargs")
        self.assertIsInstance(prompt_kwargs, dict)
        self.assertEqual(prompt_kwargs.get("task"), "task")
        self.assertEqual(prompt_kwargs.get("hands_provider"), "codex")
        self.assertEqual(prompt_kwargs.get("runtime_cfg"), {"runtime": True})
        self.assertEqual(prompt_kwargs.get("project_overlay"), {"k": "v"})
        self.assertEqual(prompt_kwargs.get("thought_db_context"), {"claims": []})
        self.assertEqual(prompt_kwargs.get("repo_observation"), {"r": 1})
        self.assertEqual(prompt_kwargs.get("check_plan"), {"should_run_checks": True, "hands_check_input": "pytest -q"})
        self.assertEqual(prompt_kwargs.get("recent_evidence"), [{"kind": "e"}])
        self.assertEqual(prompt_kwargs.get("hands_last_message"), "Need input?")

        mind_kwargs = calls.get("mind_kwargs")
        self.assertIsInstance(mind_kwargs, dict)
        self.assertEqual(mind_kwargs.get("schema_filename"), "auto_answer_to_hands.json")
        self.assertEqual(mind_kwargs.get("prompt"), "prompt")
        self.assertEqual(mind_kwargs.get("tag"), "autoanswer_retry_after_recall_b3")
        self.assertEqual(mind_kwargs.get("batch_id"), "b3.after_recall")

        queued_kwargs = queued[0]
        self.assertEqual(queued_kwargs.get("nxt"), "answer\npytest -q")
        self.assertEqual(queued_kwargs.get("hands_last_message"), "hands last")
        self.assertEqual(queued_kwargs.get("batch_id"), "b3.after_recall")
        self.assertEqual(queued_kwargs.get("reason"), "auto-answered after cross-project recall")
        self.assertEqual(queued_kwargs.get("repo_observation"), {"r": 1})
        self.assertEqual(queued_kwargs.get("thought_db_context"), {"claims": []})
        self.assertEqual(queued_kwargs.get("check_plan"), {"should_run_checks": True, "hands_check_input": "pytest -q"})

    def test_prompt_user_then_queue_wired_records_and_queues(self) -> None:
        queued: list[dict[str, object]] = []
        user_records: list[dict[str, object]] = []

        deps = PredecideUserWiringDeps(
            task="task",
            hands_provider="codex",
            runtime_cfg_getter=lambda: {},
            project_overlay={},
            recent_evidence=[],
            empty_auto_answer=lambda: {},
            maybe_cross_project_recall=lambda **_kwargs: None,
            auto_answer_prompt_builder=lambda **_kwargs: "",
            mind_call=lambda **_kwargs: ({}, "", "ok"),
            append_auto_answer_record=lambda **_kwargs: {},
            get_check_input=lambda _obj: "",
            join_hands_inputs=lambda a, b: ((a + "\n" + b).strip() if (a or b) else ""),
            queue_next_input=lambda **kwargs: queued.append(dict(kwargs)) or True,
            read_user_answer=lambda q: ("user-answer" if q == "Need input?" else ""),
            append_user_input_record=lambda **kwargs: user_records.append(dict(kwargs)) or {},
            set_blocked=lambda _note: (_ for _ in ()).throw(AssertionError("should not block when user answers")),
        )

        out = prompt_user_then_queue_wired(
            batch_idx=1,
            question="Need input?",
            hands_last="hands last",
            repo_obs={"r": 2},
            checks_obj={"should_run_checks": False, "hands_check_input": ""},
            tdb_ctx_batch_obj={"claims": []},
            deps=deps,
        )

        self.assertTrue(out)
        self.assertEqual(len(user_records), 1)
        self.assertEqual(user_records[0].get("batch_id"), "b1")
        self.assertEqual(user_records[0].get("question"), "Need input?")
        self.assertEqual(user_records[0].get("answer"), "user-answer")
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].get("batch_id"), "b1")
        self.assertEqual(queued[0].get("reason"), "answered after user input")


if __name__ == "__main__":
    unittest.main()

