import unittest

from mi.llm import _extract_json


class TestExtractJson(unittest.TestCase):
    def test_exact_json(self) -> None:
        self.assertEqual(_extract_json('{"ok": true}'), {"ok": True})

    def test_wrapped_json(self) -> None:
        txt = "prefix\n{\"a\": 1}\nsuffix"
        self.assertEqual(_extract_json(txt), {"a": 1})

    def test_no_json_raises(self) -> None:
        with self.assertRaises(ValueError):
            _extract_json("no json here")


if __name__ == "__main__":
    unittest.main()

