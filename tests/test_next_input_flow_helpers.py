from __future__ import annotations

import unittest

from mi.runtime.autopilot.next_input_flow import LoopGuardDeps, apply_loop_guard


class NextInputFlowHelpersTests(unittest.TestCase):
    def test_no_pattern_passes_through(self) -> None:
        out = apply_loop_guard(
            candidate="run tests",
            hands_last_message="ok",
            batch_id="b1",
            reason="r",
            sent_sigs=[],
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            repo_observation={},
            check_plan={},
            evidence_window=[],
            thread_id="tid_1",
            deps=LoopGuardDeps(
                loop_sig=lambda **kwargs: "s1",
                loop_pattern=lambda sigs: "",
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
                evidence_append=lambda rec: rec,
                append_segment_record=lambda rec: None,
                resolve_ask_when_uncertain=lambda: True,
                loop_break_prompt_builder=lambda **kwargs: "p",
                mind_call=lambda **kwargs: ({}, "", "ok"),
                loop_break_get_checks_input=lambda **kwargs: ("", ""),
                read_user_answer=lambda q: "y",
                append_user_input_record=lambda **kwargs: None,
            ),
        )
        self.assertTrue(out.proceed)
        self.assertEqual(out.candidate, "run tests")
        self.assertEqual(out.sent_sigs, ["s1"])

    def test_stop_blocked_action(self) -> None:
        evw: list[dict[str, object]] = []
        out = apply_loop_guard(
            candidate="next",
            hands_last_message="h",
            batch_id="b2",
            reason="loop",
            sent_sigs=[],
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            repo_observation={},
            check_plan={},
            evidence_window=evw,
            thread_id="tid_1",
            deps=LoopGuardDeps(
                loop_sig=lambda **kwargs: "s2",
                loop_pattern=lambda sigs: "aaa",
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
                evidence_append=lambda rec: rec,
                append_segment_record=lambda rec: None,
                resolve_ask_when_uncertain=lambda: True,
                loop_break_prompt_builder=lambda **kwargs: "p",
                mind_call=lambda **kwargs: ({"action": "stop_blocked"}, "mref", "ok"),
                loop_break_get_checks_input=lambda **kwargs: ("", ""),
                read_user_answer=lambda q: "y",
                append_user_input_record=lambda **kwargs: None,
            ),
        )
        self.assertFalse(out.proceed)
        self.assertEqual(out.status, "blocked")
        self.assertIn("loop_break: stop_blocked", out.notes)

    def test_run_checks_then_continue_uses_checks_input(self) -> None:
        out = apply_loop_guard(
            candidate="next",
            hands_last_message="h",
            batch_id="b3",
            reason="loop",
            sent_sigs=["x"],
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            repo_observation={},
            check_plan={},
            evidence_window=[],
            thread_id="tid_1",
            deps=LoopGuardDeps(
                loop_sig=lambda **kwargs: "s3",
                loop_pattern=lambda sigs: "aaa",
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
                evidence_append=lambda rec: rec,
                append_segment_record=lambda rec: None,
                resolve_ask_when_uncertain=lambda: True,
                loop_break_prompt_builder=lambda **kwargs: "p",
                mind_call=lambda **kwargs: ({"action": "run_checks_then_continue"}, "mref", "ok"),
                loop_break_get_checks_input=lambda **kwargs: ("run smoke", ""),
                read_user_answer=lambda q: "y",
                append_user_input_record=lambda **kwargs: None,
            ),
        )
        self.assertTrue(out.proceed)
        self.assertEqual(out.candidate, "run smoke")
        self.assertEqual(out.sent_sigs, [])

    def test_ask_user_path_with_override(self) -> None:
        calls = {"user_input": 0}
        out = apply_loop_guard(
            candidate="next",
            hands_last_message="h",
            batch_id="b4",
            reason="loop",
            sent_sigs=[],
            task="task",
            hands_provider="codex",
            mindspec_base={},
            project_overlay={},
            thought_db_context={},
            repo_observation={},
            check_plan={},
            evidence_window=[],
            thread_id="tid_1",
            deps=LoopGuardDeps(
                loop_sig=lambda **kwargs: "s4",
                loop_pattern=lambda sigs: "aaa",
                now_ts=lambda: "2026-01-01T00:00:00Z",
                truncate=lambda s, n: s[:n],
                evidence_append=lambda rec: rec,
                append_segment_record=lambda rec: None,
                resolve_ask_when_uncertain=lambda: True,
                loop_break_prompt_builder=lambda **kwargs: "p",
                mind_call=lambda **kwargs: ({"action": "ask_user", "ask_user_question": "q?"}, "mref", "ok"),
                loop_break_get_checks_input=lambda **kwargs: ("", ""),
                read_user_answer=lambda q: "new instruction",
                append_user_input_record=lambda **kwargs: calls.__setitem__("user_input", calls["user_input"] + 1),
            ),
        )
        self.assertTrue(out.proceed)
        self.assertEqual(out.candidate, "new instruction")
        self.assertEqual(out.sent_sigs, [])
        self.assertEqual(calls["user_input"], 1)


if __name__ == "__main__":
    unittest.main()
