from __future__ import annotations

import unittest

from mi.runtime.live import render_codex_event


class TestLiveRenderCodex(unittest.TestCase):
    def test_render_thread_started(self) -> None:
        lines = render_codex_event({"type": "thread.started", "thread_id": "t_123"})
        self.assertTrue(lines)
        self.assertIn("thread_id=t_123", lines[0])

    def test_render_command_started_and_completed(self) -> None:
        started = render_codex_event({"type": "item.started", "item": {"type": "command_execution", "command": "echo hi"}})
        self.assertEqual(started, ["$ echo hi"])

        completed = render_codex_event(
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "echo hi", "exit_code": 0, "aggregated_output": "hi\n"},
            }
        )
        self.assertTrue(completed)
        self.assertIn("exit_code=0", completed[0])
        self.assertIn("echo hi", completed[0])
        self.assertIn("hi", "\n".join(completed))

    def test_render_agent_message(self) -> None:
        msg = render_codex_event({"type": "item.completed", "item": {"type": "agent_message", "text": "Hello\nWorld"}})
        self.assertEqual(msg, ["Hello", "World"])

    def test_unknown_events_are_ignored(self) -> None:
        self.assertEqual(render_codex_event({"type": "unknown"}), [])


if __name__ == "__main__":
    unittest.main()
