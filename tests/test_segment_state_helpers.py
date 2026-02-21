from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mi.runtime.autopilot.segment_state import (
    add_segment_record,
    clear_segment_state,
    load_segment_state,
    new_segment_state,
    persist_segment_state,
)


class SegmentStateHelpersTests(unittest.TestCase):
    def test_new_segment_state_sets_defaults_and_truncates_task(self) -> None:
        state = new_segment_state(
            reason="run_start",
            thread_hint="tid_1",
            task="x" * 250,
            now_ts=lambda: "2026-01-01T00:00:00Z",
            truncate=lambda s, n: s[:n],
            id_factory=lambda: "seg_demo",
        )
        self.assertEqual(state["segment_id"], "seg_demo")
        self.assertEqual(state["thread_id"], "tid_1")
        self.assertEqual(state["reason"], "run_start")
        self.assertEqual(state["created_ts"], "2026-01-01T00:00:00Z")
        self.assertEqual(len(state["task_hint"]), 200)
        self.assertEqual(state["records"], [])

    def test_load_segment_state_checks_open_version_and_thread(self) -> None:
        warnings: list[dict[str, object]] = []
        obj = {
            "version": "v1",
            "open": True,
            "thread_id": "tid_a",
            "records": [{"kind": "evidence"}],
        }

        out = load_segment_state(
            path=Path("unused"),
            read_json_best_effort=lambda *args, **kwargs: obj,
            state_warnings=warnings,
            thread_hint="tid_a",
        )
        self.assertIsInstance(out, dict)

        out2 = load_segment_state(
            path=Path("unused"),
            read_json_best_effort=lambda *args, **kwargs: obj,
            state_warnings=warnings,
            thread_hint="tid_b",
        )
        self.assertIsNone(out2)

    def test_persist_and_clear_segment_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "segment_state.json"
            state = {
                "version": "v1",
                "open": True,
                "records": [{"idx": i} for i in range(10)],
            }

            def _write_json(p: Path, obj: object) -> None:
                p.write_text(json.dumps(obj), encoding="utf-8")

            persist_segment_state(
                enabled=True,
                path=path,
                segment_state=state,
                segment_max_records=4,
                now_ts=lambda: "2026-02-21T00:00:00Z",
                write_json_atomic=_write_json,
            )
            self.assertTrue(path.exists())
            obj = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(obj["updated_ts"], "2026-02-21T00:00:00Z")
            self.assertEqual(len(obj["records"]), 4)
            self.assertEqual(obj["records"][0]["idx"], 6)

            clear_segment_state(path=path)
            self.assertFalse(path.exists())

    def test_add_segment_record_compacts_and_bounds(self) -> None:
        recs: list[dict[str, object]] = []
        add_segment_record(
            enabled=True,
            obj={
                "kind": "evidence",
                "batch_id": "b1",
                "event_id": "ev_1",
                "facts": ["a", "b"],
                "actions": ["c"],
                "results": [],
                "unknowns": [],
                "risk_signals": ["net"],
                "repo_observation": {"has_tests": True, "git_head": "main"},
                "transcript_observation": {"file_paths": ["a.py"], "errors": ["e1"]},
            },
            segment_records=recs,
            segment_max_records=2,
            truncate=lambda s, n: s[:n],
        )
        add_segment_record(
            enabled=True,
            obj={"kind": "risk_event", "category": "network", "severity": "high", "risk_signals": ["http"]},
            segment_records=recs,
            segment_max_records=2,
            truncate=lambda s, n: s[:n],
        )
        add_segment_record(
            enabled=True,
            obj={"kind": "snapshot", "checkpoint_kind": "segment", "status_hint": "ok", "tags": ["t1", "t2"]},
            segment_records=recs,
            segment_max_records=2,
            truncate=lambda s, n: s[:n],
        )
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["kind"], "risk_event")
        self.assertEqual(recs[1]["kind"], "snapshot")


if __name__ == "__main__":
    unittest.main()
