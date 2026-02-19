from __future__ import annotations

import unittest

from mi.cli import build_parser


class TestCliRunTaskParsing(unittest.TestCase):
    def test_run_task_accepts_multiple_words_without_quotes(self) -> None:
        parser = build_parser()
        ns = parser.parse_args(["run", "fix", "failing", "tests"])
        self.assertEqual(ns.task, ["fix", "failing", "tests"])

    def test_run_task_accepts_quoted_task_string(self) -> None:
        parser = build_parser()
        ns = parser.parse_args(["run", "fix failing tests"])
        self.assertEqual(ns.task, ["fix failing tests"])

    def test_run_task_allows_options_after_task_words(self) -> None:
        parser = build_parser()
        ns = parser.parse_args(["run", "fix", "failing", "tests", "--max-batches", "3"])
        self.assertEqual(ns.task, ["fix", "failing", "tests"])
        self.assertEqual(ns.max_batches, 3)


if __name__ == "__main__":
    unittest.main()

