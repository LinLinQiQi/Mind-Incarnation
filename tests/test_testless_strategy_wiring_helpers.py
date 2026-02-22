from __future__ import annotations

import unittest

from mi.runtime.wiring.testless_strategy import (
    TestlessStrategyWiringDeps,
    mk_testless_strategy_flow_deps_wired,
)


class TestlessStrategyWiringHelpersTests(unittest.TestCase):
    def test_mk_flow_deps_reads_thread_id_getter_each_time(self) -> None:
        tid = {"v": "t1"}

        deps = TestlessStrategyWiringDeps(
            now_ts=lambda: "2026-02-01T00:00:00Z",
            thread_id_getter=lambda: tid["v"],
            evidence_append=lambda rec: rec,
            overlay={},
            find_testless_strategy_claim=lambda _ts: None,
            parse_testless_strategy_from_claim_text=lambda s: s,
            upsert_testless_strategy_claim=lambda **_kwargs: "cl_1",
            write_overlay=lambda _obj: None,
            refresh_overlay_refs=lambda: None,
        )

        d1 = mk_testless_strategy_flow_deps_wired(deps=deps)
        self.assertEqual(d1.thread_id, "t1")

        tid["v"] = "t2"
        d2 = mk_testless_strategy_flow_deps_wired(deps=deps)
        self.assertEqual(d2.thread_id, "t2")


if __name__ == "__main__":
    unittest.main()

