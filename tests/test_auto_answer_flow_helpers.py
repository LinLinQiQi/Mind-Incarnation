from __future__ import annotations

import unittest

from mi.runtime.autopilot.auto_answer_flow import AutoAnswerQueryDeps, query_auto_answer_to_hands


class AutoAnswerFlowHelpersTests(unittest.TestCase):
    def test_query_passes_prompt_and_mind_call_args(self) -> None:
        seen: dict[str, object] = {}

        def _prompt_builder(**kwargs):
            seen["prompt_kwargs"] = kwargs
            return "aa-prompt"

        def _mind_call(**kwargs):
            seen["mind_kwargs"] = kwargs
            return {"should_answer": True, "answer": "done"}, "mind_aa_1", "ok"

        out, ref, state = query_auto_answer_to_hands(
            batch_idx=3,
            batch_id="b3",
            task="Do checks",
            hands_provider="codex",
            mindspec_base={"ask": "auto"},
            project_overlay={"rules": []},
            thought_db_context={"claims": []},
            repo_observation={"status": "dirty"},
            check_plan={"should_run_checks": True},
            recent_evidence=[{"kind": "evidence"}],
            hands_last_message="Should I continue?",
            deps=AutoAnswerQueryDeps(
                auto_answer_prompt_builder=_prompt_builder,
                mind_call=_mind_call,
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
            ),
        )
        self.assertEqual(out.get("should_answer"), True)
        self.assertEqual(ref, "mind_aa_1")
        self.assertEqual(state, "ok")
        self.assertEqual(seen["mind_kwargs"]["schema_filename"], "auto_answer_to_hands.json")
        self.assertEqual(seen["mind_kwargs"]["tag"], "autoanswer_b3")
        self.assertEqual(seen["mind_kwargs"]["batch_id"], "b3")
        self.assertEqual(seen["mind_kwargs"]["prompt"], "aa-prompt")
        self.assertEqual(seen["prompt_kwargs"]["task"], "Do checks")

    def test_query_fallback_when_mind_skipped(self) -> None:
        out, ref, state = query_auto_answer_to_hands(
            batch_idx=1,
            batch_id="b1",
            task="T",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            repo_observation={},
            check_plan={},
            recent_evidence=[],
            hands_last_message="?",
            deps=AutoAnswerQueryDeps(
                auto_answer_prompt_builder=lambda **_kwargs: "p",
                mind_call=lambda **_kwargs: (None, "mind_x", "skipped"),
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
            ),
        )
        self.assertEqual(ref, "mind_x")
        self.assertEqual(state, "skipped")
        self.assertEqual(out.get("notes"), "skipped: mind_circuit_open (auto_answer_to_hands)")

    def test_query_fallback_when_mind_error(self) -> None:
        out, ref, state = query_auto_answer_to_hands(
            batch_idx=2,
            batch_id="b2",
            task="T",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            repo_observation={},
            check_plan={},
            recent_evidence=[],
            hands_last_message="?",
            deps=AutoAnswerQueryDeps(
                auto_answer_prompt_builder=lambda **_kwargs: "p",
                mind_call=lambda **_kwargs: (None, "mind_y", "error"),
                empty_auto_answer=lambda: {"should_answer": False, "needs_user_input": False, "confidence": 0.0},
            ),
        )
        self.assertEqual(ref, "mind_y")
        self.assertEqual(state, "error")
        self.assertEqual(
            out.get("notes"),
            "mind_error: auto_answer_to_hands failed; see EvidenceLog kind=mind_error",
        )


if __name__ == "__main__":
    unittest.main()
