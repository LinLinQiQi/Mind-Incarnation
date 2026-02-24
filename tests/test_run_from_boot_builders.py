from __future__ import annotations

import unittest
from unittest import mock

from mi.runtime.runner_state import RunnerWiringState
from mi.runtime.wiring.run_from_boot_builders import _build_cross_project_recall_writer, _build_segment_adder


class TestRunFromBootBuilders(unittest.TestCase):
    def test_cross_project_recall_writer_uses_thread_id_getter_at_call_time(self) -> None:
        thread_ids = ["t1", "t2"]

        def _get_tid() -> str:
            return thread_ids.pop(0)

        seen: list[str] = []

        with mock.patch(
            "mi.runtime.wiring.run_from_boot_builders.RF.maybe_cross_project_recall_write_through",
            autospec=True,
        ) as m:
            def _side_effect(*, thread_id: str, **_kwargs: object) -> None:
                seen.append(str(thread_id))

            m.side_effect = _side_effect

            writer = _build_cross_project_recall_writer(
                mem=mock.Mock(maybe_cross_project_recall=mock.Mock()),
                evidence_append=mock.Mock(),
                evidence_window=[],
                thread_id_getter=_get_tid,
                segment_add=lambda _x: None,
                persist_segment_state=lambda: None,
            )

            writer(batch_id="b1", reason="r1", query="q1")
            writer(batch_id="b2", reason="r2", query="q2")

        self.assertEqual(seen, ["t1", "t2"])

    def test_segment_adder_passes_expected_args(self) -> None:
        state = RunnerWiringState(thread_id="t", next_input="")
        with mock.patch("mi.runtime.wiring.run_from_boot_builders.SS.add_segment_record", autospec=True) as m:
            add = _build_segment_adder(checkpoint_enabled=True, state=state, segment_max_records=123)
            add({"kind": "x"})

            self.assertTrue(m.called)
            kwargs = m.call_args.kwargs
            self.assertEqual(kwargs.get("enabled"), True)
            self.assertIs(kwargs.get("segment_records"), state.segment_records)
            self.assertEqual(kwargs.get("segment_max_records"), 123)


if __name__ == "__main__":
    unittest.main()

