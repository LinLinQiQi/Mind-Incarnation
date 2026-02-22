from __future__ import annotations

import unittest

from mi.runtime.autopilot.windowing import trim_evidence_window


class TestWindowing(unittest.TestCase):
    def test_trim_default_keeps_last_8(self) -> None:
        win = [{"i": i} for i in range(10)]
        trim_evidence_window(win)
        self.assertEqual([x["i"] for x in win], list(range(2, 10)))

    def test_trim_preserves_list_identity(self) -> None:
        win = [{"i": i} for i in range(3)]
        before_id = id(win)
        trim_evidence_window(win)
        self.assertEqual(id(win), before_id)
        self.assertEqual([x["i"] for x in win], [0, 1, 2])

    def test_trim_custom_max_len_zero_clears(self) -> None:
        win = [{"i": 1}]
        trim_evidence_window(win, max_len=0)
        self.assertEqual(win, [])


if __name__ == "__main__":
    unittest.main()

