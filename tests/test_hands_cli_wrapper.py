from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.providers.codex_runner import InterruptConfig
from mi.providers.hands_cli import CliHandsAdapter
from mi.runtime.runner import run_autopilot
from mi.runtime.transcript import summarize_hands_transcript


@dataclass(frozen=True)
class _FakePromptResult:
    obj: dict
    transcript_path: Path


class _FakeLlm:
    def __init__(self, responses_by_schema: dict[str, list[dict]]):
        self._responses_by_schema = {k: list(v) for k, v in responses_by_schema.items()}
        self.calls: list[str] = []

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> _FakePromptResult:
        self.calls.append(schema_filename)
        q = self._responses_by_schema.get(schema_filename)
        if not q:
            raise AssertionError(f"FakeLlm: unexpected call schema={schema_filename}")
        obj = q.pop(0)
        return _FakePromptResult(obj=obj, transcript_path=Path("fake_mind.jsonl"))


class TestHandsCliWrapper(unittest.TestCase):
    def test_summarize_hands_transcript_extracts_paths_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td) / "t.jsonl"
            lines = [
                json.dumps({"type": "mi.transcript.header", "ts": "t", "kind": "cli.exec", "cwd": td, "argv": ["x"]}),
                json.dumps({"ts": "t", "stream": "stdout", "line": "edited src/app.py"}),
                json.dumps({"ts": "t", "stream": "stderr", "line": "Error: build failed"}),
                json.dumps({"ts": "t", "stream": "stdout", "line": "see README.md"}),
            ]
            tp.write_text("\n".join(lines) + "\n", encoding="utf-8")

            obs = summarize_hands_transcript(tp)
            self.assertEqual(obs["event_type_counts"]["stream.stdout"], 2)
            self.assertEqual(obs["event_type_counts"]["stream.stderr"], 1)
            self.assertIn("src/app.py", obs["file_paths"])
            self.assertIn("README.md", obs["file_paths"])
            self.assertTrue(any("Error:" in e for e in obs["errors"]))

    def test_summarize_hands_transcript_parses_stream_json_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td) / "t.jsonl"
            stream_lines = [
                {"type": "system", "subtype": "init", "session_id": "s123"},
                {"type": "stream_event", "session_id": "s123", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}},
                {"type": "result", "subtype": "success", "session_id": "s123", "result": "Hi", "path": "src/app.py"},
            ]
            lines = [
                json.dumps({"type": "mi.transcript.header", "ts": "t", "kind": "cli.exec", "cwd": td, "argv": ["x"]}),
            ] + [json.dumps({"ts": "t", "stream": "stdout", "line": json.dumps(ev)}) for ev in stream_lines]
            tp.write_text("\n".join(lines) + "\n", encoding="utf-8")

            obs = summarize_hands_transcript(tp)
            self.assertGreaterEqual(obs["event_type_counts"].get("event.system", 0), 1)
            self.assertGreaterEqual(obs["event_type_counts"].get("event.stream_event", 0), 1)
            self.assertGreaterEqual(obs["event_type_counts"].get("event.result", 0), 1)
            self.assertGreaterEqual(obs["item_type_counts"].get("content_block_delta", 0), 1)
            self.assertIn("src/app.py", obs.get("file_paths") or [])

    def test_cli_extracts_session_id_and_last_agent_message_from_stream_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript_path = root / "hands.jsonl"

            script = (
                "import json\n"
                "def p(o):\n"
                "  print(json.dumps(o))\n"
                "  import sys; sys.stdout.flush()\n"
                "p({'type':'system','subtype':'init','session_id':'s123'})\n"
                "p({'type':'stream_event','session_id':'s123','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'Hi'}}})\n"
                "p({'type':'stream_event','session_id':'s123','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':' there'}}})\n"
                "p({'type':'result','subtype':'success','session_id':'s123','result':'Hi there'})\n"
            )
            adapter = CliHandsAdapter(
                exec_argv=[sys.executable, "-c", script],
                resume_argv=None,
                prompt_mode="stdin",
                env=None,
                thread_id_regex="",
            )
            res = adapter.exec(
                prompt="x",
                project_root=root,
                transcript_path=transcript_path,
                full_auto=True,
                sandbox=None,
                output_schema_path=None,
                interrupt=None,
            )
            self.assertEqual(res.thread_id, "s123")
            self.assertEqual(res.last_agent_message().strip(), "Hi there")

    def test_cli_extracts_last_agent_message_from_json_output_object(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript_path = root / "hands.jsonl"

            script = (
                "import json\n"
                "print(json.dumps({'session_id':'s123','result':'Hi there'}))\n"
            )
            adapter = CliHandsAdapter(
                exec_argv=[sys.executable, "-c", script],
                resume_argv=None,
                prompt_mode="stdin",
                env=None,
                thread_id_regex="",
            )
            res = adapter.exec(
                prompt="x",
                project_root=root,
                transcript_path=transcript_path,
                full_auto=True,
                sandbox=None,
                output_schema_path=None,
                interrupt=None,
            )
            self.assertEqual(res.thread_id, "s123")
            self.assertEqual(res.last_agent_message().strip(), "Hi there")

    def test_cli_resume_formats_thread_id_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript_path = root / "hands.jsonl"

            script = "print('ok')\n"
            adapter = CliHandsAdapter(
                exec_argv=[sys.executable, "-c", script],
                resume_argv=[sys.executable, "-c", script, "--resume", "{thread_id}", "{prompt}"],
                prompt_mode="arg",
                env=None,
                thread_id_regex="",
            )
            _res = adapter.resume(
                thread_id="t123",
                prompt="hello",
                project_root=root,
                transcript_path=transcript_path,
                full_auto=True,
                sandbox=None,
                output_schema_path=None,
                interrupt=None,
            )

            header = json.loads(transcript_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(header.get("kind"), "cli.resume")
            self.assertEqual(header.get("thread_id"), "t123")
            argv = header.get("argv") or []
            self.assertIn("t123", argv)
            self.assertIn("hello", argv)
            self.assertTrue(all(x not in str(argv) for x in ("{thread_id}", "{prompt}")))

    def test_cli_arg_mode_appends_prompt_when_placeholder_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript_path = root / "hands.jsonl"

            script = "print('ok')\n"
            adapter = CliHandsAdapter(
                exec_argv=[sys.executable, "-c", script],
                resume_argv=None,
                prompt_mode="arg",
                env=None,
                thread_id_regex="",
            )
            _res = adapter.exec(
                prompt="hello",
                project_root=root,
                transcript_path=transcript_path,
                full_auto=True,
                sandbox=None,
                output_schema_path=None,
                interrupt=None,
            )
            header = json.loads(transcript_path.read_text(encoding="utf-8").splitlines()[0])
            argv = header.get("argv") or []
            self.assertEqual(argv[-1], "hello")

    def test_cli_interrupt_sends_signal_and_records_meta(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcript_path = root / "hands.jsonl"

            script = (
                "import time,sys\n"
                "print('git push origin main')\n"
                "sys.stdout.flush()\n"
                "time.sleep(2)\n"
            )
            adapter = CliHandsAdapter(
                exec_argv=[sys.executable, "-c", script],
                resume_argv=None,
                prompt_mode="stdin",
                env=None,
                thread_id_regex="",
            )
            intr = InterruptConfig(mode="on_any_external", signal_sequence=["SIGINT"], escalation_ms=[])
            _res = adapter.exec(
                prompt="x",
                project_root=root,
                transcript_path=transcript_path,
                full_auto=True,
                sandbox=None,
                output_schema_path=None,
                interrupt=intr,
            )

            sent = False
            with transcript_path.open("r", encoding="utf-8") as f:
                for row in f:
                    try:
                        rec = json.loads(row)
                    except Exception:
                        continue
                    if isinstance(rec, dict) and rec.get("stream") == "meta" and "mi.interrupt.sent=SIGINT" in str(rec.get("line") or ""):
                        sent = True
                        break
            self.assertTrue(sent)

    def test_runner_uses_cli_transcript_observation(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            root = Path(project_root)
            script = "print('edited src/app.py')\nprint('All done.')\n"
            adapter = CliHandsAdapter(
                exec_argv=[sys.executable, "-c", script],
                resume_argv=None,
                prompt_mode="stdin",
                env=None,
                thread_id_regex="",
            )
            fake_llm = _FakeLlm(
                {
                    "extract_evidence.json": [
                        {"facts": ["ok"], "actions": [], "results": ["done"], "unknowns": [], "risk_signals": []},
                    ],
                    "decide_next.json": [
                        {
                            "next_action": "stop",
                            "status": "done",
                            "confidence": 0.9,
                            "next_hands_input": "",
                            "ask_user_question": "",
                            "learned_changes": [],
                            "update_project_overlay": {"set_testless_strategy": None},
                            "notes": "done",
                        }
                    ],
                    "suggest_workflow.json": [
                        {"should_suggest": False, "suggestion": None, "notes": "skip"},
                    ],
                    "mine_preferences.json": [
                        {"suggestions": [], "notes": "skip"},
                    ],
                }
            )

            result = run_autopilot(
                task="x",
                project_root=str(root),
                home_dir=home,
                max_batches=1,
                hands_exec=adapter.exec,
                hands_resume=None,
                llm=fake_llm,
            )
            self.assertEqual(result.status, "done")

            evidence_item = None
            with open(result.evidence_log_path, "r", encoding="utf-8") as f:
                for row in f:
                    obj = json.loads(row)
                    if isinstance(obj, dict) and "facts" in obj and "results" in obj and "unknowns" in obj:
                        evidence_item = obj
                        break
            self.assertIsNotNone(evidence_item)
            obs = evidence_item["transcript_observation"]
            self.assertIn("src/app.py", obs.get("file_paths") or [])


if __name__ == "__main__":
    unittest.main()
