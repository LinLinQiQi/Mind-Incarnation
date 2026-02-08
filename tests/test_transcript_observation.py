import unittest


from mi.transcript import summarize_codex_events


class TestTranscriptObservation(unittest.TestCase):
    def test_summarize_counts_and_paths(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "t_123"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}},
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "echo hi",
                    "exit_code": 0,
                    "aggregated_output": "hi",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "file_patch",
                    "path": "src/app.py",
                    "diff": "--- a/src/app.py\n+++ b/src/app.py\n",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "tool_call",
                    "name": "apply_patch",
                    "arguments": {"file_path": "README.md"},
                },
            },
        ]

        obs = summarize_codex_events(events, max_paths=10, max_non_command_actions=10)
        self.assertEqual(obs["item_type_counts"]["agent_message"], 1)
        self.assertEqual(obs["item_type_counts"]["command_execution"], 1)
        self.assertEqual(obs["item_type_counts"]["file_patch"], 1)
        self.assertEqual(obs["item_type_counts"]["tool_call"], 1)

        self.assertIn("src/app.py", obs["file_paths"])
        self.assertIn("README.md", obs["file_paths"])

        actions = "\n".join(obs["non_command_actions"])
        self.assertIn("type=file_patch", actions)
        self.assertIn("type=tool_call", actions)


if __name__ == "__main__":
    unittest.main()

