from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.providers.llm import MiLlm
from mi.providers.mind_anthropic import AnthropicMindProvider
from mi.providers.mind_openai_compat import OpenAICompatibleMindProvider
from mi.providers.provider_factory import make_hands_functions, make_mind_provider


class TestProviderFactories(unittest.TestCase):
    def test_make_mind_provider_supported_providers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcripts_dir = root / "transcripts"

            m1 = make_mind_provider({"mind": {"provider": "codex_schema"}}, project_root=root, transcripts_dir=transcripts_dir)
            self.assertIsInstance(m1, MiLlm)

            m2 = make_mind_provider(
                {
                    "mind": {
                        "provider": "openai_compatible",
                        "openai_compatible": {"model": "x", "api_key_env": "", "api_key": "k"},
                    }
                },
                project_root=root,
                transcripts_dir=transcripts_dir,
            )
            self.assertIsInstance(m2, OpenAICompatibleMindProvider)

            m3 = make_mind_provider(
                {
                    "mind": {
                        "provider": "anthropic",
                        "anthropic": {"model": "x", "api_key_env": "", "api_key": "k"},
                    }
                },
                project_root=root,
                transcripts_dir=transcripts_dir,
            )
            self.assertIsInstance(m3, AnthropicMindProvider)

    def test_make_hands_functions_cli_resume_optional(self) -> None:
        exec_fn, resume_fn = make_hands_functions(
            {"hands": {"provider": "cli", "cli": {"exec": ["echo", "hi"], "resume": []}}},
            live=False,
        )
        self.assertTrue(callable(exec_fn))
        self.assertIsNone(resume_fn)

        exec_fn2, resume_fn2 = make_hands_functions(
            {"hands": {"provider": "cli", "cli": {"exec": ["echo", "hi"], "resume": ["echo", "{thread_id}"]}}},
            live=False,
        )
        self.assertTrue(callable(exec_fn2))
        self.assertTrue(callable(resume_fn2))

    def test_make_hands_functions_codex_returns_callables(self) -> None:
        exec_fn, resume_fn = make_hands_functions({"hands": {"provider": "codex"}}, live=False)
        self.assertTrue(callable(exec_fn))
        self.assertTrue(callable(resume_fn))


if __name__ == "__main__":
    unittest.main()
