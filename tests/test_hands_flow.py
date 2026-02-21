from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mi.runtime.autopilot import BatchExecutionContext, HandsFlowDeps, RunDeps, RunState, run_hands_batch


@dataclass
class _Result:
    thread_id: str
    exit_code: int


class TestHandsFlow(unittest.TestCase):
    def test_exec_path_updates_thread_and_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcripts_dir = root / "transcripts"
            (transcripts_dir / "hands").mkdir(parents=True, exist_ok=True)

            events: list[dict[str, Any]] = []
            emits: list[tuple[str, str]] = []
            overlay: dict[str, Any] = {"hands_state": {}}
            overlay_writes: list[dict[str, Any]] = []

            def _append(ev: dict[str, Any]) -> dict[str, Any]:
                events.append(ev)
                return ev

            def _emit(prefix: str, text: str) -> None:
                emits.append((prefix, text))

            def _exec(**kwargs: Any) -> _Result:
                return _Result(thread_id="t_exec_1", exit_code=0)

            def _write_overlay(ov: dict[str, Any]) -> None:
                overlay_writes.append(dict(ov))

            ctx = BatchExecutionContext(
                batch_idx=0,
                batch_id="b0",
                batch_ts="20260101T000000Z",
                hands_transcript=transcripts_dir / "hands" / "b0.jsonl",
                batch_input="do work",
                hands_prompt="light\n\ndo work\n",
                light_injection="light",
                sent_ts="2026-01-01T00:00:00Z",
                prompt_sha256="sha",
                use_resume=False,
                attempted_overlay_resume=False,
            )

            result, st = run_hands_batch(
                ctx=ctx,
                state=RunState(thread_id=None, executed_batches=0),
                deps=HandsFlowDeps(
                    run_deps=RunDeps(emit_prefixed=_emit, now_ts=lambda: "2026-01-01T00:00:01Z", evidence_append=_append),
                    project_root=root,
                    transcripts_dir=transcripts_dir,
                    cur_provider="codex",
                    no_mi_prompt=False,
                    interrupt_cfg=None,
                    overlay=overlay,
                    hands_exec=_exec,
                    hands_resume=None,
                    write_overlay=_write_overlay,
                ),
            )

            self.assertEqual(result.thread_id, "t_exec_1")
            self.assertEqual(st.thread_id, "t_exec_1")
            self.assertEqual(st.executed_batches, 1)
            self.assertTrue(any(e.get("kind") == "hands_input" for e in events))
            self.assertTrue(any("[mi]" == p and "hands_done b0" in t for p, t in emits))
            hs = overlay.get("hands_state") if isinstance(overlay.get("hands_state"), dict) else {}
            self.assertEqual(hs.get("provider"), "codex")
            self.assertEqual(hs.get("thread_id"), "t_exec_1")
            self.assertTrue(overlay_writes)

    def test_resume_failure_falls_back_to_exec(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            transcripts_dir = root / "transcripts"
            (transcripts_dir / "hands").mkdir(parents=True, exist_ok=True)

            events: list[dict[str, Any]] = []
            overlay: dict[str, Any] = {"hands_state": {}}
            resume_calls: list[dict[str, Any]] = []
            exec_calls: list[dict[str, Any]] = []

            def _append(ev: dict[str, Any]) -> dict[str, Any]:
                events.append(ev)
                return ev

            def _emit(_prefix: str, _text: str) -> None:
                return None

            def _resume(**kwargs: Any) -> _Result:
                resume_calls.append(kwargs)
                return _Result(thread_id="unknown", exit_code=1)

            def _exec(**kwargs: Any) -> _Result:
                exec_calls.append(kwargs)
                return _Result(thread_id="t_exec_2", exit_code=0)

            ctx = BatchExecutionContext(
                batch_idx=2,
                batch_id="b2",
                batch_ts="20260101T010203Z",
                hands_transcript=transcripts_dir / "hands" / "b2.jsonl",
                batch_input="continue",
                hands_prompt="light\n\ncontinue\n",
                light_injection="light",
                sent_ts="2026-01-01T01:02:03Z",
                prompt_sha256="sha2",
                use_resume=True,
                attempted_overlay_resume=True,
            )

            result, st = run_hands_batch(
                ctx=ctx,
                state=RunState(thread_id="t_old", executed_batches=4),
                deps=HandsFlowDeps(
                    run_deps=RunDeps(emit_prefixed=_emit, now_ts=lambda: "2026-01-01T01:02:04Z", evidence_append=_append),
                    project_root=root,
                    transcripts_dir=transcripts_dir,
                    cur_provider="codex",
                    no_mi_prompt=True,
                    interrupt_cfg=None,
                    overlay=overlay,
                    hands_exec=_exec,
                    hands_resume=_resume,
                    write_overlay=lambda _ov: None,
                ),
            )

            self.assertEqual(len(resume_calls), 1)
            self.assertEqual(resume_calls[0].get("thread_id"), "t_old")
            self.assertEqual(len(exec_calls), 1)
            self.assertEqual(result.thread_id, "t_exec_2")
            self.assertEqual(st.thread_id, "t_exec_2")
            self.assertEqual(st.executed_batches, 5)
            self.assertTrue(str(ctx.hands_transcript).endswith("_exec_after_resume_fail.jsonl"))
            self.assertTrue(any(e.get("kind") == "hands_resume_failed" for e in events))
            self.assertTrue(any(e.get("kind") == "hands_input" for e in events))


if __name__ == "__main__":
    unittest.main()
