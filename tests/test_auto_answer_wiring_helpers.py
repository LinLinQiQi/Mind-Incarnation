from __future__ import annotations

import unittest

from mi.runtime.wiring.auto_answer import (
    AutoAnswerQueryWiringDeps,
    query_auto_answer_to_hands_wired,
)


class AutoAnswerWiringHelpersTests(unittest.TestCase):
    def test_query_auto_answer_to_hands_wired_plumbs_runner_context(self) -> None:
        calls: dict[str, object] = {}

        def _prompt_builder(**kwargs: object) -> str:
            calls["prompt_kwargs"] = dict(kwargs)
            return "aa-prompt"

        def _mind_call(**kwargs: object):
            calls["mind_kwargs"] = dict(kwargs)
            return (
                {"should_answer": True, "hands_answer_input": "answer", "needs_user_input": False},
                "mind_ref",
                "ok",
            )

        deps = AutoAnswerQueryWiringDeps(
            task="task1",
            hands_provider="codex",
            runtime_cfg_getter=lambda: {"runtime": True},
            project_overlay={"overlay": True},
            recent_evidence=[{"kind": "e"}],
            auto_answer_prompt_builder=_prompt_builder,
            mind_call=_mind_call,
            empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
        )

        out, ref, state = query_auto_answer_to_hands_wired(
            batch_idx=3,
            batch_id="b3",
            hands_last="Need input?",
            repo_obs={"dirty": True},
            checks_obj={"should_run_checks": True, "hands_check_input": "pytest -q"},
            tdb_ctx_batch_obj={"claims": []},
            deps=deps,
        )

        self.assertEqual(state, "ok")
        self.assertEqual(ref, "mind_ref")
        self.assertEqual(out.get("should_answer"), True)

        prompt_kwargs = calls.get("prompt_kwargs")
        self.assertIsInstance(prompt_kwargs, dict)
        self.assertEqual(prompt_kwargs.get("task"), "task1")
        self.assertEqual(prompt_kwargs.get("hands_provider"), "codex")
        self.assertEqual(prompt_kwargs.get("runtime_cfg"), {"runtime": True})
        self.assertEqual(prompt_kwargs.get("project_overlay"), {"overlay": True})
        self.assertEqual(prompt_kwargs.get("thought_db_context"), {"claims": []})
        self.assertEqual(prompt_kwargs.get("repo_observation"), {"dirty": True})
        self.assertEqual(prompt_kwargs.get("check_plan"), {"should_run_checks": True, "hands_check_input": "pytest -q"})
        self.assertEqual(prompt_kwargs.get("recent_evidence"), [{"kind": "e"}])
        self.assertEqual(prompt_kwargs.get("hands_last_message"), "Need input?")

        mind_kwargs = calls.get("mind_kwargs")
        self.assertIsInstance(mind_kwargs, dict)
        self.assertEqual(mind_kwargs.get("schema_filename"), "auto_answer_to_hands.json")
        self.assertEqual(mind_kwargs.get("prompt"), "aa-prompt")
        self.assertEqual(mind_kwargs.get("tag"), "autoanswer_b3")
        self.assertEqual(mind_kwargs.get("batch_id"), "b3")


if __name__ == "__main__":
    unittest.main()

