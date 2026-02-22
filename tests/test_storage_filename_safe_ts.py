from __future__ import annotations

import unittest

from mi.core.storage import filename_safe_ts


class TestFilenameSafeTs(unittest.TestCase):
    def test_rfc3339_to_filename_safe(self) -> None:
        self.assertEqual(filename_safe_ts("2026-02-22T12:34:56Z"), "20260222T123456Z")

    def test_empty(self) -> None:
        self.assertEqual(filename_safe_ts(""), "")


if __name__ == "__main__":
    unittest.main()

