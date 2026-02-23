from __future__ import annotations

import unittest

from mi.runtime.autopilot.next_input_flow import (
    LoopGuardDeps,
    LoopGuardResult,
    QueueNextInputDeps,
    apply_loop_guard,
    queue_next_input,
)


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
            runtime_cfg={},
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
            runtime_cfg={},
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
            runtime_cfg={},
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
            runtime_cfg={},
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

    def test_queue_next_input_empty_blocks_without_invoking_deps(self) -> None:
        calls = {"loop_guard": 0, "checkpoint": 0}

        out = queue_next_input(
            nxt="",
            hands_last_message="h",
            batch_id="b1",
            reason="r",
            sent_sigs=["x"],
            deps=QueueNextInputDeps(
                loop_guard=lambda **_kwargs: calls.__setitem__("loop_guard", calls["loop_guard"] + 1)
                or LoopGuardResult(proceed=True, candidate="c", sent_sigs=[], status="", notes=""),
                checkpoint_before_continue=lambda **_kwargs: calls.__setitem__("checkpoint", calls["checkpoint"] + 1),
            ),
        )

        self.assertFalse(out.queued)
        self.assertEqual(out.status, "blocked")
        self.assertIn("empty next input", out.notes)
        self.assertEqual(out.sent_sigs, ["x"])
        self.assertEqual(calls, {"loop_guard": 0, "checkpoint": 0})

    def test_queue_next_input_loop_guard_block_propagates_status(self) -> None:
        calls = {"checkpoint": 0}

        out = queue_next_input(
            nxt="next",
            hands_last_message="h",
            batch_id="b2",
            reason="r",
            sent_sigs=["x"],
            deps=QueueNextInputDeps(
                loop_guard=lambda **_kwargs: LoopGuardResult(
                    proceed=False, candidate="next", sent_sigs=["s1"], status="done", notes="stopped"
                ),
                checkpoint_before_continue=lambda **_kwargs: calls.__setitem__("checkpoint", calls["checkpoint"] + 1),
            ),
        )

        self.assertFalse(out.queued)
        self.assertEqual(out.status, "done")
        self.assertEqual(out.notes, "stopped")
        self.assertEqual(out.sent_sigs, ["s1"])
        self.assertEqual(calls["checkpoint"], 0)

    def test_queue_next_input_success_runs_checkpoint_then_queues(self) -> None:
        calls: list[dict[str, object]] = []

        out = queue_next_input(
            nxt="next",
            hands_last_message="h",
            batch_id="b3",
            reason="continue",
            sent_sigs=["x"],
            deps=QueueNextInputDeps(
                loop_guard=lambda **_kwargs: LoopGuardResult(
                    proceed=True, candidate="queued", sent_sigs=["s2"], status="", notes=""
                ),
                checkpoint_before_continue=lambda **kwargs: calls.append(dict(kwargs)),
            ),
        )

        self.assertTrue(out.queued)
        self.assertEqual(out.next_input, "queued")
        self.assertEqual(out.status, "not_done")
        self.assertEqual(out.notes, "continue")
        self.assertEqual(out.sent_sigs, ["s2"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("batch_id"), "b3")
        self.assertEqual(calls[0].get("planned_next_input"), "queued")
        self.assertEqual(calls[0].get("status_hint"), "not_done")
        self.assertTrue(str(calls[0].get("note") or "").startswith("before_continue:"))


if __name__ == "__main__":
    unittest.main()
