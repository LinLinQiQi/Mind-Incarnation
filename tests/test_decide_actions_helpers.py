from __future__ import annotations

import unittest

from mi.runtime.autopilot.decide_actions import handle_decide_next_missing, route_decide_next_action


class DecideActionsHelpersTests(unittest.TestCase):
    def test_handle_missing_blocks_when_ask_disabled(self) -> None:
        cont, note = handle_decide_next_missing(
            batch_idx=1,
            decision_state="skipped",
            hands_last="",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            ask_when_uncertain=False,
            looks_like_user_question=lambda s: False,
            read_user_answer=lambda q: "ignored",
            append_user_input_record=lambda **kwargs: None,
            queue_next_input=lambda **kwargs: True,
        )
        self.assertFalse(cont)
        self.assertIn("ask_when_uncertain=false", note)

    def test_handle_missing_user_stop_sets_note(self) -> None:
        cont, note = handle_decide_next_missing(
            batch_idx=2,
            decision_state="error",
            hands_last="",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            ask_when_uncertain=True,
            looks_like_user_question=lambda s: False,
            read_user_answer=lambda q: "stop",
            append_user_input_record=lambda **kwargs: None,
            queue_next_input=lambda **kwargs: True,
        )
        self.assertFalse(cont)
        self.assertIn("stopped after mind_error(decide_next)", note)

    def test_handle_missing_user_override_queues(self) -> None:
        seen = {"queued": False}

        def _queue(**kwargs):
            seen["queued"] = True
            return True

        cont, note = handle_decide_next_missing(
            batch_idx=3,
            decision_state="skipped",
            hands_last="",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            ask_when_uncertain=True,
            looks_like_user_question=lambda s: False,
            read_user_answer=lambda q: "continue",
            append_user_input_record=lambda **kwargs: None,
            queue_next_input=_queue,
        )
        self.assertTrue(cont)
        self.assertEqual(note, "")
        self.assertTrue(seen["queued"])

    def test_route_send_to_hands_missing_input_blocks(self) -> None:
        cont, note = route_decide_next_action(
            batch_idx=4,
            next_action="send_to_hands",
            hands_last="",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            decision_obj={},
            handle_ask_user=lambda **kwargs: True,
            queue_next_input=lambda **kwargs: True,
        )
        self.assertFalse(cont)
        self.assertIn("without next_hands_input", note)

    def test_route_unknown_action_blocks(self) -> None:
        cont, note = route_decide_next_action(
            batch_idx=5,
            next_action="unknown",
            hands_last="",
            repo_obs={},
            checks_obj={},
            tdb_ctx_obj={},
            decision_obj={},
            handle_ask_user=lambda **kwargs: True,
            queue_next_input=lambda **kwargs: True,
        )
        self.assertFalse(cont)
        self.assertIn("unknown next_action", note)


if __name__ == "__main__":
    unittest.main()
