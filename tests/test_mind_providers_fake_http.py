from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mi.providers.mind_anthropic import AnthropicMindProvider
from mi.providers.mind_errors import MindCallError
from mi.providers.mind_openai_compat import OpenAICompatibleMindProvider


_DECIDE_NEXT_OK = {
    "next_action": "stop",
    "status": "done",
    "confidence": 0.9,
    "next_hands_input": "",
    "ask_user_question": "",
    "learn_suggested": [],
    "update_project_overlay": {"set_testless_strategy": None},
    "notes": "done",
}


class TestMindProvidersFakeHttp(unittest.TestCase):
    def test_openai_compatible_provider_rejects_choices_text_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)

            def fake_http(_url: str, _body: dict, _headers: dict, _timeout_s: int) -> dict:
                # Non-chat-completions payload shape; should be rejected.
                return {"choices": [{"text": json.dumps(_DECIDE_NEXT_OK)}]}

            p = OpenAICompatibleMindProvider(
                base_url="https://example.com/v1",
                model="fake-model",
                api_key="fake-key",
                transcripts_dir=out_dir,
                timeout_s=1,
                max_retries=0,
                http_post_json=fake_http,
            )
            with self.assertRaises(MindCallError) as ctx:
                p.call(schema_filename="decide_next.json", prompt="x", tag="t")
            self.assertIn("unsupported_response_shape", str(ctx.exception))

    def test_openai_compatible_provider_rejects_responses_style_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)

            def fake_http(_url: str, _body: dict, _headers: dict, _timeout_s: int) -> dict:
                # OpenAI Responses-style shape; should be rejected.
                return {"output": [{"content": [{"text": json.dumps(_DECIDE_NEXT_OK)}]}]}

            p = OpenAICompatibleMindProvider(
                base_url="https://example.com/v1",
                model="fake-model",
                api_key="fake-key",
                transcripts_dir=out_dir,
                timeout_s=1,
                max_retries=0,
                http_post_json=fake_http,
            )
            with self.assertRaises(MindCallError) as ctx:
                p.call(schema_filename="decide_next.json", prompt="x", tag="t")
            self.assertIn("unsupported_response_shape", str(ctx.exception))

    def test_openai_compatible_provider_validates_and_writes_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)

            def fake_http(url: str, body: dict, headers: dict, timeout_s: int) -> dict:
                self.assertIn("/chat/completions", url)
                self.assertTrue(headers.get("Authorization", "").startswith("Bearer "))
                return {"choices": [{"message": {"content": json.dumps(_DECIDE_NEXT_OK)}}]}

            p = OpenAICompatibleMindProvider(
                base_url="https://example.com/v1",
                model="fake-model",
                api_key="fake-key",
                transcripts_dir=out_dir,
                timeout_s=1,
                max_retries=0,
                http_post_json=fake_http,
            )
            r = p.call(schema_filename="decide_next.json", prompt="x", tag="t")
            self.assertEqual(r.obj, _DECIDE_NEXT_OK)
            self.assertTrue(r.transcript_path.exists())

    def test_openai_compatible_provider_repairs_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            calls = {"n": 0}

            def fake_http(_url: str, _body: dict, _headers: dict, _timeout_s: int) -> dict:
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"choices": [{"message": {"content": "not json"}}]}
                return {"choices": [{"message": {"content": json.dumps(_DECIDE_NEXT_OK)}}]}

            p = OpenAICompatibleMindProvider(
                base_url="https://example.com/v1",
                model="fake-model",
                api_key="fake-key",
                transcripts_dir=out_dir,
                timeout_s=1,
                max_retries=1,
                http_post_json=fake_http,
            )
            r = p.call(schema_filename="decide_next.json", prompt="x", tag="t")
            self.assertEqual(r.obj, _DECIDE_NEXT_OK)
            self.assertEqual(calls["n"], 2)

    def test_anthropic_provider_rejects_completion_payload_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)

            def fake_http(_url: str, _body: dict, _headers: dict, _timeout_s: int) -> dict:
                # Completions-style payload is not accepted by this provider.
                return {"completion": json.dumps(_DECIDE_NEXT_OK)}

            p = AnthropicMindProvider(
                base_url="https://example.com",
                model="fake-model",
                api_key="fake-key",
                transcripts_dir=out_dir,
                timeout_s=1,
                max_retries=0,
                anthropic_version="2023-06-01",
                max_tokens=256,
                http_post_json=fake_http,
            )
            with self.assertRaises(MindCallError) as ctx:
                p.call(schema_filename="decide_next.json", prompt="x", tag="t")
            self.assertIn("unsupported_response_shape", str(ctx.exception))

    def test_anthropic_provider_validates_and_writes_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)

            def fake_http(url: str, body: dict, headers: dict, timeout_s: int) -> dict:
                self.assertIn("/v1/messages", url)
                self.assertIn("x-api-key", headers)
                self.assertIn("anthropic-version", headers)
                return {"content": [{"type": "text", "text": json.dumps(_DECIDE_NEXT_OK)}]}

            p = AnthropicMindProvider(
                base_url="https://example.com",
                model="fake-model",
                api_key="fake-key",
                transcripts_dir=out_dir,
                timeout_s=1,
                max_retries=0,
                anthropic_version="2023-06-01",
                max_tokens=256,
                http_post_json=fake_http,
            )
            r = p.call(schema_filename="decide_next.json", prompt="x", tag="t")
            self.assertEqual(r.obj, _DECIDE_NEXT_OK)
            self.assertTrue(r.transcript_path.exists())

    def test_anthropic_provider_repairs_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            calls = {"n": 0}

            def fake_http(_url: str, _body: dict, _headers: dict, _timeout_s: int) -> dict:
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"content": [{"type": "text", "text": "not json"}]}
                return {"content": [{"type": "text", "text": json.dumps(_DECIDE_NEXT_OK)}]}

            p = AnthropicMindProvider(
                base_url="https://example.com",
                model="fake-model",
                api_key="fake-key",
                transcripts_dir=out_dir,
                timeout_s=1,
                max_retries=1,
                anthropic_version="2023-06-01",
                max_tokens=256,
                http_post_json=fake_http,
            )
            r = p.call(schema_filename="decide_next.json", prompt="x", tag="t")
            self.assertEqual(r.obj, _DECIDE_NEXT_OK)
            self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
