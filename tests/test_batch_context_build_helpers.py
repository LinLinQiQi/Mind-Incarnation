from __future__ import annotations

from pathlib import Path
import unittest

from mi.runtime.autopilot.batch_context import build_batch_execution_context


class BatchContextBuildHelpersTests(unittest.TestCase):
    def test_build_context_with_resume_enabled(self) -> None:
        ts_values = iter(
            [
                "2026-02-01T01:02:03Z",  # batch_ts source
                "2026-02-01T01:02:04Z",  # light injection as_of
                "2026-02-01T01:02:05Z",  # sent_ts
            ]
        )
        seen: dict[str, str] = {}

        def _now_ts() -> str:
            return next(ts_values)

        def _build_light(as_of_ts: str) -> str:
            seen["as_of"] = as_of_ts
            return "LIGHT"

        ctx = build_batch_execution_context(
            batch_idx=0,
            transcripts_dir=Path("/tmp/mi-transcripts"),
            next_input="  do work  ",
            thread_id="thread_1",
            hands_resume=object(),
            resumed_from_overlay=True,
            now_ts=_now_ts,
            build_light_injection_for_ts=_build_light,
        )

        self.assertEqual(ctx.batch_id, "b0")
        self.assertEqual(ctx.batch_ts, "20260201T010203Z")
        self.assertEqual(ctx.hands_transcript, Path("/tmp/mi-transcripts/hands/20260201T010203Z_b0.jsonl"))
        self.assertEqual(ctx.batch_input, "do work")
        self.assertEqual(ctx.hands_prompt, "LIGHT\ndo work\n")
        self.assertEqual(ctx.sent_ts, "2026-02-01T01:02:05Z")
        self.assertEqual(seen.get("as_of"), "2026-02-01T01:02:04Z")
        self.assertTrue(ctx.use_resume)
        self.assertTrue(ctx.attempted_overlay_resume)
        self.assertEqual(len(ctx.prompt_sha256), 64)

    def test_build_context_with_resume_disabled(self) -> None:
        ts_values = iter(
            [
                "2026-02-01T11:00:00Z",
                "2026-02-01T11:00:01Z",
                "2026-02-01T11:00:02Z",
            ]
        )

        ctx = build_batch_execution_context(
            batch_idx=2,
            transcripts_dir=Path("/tmp/mi-transcripts"),
            next_input="next step",
            thread_id="unknown",
            hands_resume=object(),
            resumed_from_overlay=True,
            now_ts=lambda: next(ts_values),
            build_light_injection_for_ts=lambda _as_of_ts: "L",
        )

        self.assertEqual(ctx.batch_id, "b2")
        self.assertEqual(ctx.batch_ts, "20260201T110000Z")
        self.assertFalse(ctx.use_resume)
        self.assertFalse(ctx.attempted_overlay_resume)


if __name__ == "__main__":
    unittest.main()
