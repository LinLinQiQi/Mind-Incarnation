import unittest

from mi.redact import redact_text


class TestRedact(unittest.TestCase):
    def test_redacts_generic_key_value(self) -> None:
        s = "api_key=sk-12345 token: abc password=secret"
        out = redact_text(s)
        self.assertNotIn("sk-12345", out)
        self.assertNotIn("abc", out)
        self.assertNotIn("secret", out)

    def test_redacts_authorization_header(self) -> None:
        s = "Authorization: Bearer abc.def.ghi"
        out = redact_text(s)
        self.assertIn("Authorization: Bearer", out)
        self.assertNotIn("abc.def.ghi", out)

    def test_redacts_known_token_formats(self) -> None:
        s = "ghp_0123456789abcdef0123456789abcdef0123"
        out = redact_text(s)
        self.assertNotIn("ghp_", out)


if __name__ == "__main__":
    unittest.main()

